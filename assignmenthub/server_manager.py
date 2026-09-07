"""Double-click local server manager. Slow operations never run on Tk's UI thread."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import queue
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from assignmenthub.config import Config
from assignmenthub.desktop_style import ScrollFrame, apply_theme
from assignmenthub.launcher import PROJECT_ROOT, start, status, stop
from assignmenthub.management import Catalog, diagnose, gib_bytes, gib_text, network_addresses, state_label


def open_local(path: Path):
    if not path.exists():
        raise ValueError("아직 생성되지 않은 폴더입니다. 과정을 만들거나 서버를 시작한 뒤 다시 열어 주세요.")
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class CourseDialog(tk.Toplevel):
    def __init__(self, manager, path: Path | None = None):
        super().__init__(manager.root)
        self.manager, self.path = manager, path
        self.config = Config.load(path) if path else None
        self.title("과정 설정" if path else "새 과정 만들기")
        self.configure(background="#f7f8fc")
        self.geometry("740x760")
        self.minsize(540, 480)
        self.transient(manager.root)
        self.grab_set()
        self.busy = False
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.variables = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        ttk.Label(self, text="과정과 접속 정보를 입력하세요", style="Heading.TLabel").grid(row=0, column=0, sticky="w", padx=24, pady=(22, 10))
        tabs = ttk.Notebook(self)
        tabs.grid(row=1, column=0, sticky="nsew", padx=24)
        basic_view, limits_view = ScrollFrame(tabs, padding=16), ScrollFrame(tabs, padding=16)
        basic, limits = basic_view.content, limits_view.content
        tabs.add(basic_view, text="기본 정보")
        tabs.add(limits_view, text="용량·전송 설정")
        self.views = (basic_view, limits_view)
        for frame in (basic, limits):
            frame.columnconfigure(1, weight=1)
        identity, port = (self.config.instance_id, self.config.port) if path else manager.catalog.suggest()
        addresses = manager.addresses
        host = self.config.public_host if path else (addresses[0][0] if addresses else "127.0.0.1")

        def field(frame, row, key, label, value, *, secret=False, readonly=False, choices=None):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=8)
            variable = tk.StringVar(value=str(value))
            self.variables[key] = variable
            widget = ttk.Combobox(frame, textvariable=variable, values=choices) if choices is not None else ttk.Entry(frame, textvariable=variable, show="●" if secret else "")
            if readonly:
                widget.configure(state="readonly")
            widget.grid(row=row, column=1, sticky="ew", pady=8)
            return widget

        self.name_entry = field(basic, 0, "course_name", "화면에 표시할 과정명", self.config.course_name if path else "")
        field(basic, 1, "instance_id", "과정 ID", identity, readonly=True)
        field(basic, 2, "public_host", "수강생에게 안내할 IP", host, choices=[ip for ip, _ in addresses] + ["127.0.0.1"])
        interface_text = " / ".join(f"{name}: {ip}" for ip, name in addresses) or "네트워크 주소를 찾지 못했습니다. 127.0.0.1은 이 PC에서만 접속할 수 있습니다."
        ttk.Label(basic, text=interface_text, wraplength=560, style="Muted.TLabel").grid(row=3, column=0, columnspan=2, sticky="w")
        field(basic, 4, "port", "접속 포트", port)
        field(basic, 5, "storage_root", "파일 저장 폴더", self.config.root if path else manager.data_directory / identity, readonly=bool(path))
        if not path:
            ttk.Button(basic, text="저장 폴더 선택…", command=self.choose_folder).grid(row=6, column=1, sticky="e")
            field(basic, 7, "admin_id", "관리자 ID", "admin")
            self.password = field(basic, 8, "password", "관리자 비밀번호", "", secret=True)
            self.confirm = field(basic, 9, "confirm", "비밀번호 확인", "", secret=True)
            self.show_password = tk.BooleanVar()
            ttk.Checkbutton(basic, text="비밀번호 표시 (12~128자)", variable=self.show_password,
                            command=lambda: [w.configure(show="" if self.show_password.get() else "●") for w in (self.password, self.confirm)]).grid(row=10, column=1, sticky="w")
        else:
            ttk.Label(basic, text="저장 폴더와 과정 ID는 유지됩니다. 접속 주소를 바꾸면 수강생에게 새 주소를 안내하세요.", wraplength=560, style="Muted.TLabel").grid(row=6, column=0, columnspan=2, sticky="w", pady=12)
        defaults = self.config or Config(identity, "새 과정", port, str(manager.data_directory / identity))
        for row, (key, label) in enumerate((("max_file_bytes", "파일당 최대 크기 (GiB)"), ("user_quota_bytes", "사용자별 누적 한도 (GiB)"), ("min_free_bytes", "디스크 최소 여유 (GiB)"), ("max_files", "한 제출의 최대 파일 수"), ("concurrent_uploads", "동시 전송 수"), ("upload_ttl_hours", "미완료 보관 시간 (시간)"))):
            value = getattr(defaults, key)
            field(limits, row, key, label, gib_text(value) if key.endswith("_bytes") else value)
        ttk.Label(limits, text="기본 설정으로 바로 사용할 수 있습니다.\n1 GiB = 1,073,741,824바이트\n이전 제출 버전도 사용자 한도에 포함하며 완료 파일은 자동 삭제하지 않습니다.", wraplength=560, style="Muted.TLabel").grid(row=7, column=0, columnspan=2, sticky="w", pady=20)
        self.error = tk.StringVar()
        ttk.Label(self, textvariable=self.error, foreground="#a32c28", wraplength=640).grid(row=2, column=0, sticky="ew", padx=24, pady=8)
        buttons = ttk.Frame(self)
        buttons.grid(row=3, column=0, sticky="e", padx=24, pady=(0, 20))
        self.save_button = ttk.Button(buttons, text="설정 저장" if path else "과정 만들기", style="Primary.TButton", command=self.save)
        self.save_button.pack(side="right", padx=(8, 0))
        self.cancel_button = ttk.Button(buttons, text="취소", command=self.close)
        self.cancel_button.pack(side="right")
        self.name_entry.focus_set()
        self.bind("<Escape>", lambda _: self.close())
        for frame in (basic, limits):
            frame.bind("<Configure>", self.wrap_labels, add="+")

    def wrap_labels(self, event):
        for label in event.widget.winfo_children():
            if isinstance(label, ttk.Label) and int(label.grid_info().get("columnspan", 1)) > 1:
                label.configure(wraplength=max(160, event.width - 40))

    def choose_folder(self):
        value = filedialog.askdirectory(parent=self, title="새 과정에 사용할 비어 있는 폴더")
        if value:
            self.variables["storage_root"].set(value)

    def close(self):
        if not self.busy:
            self.destroy()

    def save(self):
        try:
            values = {key: value.get().strip() for key, value in self.variables.items() if key not in ("password", "confirm")}
            changes = {"course_name": values["course_name"], "public_host": values["public_host"], "port": int(values["port"]),
                       "max_file_bytes": gib_bytes(values["max_file_bytes"], "파일당 최대 크기"),
                       "user_quota_bytes": gib_bytes(values["user_quota_bytes"], "사용자별 누적 한도"),
                       "min_free_bytes": gib_bytes(values["min_free_bytes"], "디스크 최소 여유", True),
                       "max_files": int(values["max_files"]), "concurrent_uploads": int(values["concurrent_uploads"]),
                       "upload_ttl_hours": float(values["upload_ttl_hours"])}
            if self.path:
                task = lambda: self.manager.catalog.update(self.path, **changes)
            else:
                config = Config(instance_id=values["instance_id"], storage_root=values["storage_root"], **changes)
                password, confirm = self.variables["password"].get(), self.variables["confirm"].get()
                if not values["admin_id"] or password != confirm or not 12 <= len(password) <= 128:
                    raise ValueError("관리자 ID와 12~128자 비밀번호를 입력하고 비밀번호 확인을 일치시켜 주세요.")
                task = lambda: self.manager.catalog.create(config, values["admin_id"], password, confirm)
            self.busy = True
            self.error.set("저장하고 있습니다…")
            self.save_button.configure(state="disabled")
            self.cancel_button.configure(state="disabled")
            self.manager.submit("설정 저장", task, self.saved, self.failed)
        except (ValueError, KeyError) as exc:
            self.error.set(str(exc) if not str(exc).startswith("invalid literal") else "포트, 파일 수와 동시 전송 수에는 정수를 입력하세요.")

    def failed(self, error):
        self.busy = False
        self.error.set(str(getattr(error, "detail", error)))
        self.save_button.configure(state="normal")
        self.cancel_button.configure(state="normal")

    def saved(self, result):
        self.manager.selected_path = self.path or result
        self.manager.notice.set("설정을 저장했습니다. 서버 시작을 누르면 수강생이 접속할 수 있습니다.")
        for key in ("password", "confirm"):
            if key in self.variables:
                self.variables[key].set("")
        self.destroy()
        self.manager.refresh()


class ServerManager:
    def __init__(self, root: tk.Tk, directory: Path | None = None, data_directory: Path | None = None):
        self.root = root
        self.catalog = Catalog(directory or PROJECT_ROOT / "instances")
        self.data_directory = data_directory or PROJECT_ROOT / "data"
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="manager")
        self.events = queue.Queue()
        self.jobs = {}
        self.busy_paths = set()
        self.courses, self.states = {}, {}
        self.selected_path = None
        self.refreshing = False
        self.refresh_failed = False
        self.addresses = []
        self.root.title("AssignmentHub · 서버 관리")
        self.root.geometry("1100x760")
        self.root.minsize(640, 520)
        apply_theme(root)
        self.viewport = ScrollFrame(root, padding=24)
        self.viewport.pack(fill="both", expand=True)
        outer = self.viewport.content
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        heading = ttk.Frame(outer)
        heading.grid(row=0, column=0, sticky="ew")
        ttk.Label(heading, text="AH  /  AssignmentHub", style="Brand.TLabel").pack(anchor="w", pady=(0, 14))
        ttk.Label(heading, text="수업 서버", style="Heading.TLabel").pack(anchor="w")
        self.subtitle = ttk.Label(outer, text="과정을 선택하고 서버를 시작하세요. 접속 주소로 수강생을 초대할 수 있습니다.", style="Muted.TLabel")
        self.subtitle.grid(row=1, column=0, sticky="ew", pady=(6, 22))
        toolbar = ttk.Frame(outer)
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self.new_button = ttk.Button(toolbar, text="+ 새 과정 만들기", style="Primary.TButton", command=lambda: self.guarded(lambda: CourseDialog(self)))
        self.toolbar_buttons = [self.new_button, ttk.Button(toolbar, text="기존 설정 추가…", command=self.import_config),
                                ttk.Button(toolbar, text="새로고침", command=self.refresh), ttk.Button(toolbar, text="사용 방법", command=self.help)]
        table = ttk.Frame(outer)
        table.grid(row=3, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("name", "state", "address"), show="headings", selectmode="browse", height=4)
        for key, label, width in (("name", "과정", 285), ("state", "서버 상태", 120), ("address", "수강생 접속 주소", 410)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=100, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal.set)
        self.empty_state = tk.Frame(self.tree, background="white")
        tk.Label(self.empty_state, text="아직 등록된 과정이 없습니다", background="white", foreground="#414c63",
                 font=("Malgun Gothic", 12, "bold")).pack(pady=(0, 6))
        tk.Label(self.empty_state, text="새 과정 만들기로 첫 수업을 준비하세요.", background="white", foreground="#647087",
                 font=("Malgun Gothic", 10)).pack()
        self.tree.tag_configure("error", foreground="#a32c28")
        self.tree.tag_configure("running", foreground="#17684f")
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.selection())
        self.tree.bind("<Double-1>", lambda _: self.open_browser())
        self.summary = tk.StringVar(value="처음 사용하는 경우 '+ 새 과정 만들기'를 누르세요.")
        self.summary_label = ttk.Label(outer, textvariable=self.summary, wraplength=1010)
        self.summary_label.grid(row=4, column=0, sticky="ew", pady=(16, 10))
        address = ttk.Frame(outer)
        address.grid(row=5, column=0, sticky="ew")
        address.columnconfigure(0, weight=1)
        self.url = tk.StringVar()
        self.address_entry = ttk.Entry(address, textvariable=self.url, state="readonly", font=("Segoe UI", 11))
        self.copy_button = ttk.Button(address, text="주소 복사", command=self.copy_url)
        self.browser_button = ttk.Button(address, text="관리자 화면 열기 ↗", command=self.open_browser)
        actions = ttk.Frame(outer)
        actions.grid(row=6, column=0, sticky="ew", pady=14)
        self.buttons = {}
        for key, label, callback in (("start", "▶ 서버 시작", lambda: self.operate("start")), ("stop", "■ 서버 중지", lambda: self.operate("stop")), ("settings", "과정 설정", self.settings), ("diagnose", "접속·오류 점검", self.inspect), ("storage", "저장 폴더", lambda: self.folder(False)), ("logs", "로그 폴더", lambda: self.folder(True))):
            button = ttk.Button(actions, text=label, command=callback, **({"style": "Primary.TButton"} if key == "start" else {}))
            self.buttons[key] = button
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.grid(row=7, column=0, sticky="ew", pady=(0, 8))
        self.notice = tk.StringVar(value="과정 목록을 확인하고 있습니다…")
        self.notice_label = ttk.Label(outer, textvariable=self.notice, wraplength=1010, style="Muted.TLabel")
        self.notice_label.grid(row=8, column=0, sticky="ew")
        self.footer = ttk.Label(outer, text="관리창을 닫아도 서버는 유지됩니다. 수업이 끝나면 '서버 중지'를 눌러 주세요.", style="Muted.TLabel")
        self.footer.grid(row=9, column=0, sticky="ew", pady=(16, 0))
        self._compact = None
        outer.bind("<Configure>", self.reflow, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self.poll)
        self.root.after(5000, self.auto_refresh)
        self.submit("네트워크 주소 확인", network_addresses, lambda result: setattr(self, "addresses", result))
        self.refresh()

    def reflow(self, event):
        width = max(200, event.width - 48)
        for label in (self.subtitle, self.summary_label, self.notice_label, self.footer):
            label.configure(wraplength=width)
        self.viewport.layout()
        compact = width < 930
        if compact == self._compact:
            return
        self._compact = compact
        columns = 3 if compact else 6
        for i, button in enumerate(self.buttons.values()):
            button.grid(row=i // columns, column=i % columns, sticky="ew", padx=(0, 8), pady=(0, 8))
        for i in range(6):
            button.master.columnconfigure(i, weight=1 if i < columns else 0)
        for i, button in enumerate(self.toolbar_buttons):
            button.grid(row=i // (2 if compact else 4), column=i % (2 if compact else 4), sticky="ew", padx=(0, 8), pady=(0, 8))
        for i in range(4):
            button.master.columnconfigure(i, weight=1 if i < (2 if compact else 4) else 0)
        self.address_entry.grid(row=0, column=0, columnspan=3 if compact else 1, sticky="ew")
        self.copy_button.grid(row=1 if compact else 0, column=1, sticky="ew", padx=(8, 0), pady=(8, 0) if compact else 0)
        self.browser_button.grid(row=1 if compact else 0, column=2, sticky="ew", padx=(8, 0), pady=(8, 0) if compact else 0)

    def guarded(self, callback):
        try:
            return callback()
        except Exception as error:
            self.show_error(error)

    def show_error(self, error):
        text = str(getattr(error, "detail", error))
        self.notice.set(text)
        messagebox.showerror("작업을 완료하지 못했습니다", text, parent=self.root)

    def submit(self, label, task, success=lambda _: None, failure=None):
        job_id = object()
        self.jobs[job_id] = (label, time.monotonic())
        self.progress.start(12)
        future = self.executor.submit(task)
        future.add_done_callback(lambda result: self.events.put((job_id, result, success, failure or self.show_error)))

    def poll(self):
        while True:
            try:
                job_id, future, success, failure = self.events.get_nowait()
            except queue.Empty:
                break
            self.jobs.pop(job_id, None)
            try:
                result = future.result()
            except Exception as error:
                failure(error)
            else:
                try:
                    success(result)
                except Exception as error:
                    self.show_error(error)
        if self.jobs:
            label, since = next(iter(self.jobs.values()))
            self.notice.set(f"{label}… {int(time.monotonic() - since)}초 경과 · 다른 과정을 선택할 수 있습니다.")
        else:
            self.progress.stop()
        self.root.after(100, self.poll)

    def refresh(self):
        if self.refreshing:
            return
        self.refreshing = True
        def read():
            courses = self.catalog.courses()
            return courses, {str(c.path): status(c.config) for c in courses if c.config}
        def failed(error):
            self.refreshing = False
            self.refresh_failed = True
            self.show_error(error)
        self.submit("상태 확인", read, self.render, failed)

    def render(self, result):
        courses, self.states = result
        self.refreshing = False
        self.refresh_failed = False
        selected = str(self.selected_path) if self.selected_path else next(iter(self.tree.selection()), None)
        self.courses = {str(c.path): c for c in courses}
        if courses:
            self.empty_state.place_forget()
        else:
            self.empty_state.place(relx=.5, rely=.58, anchor="center")
        self.tree.delete(*self.tree.get_children())
        for key, course in self.courses.items():
            config = course.config
            label = "설정 오류" if not config else ("작업 중" if key in self.busy_paths else state_label(self.states.get(key, {})))
            self.tree.insert("", "end", iid=key, values=(config.course_name if config else course.path.name, label, config.public_url if config else course.error), tags=("running" if label == "실행 중" else "error" if label in ("설정 오류", "점검 필요") else "",))
        if not selected or selected not in self.courses:
            selected = next(iter(self.courses), None)
        if selected:
            self.tree.selection_set(selected)
            self.tree.see(selected)
        self.selection()
        if self.notice.get() == "과정 목록을 확인하고 있습니다…" or self.notice.get().startswith("상태 확인"):
            active = sum(bool(value.get("running")) for value in self.states.values())
            self.notice.set(f"전체 {len(courses)}개 과정 · 실행 중 {active}개 · 상태 확인 {time.strftime('%H:%M:%S')}" if courses else "1. 새 과정 만들기   2. 서버 시작   3. 접속 주소 복사 후 수강생에게 안내")

    def auto_refresh(self):
        if not self.refresh_failed:
            self.refresh()
        self.root.after(5000, self.auto_refresh)

    def selected(self):
        keys = self.tree.selection()
        return self.courses.get(keys[0]) if keys else None

    def selection(self):
        course = self.selected()
        config = course.config if course else None
        key = str(course.path) if course else ""
        self.selected_path = course.path if course else None
        current = self.states.get(key, {})
        active = current.get("running", False)
        working = key in self.busy_paths or state_label(current) == "시작 중"
        self.url.set(config.public_url if config else "")
        self.summary.set((f"{config.course_name} · {config.instance_id}\n저장: {config.root}" + (f"\n최근 오류: {current['error']}" if current.get("error") else "")) if config else (f"설정 파일을 확인하세요: {course.path}\n{course.error}" if course else "처음 사용하는 경우 '+ 새 과정 만들기'를 누르세요."))
        for name, button in self.buttons.items():
            enabled = bool(config) and not working
            if name in ("start", "settings"):
                enabled = enabled and not active
            if name == "stop":
                enabled = enabled and (active or state_label(current) == "점검 필요")
            button.configure(state="normal" if enabled else "disabled")
        self.copy_button.configure(state="normal" if config else "disabled")
        self.browser_button.configure(state="normal" if config and active else "disabled")

    def operate(self, action):
        course = self.selected()
        if not course or not course.config or str(course.path) in self.busy_paths:
            return
        config, key = course.config, str(course.path)
        if action == "stop" and not messagebox.askyesno("이 과정의 서버를 중지할까요?", f"{config.course_name}\n수강생 접속과 전송이 중단됩니다. 완료 제출은 보존되며 미완료 작업은 재시작 후 이어 올릴 수 있습니다.", parent=self.root):
            return
        self.busy_paths.add(key)
        self.selection()
        def done(result=None, error=None):
            self.busy_paths.discard(key)
            if error:
                self.show_error(error)
            else:
                self.notice.set(f"{config.course_name}: " + ("서버가 준비됐습니다. 관리자 화면을 열거나 주소를 복사하세요." if action == "start" else "서버를 중지했습니다. 설정과 저장 폴더 전체를 백업할 수 있습니다."))
            self.refresh()
        self.submit(f"{config.course_name} {'시작' if action == 'start' else '중지'}", lambda: start(course.path) if action == "start" else stop(config), done, lambda error: done(error=error))

    def settings(self):
        course = self.selected()
        if course and course.config:
            self.guarded(lambda: CourseDialog(self, course.path))

    def import_config(self):
        value = filedialog.askopenfilename(parent=self.root, title="기존 과정의 설정 JSON 선택", filetypes=[("과정 설정", "*.json")])
        if value:
            def imported(_):
                self.selected_path = Path(value).resolve()
                self.notice.set("기존 과정을 연결했습니다. 계정과 제출 파일은 기존 저장 폴더를 사용합니다.")
                self.refresh()
            self.submit("기존 과정 연결", lambda: self.catalog.add(Path(value)), imported)

    def copy_url(self):
        if self.url.get():
            self.root.clipboard_clear()
            self.root.clipboard_append(self.url.get())
            course = self.selected()
            local_only = course and course.config.public_host in ("127.0.0.1", "localhost", "::1")
            self.notice.set("주소를 복사했습니다. 현재 주소는 이 PC에서만 접속 가능합니다. 다른 PC에 안내하려면 과정 설정에서 네트워크 IP를 선택하세요." if local_only else "접속 주소를 복사했습니다. 수강생 PC에서 열 수 있는지 확인한 뒤 안내하세요.")

    def open_browser(self):
        course = self.selected()
        if course and course.config and self.states.get(str(course.path), {}).get("running"):
            self.guarded(lambda: webbrowser.open(course.config.public_url))

    def folder(self, logs):
        course = self.selected()
        if course and course.config:
            self.guarded(lambda: open_local(course.config.root / "logs" if logs else course.config.root))

    def inspect(self):
        course = self.selected()
        if course and course.config:
            self.submit("접속·오류 점검", lambda: diagnose(course.config), lambda result: self.text_window("접속·오류 점검", "\n\n".join(f"{name} — {result}\n{detail}" for name, result, detail in result)))

    def text_window(self, title, text):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.configure(background="#f7f8fc")
        window.geometry("800x550")
        window.minsize(440, 320)
        ttk.Label(window, text=title, style="Heading.TLabel").pack(anchor="w", padx=24, pady=20)
        content = ttk.Frame(window)
        content.pack(fill="both", expand=True, padx=24)
        area = tk.Text(content, wrap="word", padx=20, pady=20, font=("Malgun Gothic", 11),
                       background="white", foreground="#202539", relief="flat", spacing3=8)
        scroll = ttk.Scrollbar(content, command=area.yview)
        scroll.pack(side="right", fill="y")
        area.configure(yscrollcommand=scroll.set)
        area.insert("1.0", text)
        area.configure(state="disabled")
        area.pack(fill="both", expand=True)
        ttk.Button(window, text="닫기", command=window.destroy).pack(side="bottom", before=content, pady=12)

    def help(self):
        self.text_window("서버 사용 방법", "처음 사용하는 경우\n1. 새 과정 만들기에서 과정명과 관리자 비밀번호를 입력합니다.\n2. 수강생에게 안내할 IP를 확인하고 과정을 만듭니다.\n3. 서버 시작 → 관리자 화면 열기 → 사용자 명단을 등록합니다.\n4. 주소 복사로 수강생에게 접속 주소를 안내합니다.\n\n다음 수업부터\n목록에서 과정을 선택하고 서버 시작을 누릅니다.\n\n다른 PC에서 접속이 안 될 때\n서버가 실행 중인지, 안내 IP가 현재 PC의 주소인지 확인하고 접속·오류 점검을 실행하세요. 교육장 네트워크와 방화벽의 해당 포트 접근도 확인해야 합니다.\n\n수업이 끝난 뒤\n해당 과정을 선택하고 서버 중지를 누릅니다. 관리창을 닫는 것만으로 서버가 꺼지지는 않습니다.\n\n백업\n서버 중지 후 설정 JSON과 저장 폴더 전체를 같은 시점으로 복사합니다. 복원 절차는 docs/admin-guide.md를 참고하세요.")

    def close(self):
        if self.busy_paths or any(label != "상태 확인" for label, _ in self.jobs.values()):
            messagebox.showinfo("작업 진행 중", "시작·중지·저장 작업이 끝난 뒤 관리창을 닫아 주세요.", parent=self.root)
            return
        if any(state.get("running") for state in self.states.values()):
            if not messagebox.askyesno("관리창만 닫을까요?", "실행 중인 서버는 계속 동작합니다. 서버도 끝내려면 취소한 뒤 해당 과정의 서버 중지를 눌러 주세요.", parent=self.root):
                return
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="AssignmentHub 로컬 서버 관리창")
    parser.add_argument("--instances-dir", type=Path, default=PROJECT_ROOT / "instances")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    root = tk.Tk()
    ServerManager(root, args.instances_dir, args.data_dir)
    root.mainloop()


if __name__ == "__main__":
    main()

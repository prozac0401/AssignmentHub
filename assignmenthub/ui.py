"""Korean Streamlit console. Large submission bytes never pass through this process."""
from __future__ import annotations

import csv
import html
import io
import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components


API_URL = os.environ.get("AH_API_URL", "http://127.0.0.1:8601").rstrip("/")
STATUS = {"uploading": "전송 중", "paused": "일시 중지", "verifying": "검증 중", "finalizing": "저장 중", "completed": "제출 완료", "failed": "실패", "cancelled": "취소", "expired": "만료"}


class APIError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def call(path: str, method: str = "GET", data=None, raw: bytes | None = None, content_type: str | None = None, binary: bool = False):
    headers = {"Accept": "application/json"}
    if st.session_state.get("token"):
        headers["Authorization"] = "Bearer " + st.session_state.token
    payload = raw
    if data is not None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    try:
        with urlopen(Request(API_URL + "/api" + path, data=payload, headers=headers, method=method), timeout=60) as response:
            result = response.read()  # JSON / small roster / CSV only; never submission files.
            return result if binary else json.loads(result)
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read(65536)).get("detail", "요청을 처리하지 못했습니다.")
        except (ValueError, UnicodeDecodeError):
            detail = "요청을 처리하지 못했습니다."
        raise APIError(str(detail), exc.code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise APIError("서버에 연결할 수 없습니다. 실행 상태와 네트워크를 확인해 주세요.") from exc


def size(value: int) -> str:
    value = int(value or 0)
    for unit, divisor in (("GiB", 2**30), ("MiB", 2**20), ("KiB", 2**10)):
        if value >= divisor:
            return f"{value / divisor:,.2f} {unit}"
    return f"{value:,} B"


def timestamp(value: str | float | None) -> str:
    if not value:
        return "—"
    tz = st.session_state.get("info", {}).get("timezone", "Asia/Seoul")
    try:
        dt = datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S") + f" ({tz})"
    except (ValueError, KeyError):
        return str(value)


def csv_bytes(rows: list[dict], columns: list[str]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        safe = {}
        for key in columns:
            value = str(row.get(key, ""))
            # Spreadsheet formula injection includes whitespace before operators.
            safe[key] = "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
        writer.writerow(safe)
    return text.getvalue().encode("utf-8-sig")


def private_csv_download(label: str, data: bytes, filename: str):
    """Small, authorized CSV stays in this user's DOM; never use Streamlit media URLs."""
    # Escape '<' even in valid JSON so untrusted cells cannot close the script element.
    payload = json.dumps(data.decode("utf-8-sig"), ensure_ascii=True).replace("<", "\\u003c")
    output_name = json.dumps(filename, ensure_ascii=True).replace("<", "\\u003c")
    components.html(
        '<button id="download" type="button" style="border:1px solid #166b50;border-radius:6px;background:white;color:#166b50;padding:9px 15px;cursor:pointer">'
        + html.escape(label) + '</button><script>'
        + f'const csv={payload};const filename={output_name};'
        + "document.getElementById('download').onclick=()=>{"
        + "const blob=new Blob(['\\uFEFF',csv],{type:'text/csv;charset=utf-8'});"
        + "const url=URL.createObjectURL(blob);const anchor=document.createElement('a');"
        + "anchor.href=url;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();"
        + "setTimeout(()=>URL.revokeObjectURL(url),1000);};</script>",
        height=52,
    )


def clear_session():
    for key in list(st.session_state):
        if key != "info":
            del st.session_state[key]


def login():
    left, main, right = st.columns([1, 2, 1])
    with main:
        st.markdown("### 수강생·관리자 로그인")
        st.caption("관리자가 등록한 계정으로 로그인하세요. 처음이라면 전달받은 임시비밀번호를 입력하세요.")
        with st.form("login"):
            user_id = st.text_input("로그인 ID", max_chars=128)
            password = st.text_input("비밀번호", type="password", max_chars=128)
            submitted = st.form_submit_button("로그인", use_container_width=True, type="primary")
        if submitted:
            result = call("/auth/login", "POST", {"user_id": user_id, "password": password})
            st.session_state.token = result["token"]
            st.session_state.user = result["user"]
            st.session_state.must_change = result["must_change_password"]
            st.rerun()


def password_change(required: bool = False):
    st.subheader("비밀번호 변경")
    if required:
        st.info("최초 로그인 또는 관리자 초기화 상태입니다. 비밀번호를 변경하고 다시 로그인해야 과제에 접근할 수 있습니다.")
    st.caption("12~128자로 입력하세요. 현재 비밀번호와 같은 값은 사용할 수 없습니다. 붙여넣기를 사용할 수 있습니다.")
    with st.form("password_change", clear_on_submit=True):
        current = st.text_input("현재 비밀번호 또는 임시비밀번호", type="password", max_chars=128)
        new = st.text_input("새 비밀번호", type="password", max_chars=128)
        confirm = st.text_input("새 비밀번호 확인", type="password", max_chars=128)
        change = st.form_submit_button("비밀번호 변경", type="primary")
    if change:
        call("/auth/password", "POST", {"current_password": current, "new_password": new, "confirm_password": confirm})
        clear_session()
        st.session_state.flash = "비밀번호를 변경했습니다. 새 비밀번호로 다시 로그인하세요."
        st.rerun()


def file_download(file: dict, key: str):
    if st.button("다운로드 준비", key=key):
        result = call(f"/files/{file['id']}/ticket", "POST")
        ticket = html.escape(result["ticket"], quote=True)
        components.html(
            '<form action="/api/downloads" method="post" target="_blank">'
            f'<input type="hidden" name="ticket" value="{ticket}">'
            '<button style="border:1px solid #166b50;border-radius:6px;background:#166b50;color:white;padding:8px 14px;cursor:pointer" type="submit">파일 다운로드</button>'
            '<span style="font:12px sans-serif;color:#607468;margin-left:10px">일회용 링크 · 만료되면 다시 준비하세요</span></form>',
            height=54,
        )


def submission_details(item: dict, key: str, admin: bool = False):
    st.write(f"**제출번호 {item.get('submission_number', '—')}** · 버전 {item.get('version', '—')}")
    st.caption(f"{item.get('assignment_title', item.get('assignment_id', ''))} · ID {item.get('user_id', '')} · {timestamp(item.get('completed_at'))}")
    for index, file in enumerate(item.get("files", [])):
        left, right = st.columns([4, 1])
        with left:
            st.text(file["name"])
            st.caption(f"{size(file['size'])} · {int(file['size']):,} B")
            if admin and file.get("storage_path"):
                st.code(file["storage_path"], language=None)
        with right:
            file_download(file, f"download_{key}_{index}")


def student_home():
    assignments = call("/assignments")
    quota = call("/quota")
    a, b, c = st.columns(3)
    a.metric("완료 제출 사용량", size(quota["used_bytes"]))
    b.metric("진행 중 예약 용량", size(quota["reserved_bytes"]))
    c.metric("누적 저장 한도", size(quota["quota_bytes"]))
    st.caption(f"파일당 최대 {size(quota['max_file_bytes'])} ({quota['max_file_bytes']:,} B) · 한 묶음 최대 {quota['max_files']}개 · 1 GiB = 1,073,741,824 B · 이전 제출 버전도 사용량에 포함")
    st.divider()
    st.subheader("과제 제출")
    if assignments:
        assignment = st.selectbox("과제", assignments, format_func=lambda a: a["title"] + (" · 접수 중" if a["is_open"] else " · 접수 종료"))
        st.text(assignment.get("description", ""))
        if not assignment["is_open"]:
            st.info("신규 제출 접수가 종료되었습니다. 종료 전에 시작한 유효한 작업은 보관 시간 내에 이어 올릴 수 있습니다.")
    else:
        st.info("등록된 과제가 없습니다.")
    with st.container(border=True):
        st.markdown("**브라우저에서 파일을 직접 전송합니다.**")
        st.write("아래에서 일회용 연결 코드를 발급한 뒤 제출 화면에 입력하세요. 제출 화면에서 같은 계정으로 다시 로그인해도 됩니다.")
        if st.button("일회용 연결 코드 발급", type="primary"):
            bridge = call("/auth/bridge", "POST")
            st.session_state.bridge = bridge
        if st.session_state.get("bridge"):
            st.code(st.session_state.bridge["code"], language=None)
            st.caption(f"발급 후 {st.session_state.bridge['expires_in']}초 동안 한 번만 사용할 수 있습니다. 다른 사람에게 공유하지 마세요.")
        st.link_button("파일 제출·이어 올리기 화면 열기 ↗", "/upload", type="primary")
    uploads = call("/uploads")
    if uploads:
        with st.expander(f"미완료 업로드 {len(uploads)}건"):
            st.dataframe([{"과제": u.get("assignment_title", ""), "상태": STATUS.get(u["status"], u["status"]), "파일 수": len(u["files"]), "확정 수신량": size(sum(f["offset"] for f in u["files"])), "전체 크기": size(u["total_bytes"])} for u in uploads], hide_index=True, use_container_width=True)
            st.caption("완료되지 않은 작업은 제출자로 집계되지 않습니다. 파일 제출 화면에서 이어 올리거나 취소하세요.")
    st.divider()
    st.subheader("나의 제출 이력")
    previous = st.checkbox("이전 완료 버전도 표시")
    params = {"all_versions": str(previous).lower()}
    if assignments:
        params["assignment_id"] = assignment["id"]
    submissions = call("/submissions?" + urlencode(params))
    if not submissions:
        st.info("이 과제의 완료된 제출이 없습니다.")
    for item in submissions:
        with st.container(border=True):
            submission_details(item, item["id"])


def admin_roster():
    st.subheader("사용자 명단 등록")
    st.caption("ID는 앞뒤 공백을 제거하고 대소문자를 구분합니다. 앞자리 0을 보존하려면 엑셀의 user_id 열을 텍스트로 입력하세요.")
    template = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "users_template.xlsx")
    if os.path.isfile(template):
        with open(template, "rb") as source:
            st.download_button("명단 엑셀 템플릿 다운로드", source.read(), "users_template.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    roster = st.file_uploader("명단 .xlsx (최대 5 MiB, 과제 파일 업로드용이 아닙니다)", type=["xlsx"], key="roster_file", on_change=lambda: st.session_state.pop("roster_preview", None))
    if st.button("명단 검증 및 미리보기", disabled=roster is None):
        if roster.size > 5 * 2**20:
            st.error("명단은 최대 5 MiB입니다.")
        else:
            st.session_state.roster_preview = call("/admin/roster/preview", "POST", raw=roster.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    preview = st.session_state.get("roster_preview")
    if preview:
        for error in preview.get("errors", []):
            st.error(str(error))
        st.dataframe([{"행": r.get("row"), "user_id": r.get("user_id"), "name": r.get("name"), "group": r.get("group", ""), "반영": r.get("action", ""), "기존 정보": json.dumps({k: r["existing"].get(k) for k in ("name", "group", "active")}, ensure_ascii=False) if isinstance(r.get("existing"), dict) else "", "오류": "; ".join(map(str, r.get("errors", [])))} for r in preview.get("rows", [])], hide_index=True, use_container_width=True)
        update = st.checkbox("기존 계정의 이름·그룹 변경도 적용합니다. 비밀번호·활성 상태·제출 기록은 유지됩니다.")
        if st.button("검증된 명단 반영", disabled=not preview.get("valid"), type="primary"):
            rows = [{k: r.get(k, "") for k in ("user_id", "name", "group")} for r in preview["rows"]]
            result = call("/admin/roster/apply", "POST", {"rows": rows, "update_existing": update})
            st.session_state.credentials = result.get("created", [])
            st.session_state.pop("roster_preview", None)
            st.success(f"신규 {len(result.get('created', []))}명 등록 · 기존 {result.get('updated', 0)}명 정보 변경")
    if st.session_state.get("credentials"):
        with st.container(border=True):
            st.warning("신규 사용자의 임시비밀번호입니다. 이 결과를 안전하게 배포하세요. 결과 닫기 또는 로그아웃 후에는 다시 조회할 수 없습니다.")
            st.dataframe(st.session_state.credentials, hide_index=True, use_container_width=True)
            private_csv_download("임시비밀번호 결과 CSV 다운로드", csv_bytes(st.session_state.credentials, ["user_id", "name", "temporary_password"]), "temporary_passwords.csv")
            if st.button("임시비밀번호 결과 닫기"):
                del st.session_state["credentials"]
                st.rerun()
    st.divider()
    st.subheader("사용자 계정 관리")
    search = st.text_input("ID·이름·그룹 검색")
    users = call("/admin/users?" + urlencode({"search": search}))
    st.dataframe([{"ID": u["user_id"], "이름": u["name"], "그룹": u.get("group", ""), "역할": "관리자" if u["role"] == "admin" else "수강생", "활성": u["active"], "비밀번호 변경 필요": u["must_change_password"]} for u in users], hide_index=True, use_container_width=True)
    students = [u for u in users if u["role"] != "admin"]
    if students:
        selected = st.selectbox("관리할 수강생", students, format_func=lambda u: f"{u['user_id']} · {u['name']} · {'활성' if u['active'] else '비활성'}")
        col1, col2 = st.columns(2)
        with col1:
            confirm_reset = st.checkbox("이 사용자의 기존 로그인·업로드 권한을 즉시 무효화하고 비밀번호를 초기화합니다.", key="confirm_reset_" + selected["id"])
            if st.button("비밀번호 초기화", disabled=not confirm_reset):
                result = call(f"/admin/users/{selected['id']}/reset", "POST")
                st.session_state.reset_result = {"user_id": selected["user_id"], "temporary_password": result["temporary_password"]}
            if st.session_state.get("reset_result"):
                st.warning("새 임시비밀번호 · " + st.session_state.reset_result["user_id"])
                st.code(st.session_state.reset_result["temporary_password"], language=None)
                if st.button("초기화 결과 닫기"):
                    del st.session_state["reset_result"]
                    st.rerun()
        with col2:
            action = "비활성화" if selected["active"] else "활성화"
            confirm_active = st.checkbox(f"{selected['user_id']} 계정을 {action}합니다. 기존 제출 이력은 보존됩니다.", key="confirm_active_" + selected["id"])
            if st.button(f"계정 {action}", disabled=not confirm_active):
                call(f"/admin/users/{selected['id']}", "PATCH", {"active": not selected["active"]})
                st.rerun()


def admin_home():
    st.subheader("운영 안내")
    users = call("/admin/users")
    assignments = call("/assignments")
    students = [user for user in users if user["role"] == "student" and user["active"]]
    opened = [item for item in assignments if item["is_open"]]
    left, right = st.columns(2)
    left.metric("등록된 활성 수강생", len(students))
    right.metric("접수 중인 과제", len(opened))

    def navigate(target):
        st.session_state.next_page = target
        st.rerun()

    with st.container(border=True):
        st.markdown("**1. 수강생 명단 등록**")
        st.write(f"현재 활성 수강생 {len(students)}명입니다." if students else "엑셀 템플릿을 내려받아 명단을 입력하고, 미리보기를 확인한 뒤 등록하세요.")
        st.caption("처음 등록한 수강생에게는 서로 다른 임시비밀번호가 발급됩니다. 수강생은 첫 로그인 후 새 비밀번호로 변경합니다.")
        if st.button("명단 등록·사용자 관리로 이동", type="primary" if not students else "secondary"):
            navigate("사용자 관리")
    with st.container(border=True):
        st.markdown("**2. 과제와 접수 상태 확인**")
        st.write("접수 중: " + ", ".join(item["title"] for item in opened) if opened else "현재 접수 중인 과제가 없습니다. 과제 관리에서 접수를 열어 주세요.")
        if st.button("과제 관리로 이동"):
            navigate("과제 관리")
    with st.container(border=True):
        st.markdown("**3. 접속 주소 안내**")
        url = st.session_state.info.get("public_url", "")
        if url:
            st.code(url, language=None)
            st.caption("코드 상자의 복사 버튼으로 주소를 복사하세요. 수강생 PC에서 접속을 확인한 후 안내합니다.")
        st.write("접속 주소를 바꾸거나 서버를 시작·중지하려면 서버 PC에서 start.bat를 열어 주세요.")
        st.caption("다른 PC에서 접속되지 않으면 서버 관리창의 '접속·오류 점검'에서 IP·포트를 확인하고 교육장 네트워크의 방화벽을 점검하세요.")
    if st.button("제출 현황 확인"):
        navigate("제출 현황")
    with st.expander("수업 종료와 백업"):
        st.write("서버 관리창에서 해당 과정을 선택하고 서버 중지를 누릅니다. 관리창을 닫는 것만으로 서버가 중지되지는 않습니다.")
        st.write("중지 상태를 확인한 뒤 설정 JSON과 저장 폴더 전체를 함께 백업하세요. 완료 제출물은 자동 삭제하지 않습니다.")


def admin_assignments():
    st.subheader("과제 관리")
    assignments = call("/assignments")
    with st.expander("새 과제 추가", expanded=not assignments):
        with st.form("new_assignment", clear_on_submit=True):
            title = st.text_input("새 과제명", max_chars=200)
            description = st.text_area("과제 설명", max_chars=10000)
            opened = st.checkbox("즉시 접수 시작", value=True)
            submit = st.form_submit_button("과제 추가", type="primary")
        if submit:
            call("/admin/assignments", "POST", {"title": title, "description": description, "is_open": opened})
            st.rerun()
    if assignments:
        selected = st.selectbox("수정할 과제", assignments, format_func=lambda a: a["title"])
        with st.form("edit_assignment_" + selected["id"]):
            title = st.text_input("과제명", value=selected["title"], max_chars=200)
            description = st.text_area("설명", value=selected.get("description", ""), max_chars=10000)
            opened = st.checkbox("접수 중", value=bool(selected["is_open"]))
            st.caption("접수 종료는 신규 제출 시작을 막습니다. 종료 전에 시작한 유효한 작업은 보관 시간 내에 완료할 수 있습니다.")
            save = st.form_submit_button("변경 사항 저장", type="primary")
        if save:
            call(f"/admin/assignments/{selected['id']}", "PATCH", {"title": title, "description": description, "is_open": opened})
            st.rerun()


def admin_dashboard():
    st.subheader("제출 현황")
    assignments = call("/assignments")
    if not assignments:
        st.info("과제를 먼저 추가하세요.")
        return
    assignment = st.selectbox("조회할 과제", assignments, format_func=lambda a: a["title"])
    c1, c2, c3 = st.columns([2, 1, 1])
    search = c1.text_input("ID·이름·그룹 검색", key="dashboard_search")
    group = c2.text_input("그룹 정확히 일치", key="dashboard_group")
    inactive = c3.checkbox("비활성 계정 포함")
    data = call("/admin/dashboard?" + urlencode({"assignment_id": assignment["id"], "search": search, "group": group, "include_inactive": str(inactive).lower()}))
    metrics = st.columns(5)
    for column, label, field in zip(metrics, ("활성 대상자", "제출자", "미제출자", "진행 건수", "실패 건수"), ("target_count", "submitted_count", "missing_count", "in_progress_count", "failed_count")):
        column.metric(label, data[field])
    st.caption("제출자는 이 과제에 완료된 제출이 하나 이상 있는 활성 수강생입니다. 관리자와 미완료 작업은 제출자 수에 포함되지 않습니다.")
    mode = st.radio("목록 필터", ["전체", "제출자", "미제출자"], horizontal=True)
    rows = [r for r in data["rows"] if mode == "전체" or bool(r["submitted"]) == (mode == "제출자")]
    st.dataframe([{"ID": r["user_id"], "이름": r["name"], "그룹": r.get("group", ""), "활성": r["active"], "완료 제출": bool(r["submitted"]), "완료 제출 횟수": r["submission_count"], "최근 제출번호": (r.get("latest") or {}).get("submission_number", ""), "최근 완료 시각": timestamp((r.get("latest") or {}).get("completed_at"))} for r in rows], hide_index=True, use_container_width=True)
    if st.button("이 과제의 전체 현황 CSV 준비"):
        st.session_state.export = {"assignment_id": assignment["id"], "data": call("/admin/export?" + urlencode({"assignment_id": assignment["id"]}), binary=True)}
    export = st.session_state.get("export")
    if export and export["assignment_id"] == assignment["id"]:
        private_csv_download("제출 현황 CSV 다운로드", export["data"], "submission_status.csv")
    st.divider()
    st.subheader("완료 제출물·보존 버전")
    submissions = data.get("submissions", [])
    if not submissions:
        st.info("조회 조건에 해당하는 완료 제출물이 없습니다.")
    for item in submissions:
        with st.expander(f"{item.get('user_id', '')} · 제출번호 {item.get('submission_number', '—')} · 버전 {item.get('version', '—')} · {size(item['total_bytes'])}"):
            submission_details(item, "admin_" + item["id"], admin=True)


def admin_storage():
    st.subheader("저장 공간과 운영 기록")
    storage = call("/admin/storage")
    cols = st.columns(4)
    for col, label, field in zip(cols, ("완료 사용량", "예약 용량", "임시 파일 실사용량", "볼륨 잔여 공간"), ("used_bytes", "reserved_bytes", "temp_bytes", "free_bytes")):
        col.metric(label, size(storage.get(field, 0)))
    st.caption("예약 용량은 미완료 묶음 전체에 대한 할당입니다. 임시 파일 실사용량과 중복해서 볼륨 사용량에 더하지 않습니다.")
    names = {"max_file_bytes": "파일당 최대", "user_quota_bytes": "사용자별 누적 한도", "min_free_bytes": "볼륨 최소 여유 공간", "chunk_bytes": "전송 청크", "max_files": "묶음당 파일 수", "concurrent_uploads": "동시 파일 전송", "upload_ttl_hours": "미완료 보관 시간 (시간)", "storage_root": "저장 루트", "instance_id": "인스턴스 ID"}
    combined = {**st.session_state.get("info", {}), **storage}
    st.table([{"설정": label, "값": size(combined[field]) if field.endswith("_bytes") else str(combined[field])} for field, label in names.items() if field in combined])
    st.subheader("관리 작업 기록")
    audit = call("/admin/audit")
    st.dataframe([{"시각": timestamp(row.get("at")), "관리자": row.get("actor", ""), "작업": row.get("action", ""), "대상": row.get("object_id", ""), "상세": row.get("detail", "")} for row in audit], hide_index=True, use_container_width=True)


def main():
    st.set_page_config(page_title="AssignmentHub", page_icon="📁", layout="wide")
    st.markdown("""<style>.stApp{background:#f5f7f3}div[data-testid="stMetric"]{background:white;border:1px solid #dce5dc;border-radius:12px;padding:18px}section[data-testid="stSidebar"]{background:#eef3ec}.block-container{padding-top:2.5rem;max-width:1280px}h1,h2,h3{color:#183e2e}a{color:#166b50}</style>""", unsafe_allow_html=True)
    try:
        if "info" not in st.session_state:
            st.session_state.info = call("/info")
        info = st.session_state.info
        st.caption("ASSIGNMENTHUB · 로컬 과제 제출·관리")
        st.title(info.get("course_name", "AssignmentHub"))
        if st.session_state.get("flash"):
            st.success(st.session_state.pop("flash"))
        if not st.session_state.get("token"):
            login()
            return
        user = call("/auth/me")
        st.session_state.user = user
        with st.sidebar:
            st.markdown("## AssignmentHub")
            st.write(user["name"])
            st.caption(user["user_id"] + (" · 관리자" if user["role"] == "admin" else " · 수강생"))
            st.caption(info.get("instance_id", ""))
            if st.button("로그아웃", use_container_width=True):
                call("/auth/logout", "POST")
                clear_session()
                st.rerun()
        if user.get("must_change_password") or st.session_state.get("must_change"):
            password_change(required=True)
            return
        pages = ["과제 제출·나의 이력"]
        if user["role"] == "admin":
            pages = ["운영 안내", "제출 현황", "사용자 관리", "과제 관리", "저장 공간·기록"]
        pages.append("비밀번호 변경")
        requested = st.session_state.pop("next_page", None)
        if requested in pages:
            st.session_state.navigation = requested
        with st.sidebar:
            page = st.radio("메뉴", pages, label_visibility="collapsed", key="navigation")
            st.divider()
            st.caption("표시 시간대: " + info.get("timezone", "Asia/Seoul"))
            if user["role"] != "admin":
                st.link_button("브라우저 파일 제출 ↗", "/upload", use_container_width=True)
        {"운영 안내": admin_home, "과제 제출·나의 이력": student_home, "제출 현황": admin_dashboard, "사용자 관리": admin_roster, "과제 관리": admin_assignments, "저장 공간·기록": admin_storage, "비밀번호 변경": password_change}[page]()
    except APIError as exc:
        st.error(str(exc))
        if exc.status == 401:
            clear_session()
            st.info("로그인이 만료되었거나 계정 권한이 변경되었습니다. 다시 로그인하세요.")
            if st.button("로그인 화면으로"):
                st.rerun()


if __name__ == "__main__":
    main()

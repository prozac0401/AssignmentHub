"""Offline desktop styling and a viewport that preserves access on small screens."""
import tkinter as tk
from tkinter import ttk

BACKGROUND = "#f7f8fc"


def apply_theme(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=BACKGROUND)
    style.configure(".", font=("Malgun Gothic", 10), background=BACKGROUND, foreground="#202539")
    style.configure("TButton", background="white", foreground="#414c63", bordercolor="#d5dbe8",
                    lightcolor="white", darkcolor="white", padding=(14, 9), focusthickness=2, focuscolor="#a5b4fc")
    style.map("TButton", background=[("active", "#eeedff")], foreground=[("disabled", "#9098aa")])
    style.configure("Primary.TButton", background="#4f46e5", foreground="white", bordercolor="#4f46e5",
                    lightcolor="#4f46e5", darkcolor="#4f46e5")
    style.map("Primary.TButton", background=[("disabled", "#e1e3ef"), ("active", "#4338ca")],
              foreground=[("disabled", "#848ba1"), ("!disabled", "white")])
    style.configure("Heading.TLabel", font=("Malgun Gothic", 20, "bold"), foreground="#202539")
    style.configure("Brand.TLabel", font=("Segoe UI", 11, "bold"), foreground="#4f46e5")
    style.configure("Muted.TLabel", foreground="#647087")
    style.configure("TEntry", fieldbackground="white", bordercolor="#cfd5e3", padding=7)
    style.map("TEntry", bordercolor=[("focus", "#4f46e5")], fieldbackground=[("readonly", "#f0f2f9")])
    style.configure("TCombobox", fieldbackground="white", padding=7, bordercolor="#cfd5e3", arrowsize=14)
    style.configure("TNotebook", borderwidth=0, tabmargins=(0, 6, 0, 0))
    style.configure("TNotebook.Tab", padding=(18, 10), background="#f0f2f9", foreground="#647087",
                    bordercolor="#d5dbe8", lightcolor="#d5dbe8", darkcolor="#d5dbe8",
                    focuscolor="#4f46e5")
    # Clam otherwise overrides the selected tab with smaller padding (6, 4, 6, 2).
    # Keep geometry identical in every state; communicate selection using color.
    style.map("TNotebook.Tab", padding=[], lightcolor=[],
              background=[("selected", "#e7e7fc"), ("active", "#e9ecf6")],
              foreground=[("selected", "#3730a3"), ("active", "#414c63")])
    style.configure("Treeview", rowheight=46, background="white", fieldbackground="white",
                    bordercolor="#e3e7ef", lightcolor="#e3e7ef", darkcolor="#e3e7ef")
    style.configure("Treeview.Heading", font=("Malgun Gothic", 10, "bold"), background="#f0f2f9",
                    foreground="#647087", padding=(12, 12), relief="flat")
    style.map("Treeview", background=[("selected", "#eeedff")], foreground=[("selected", "#3730a3")])
    style.configure("Horizontal.TProgressbar", background="#4f46e5", troughcolor="#e9ecf5", borderwidth=0)
    style.configure("TScrollbar", background="#d5dbe8", troughcolor=BACKGROUND, borderwidth=0, arrowsize=12)


class ScrollFrame(ttk.Frame):
    """A width-following frame with scoped wheel and keyboard-focus scrolling."""
    def __init__(self, parent, padding=0):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=BACKGROUND)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(self.canvas, padding=padding)
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self._pending = None
        self.content.bind("<Configure>", self.layout)
        self.canvas.bind("<Configure>", self.layout)
        top = self.winfo_toplevel()
        self._top = top
        self._wheel = top.bind("<MouseWheel>", self.wheel, add="+")
        self._focus = top.bind("<FocusIn>", self.reveal, add="+")
        self.bind("<Destroy>", self.cleanup, add="+")

    def contains(self, widget):
        return str(widget).startswith(str(self.content) + ".")

    def layout(self, _=None):
        # Wait for nested grids and wrapped labels to settle before measuring.
        if self._pending is None:
            self._pending = self.after_idle(self.fit)

    def fit(self):
        width = max(1, self.canvas.winfo_width())
        self.canvas.itemconfigure(self.item, width=width)
        self.content.update_idletasks()
        height = max(self.content.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.itemconfigure(self.item, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self._pending = None

    def wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        if widget and (self.contains(widget) or widget == self.canvas):
            if self.content.winfo_height() > self.canvas.winfo_height():
                self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def reveal(self, event):
        if not self.contains(event.widget):
            return
        self.update_idletasks()
        y = event.widget.winfo_rooty() - self.content.winfo_rooty()
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        height = event.widget.winfo_height()
        if y < top or y + height > bottom:
            target = y - 16 if y < top else y + height - self.canvas.winfo_height() + 16
            self.canvas.yview_moveto(max(0, target) / max(1, self.content.winfo_height()))

    def cleanup(self, event):
        if event.widget == self and self._top.winfo_exists():
            if self._pending is not None:
                self.after_cancel(self._pending)
            self._top.unbind("<MouseWheel>", self._wheel)
            self._top.unbind("<FocusIn>", self._focus)

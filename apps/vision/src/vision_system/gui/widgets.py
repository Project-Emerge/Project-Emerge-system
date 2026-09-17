"""Small reusable ttk components: a log pane, a path picker row, a value indicator."""

from __future__ import annotations

from typing import Any

from .toolkit import TkToolkit

DEFAULT_MAX_LOG_LINES = 2000


class LogPane:
    """A read-only scrolling text area with a bounded backlog and a Clear button."""

    def __init__(
        self,
        parent: Any,
        toolkit: TkToolkit,
        *,
        clear_label: str,
        max_lines: int = DEFAULT_MAX_LOG_LINES,
    ) -> None:
        self.max_lines = max_lines
        self.frame = toolkit.ttk.Frame(parent, padding=6)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.text = toolkit.tk.Text(self.frame, wrap="none", height=12, state="disabled")
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll = toolkit.ttk.Scrollbar(self.frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        toolkit.ttk.Button(self.frame, text=clear_label, command=self.clear).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )

    def append(self, line: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        excess = int(self.text.index("end-1c").split(".")[0]) - self.max_lines
        if excess > 0:
            self.text.delete("1.0", f"{excess + 1}.0")
        self.text.see("end")
        self.text.configure(state="disabled")

    def extend(self, lines) -> None:
        for line in lines:
            self.append(line)

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


class PathRow:
    """A label, an entry bound to a variable, and a Browse button."""

    def __init__(
        self,
        parent: Any,
        toolkit: TkToolkit,
        *,
        row: int,
        label: str,
        variable: Any,
        directory: bool,
        browse_label: str,
        all_files_label: str,
    ) -> None:
        self.toolkit = toolkit
        self.variable = variable
        self.directory = directory
        self.all_files_label = all_files_label
        toolkit.ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        toolkit.ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, columnspan=3, sticky="ew", padx=4, pady=2
        )
        toolkit.ttk.Button(parent, text=browse_label, command=self.browse).grid(
            row=row, column=4, sticky="e", pady=2
        )

    def browse(self) -> None:
        current = self.variable.get().strip()
        initial = current or "."
        if self.directory:
            chosen = self.toolkit.filedialog.askdirectory(initialdir=initial)
        else:
            chosen = self.toolkit.filedialog.askopenfilename(
                initialdir=initial,
                filetypes=[("JSON", "*.json"), (self.all_files_label, "*.*")],
            )
        if chosen:
            self.variable.set(chosen)


class Indicator:
    """A label bound to a StringVar that is only written when the text changes.

    Tk repaints on every ``set``, so guarding the write is what keeps a 4 Hz tick
    from making the status line flicker.
    """

    def __init__(self, parent: Any, toolkit: TkToolkit, *, initial: str = "") -> None:
        self.variable = toolkit.tk.StringVar(value=initial)
        self.label = toolkit.ttk.Label(parent, textvariable=self.variable)

    def set(self, text: str) -> None:
        if self.variable.get() != text:
            self.variable.set(text)

    def pack(self, **options: Any) -> None:
        self.label.pack(**options)


def bind_if_changed(variable: Any, value: str) -> None:
    """Assign to a Tk variable only when the value actually differs."""
    if variable.get() != value:
        variable.set(value)


__all__ = [
    "DEFAULT_MAX_LOG_LINES",
    "Indicator",
    "LogPane",
    "PathRow",
    "bind_if_changed",
]

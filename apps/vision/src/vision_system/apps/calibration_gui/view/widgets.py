"""Dumb widgets: a step sidebar, a status table and a field form.

Nothing here branches on domain state. Each component is told what to show.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ....gui.toolkit import TkToolkit
from .. import presentation


class StepSidebar:
    """The numbered step list on the left, with a readiness dot per entry."""

    READY_MARK = "●"
    BLOCKED_MARK = "○"

    def __init__(
        self, parent: Any, toolkit: TkToolkit, *, on_select: Callable[[str], None]
    ) -> None:
        ttk = toolkit.ttk
        self.frame = ttk.Frame(parent, padding=(8, 8))
        self.buttons: dict[str, Any] = {}
        self._on_select = on_select
        self._toolkit = toolkit

    def build(self, steps) -> None:
        for row, step in enumerate(steps):
            button = self._toolkit.ttk.Button(
                self.frame,
                text=presentation.STEP_PREFIX.format(index=step.index, title=step.title),
                width=22,
                command=lambda step_id=step.id: self._on_select(step_id),
            )
            button.grid(row=row, column=0, sticky="ew", pady=2)
            self.buttons[step.id] = button

    def paint(self, steps, overview, current_step_id: str) -> None:
        for step in steps:
            ready = step.precondition(overview).ready
            mark = self.READY_MARK if ready else self.BLOCKED_MARK
            current = "▸" if step.id == current_step_id else " "
            self.buttons[step.id].configure(
                text=f"{current}{mark} {step.index} {step.title}"
            )


class StatusTable:
    """A Treeview whose columns come from the step that asked for it."""

    def __init__(self, parent: Any, toolkit: TkToolkit) -> None:
        ttk = toolkit.ttk
        self.toolkit = toolkit
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.table: Any | None = None
        self.kind: str | None = None

    def show(self, kind: str) -> None:
        """Rebuild only when the step changed the table it wants."""
        if kind == self.kind:
            return
        self.kind = kind
        if self.table is not None:
            self.table.destroy()
            self.table = None
        if kind == "none":
            return
        columns = presentation.TABLE_COLUMNS[kind]
        self.table = self.toolkit.ttk.Treeview(
            self.frame,
            columns=tuple(name for name, _, _ in columns),
            show="headings",
            height=6,
        )
        for name, title, width in columns:
            self.table.heading(name, text=title)
            self.table.column(name, width=width, anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")

    def paint(self, rows: Sequence[Sequence[str]]) -> None:
        if self.table is None:
            return
        self.table.delete(*self.table.get_children())
        for values in rows:
            self.table.insert("", "end", values=tuple(values))


class FieldForm:
    """The editable settings a step declares, bound to Tk variables."""

    def __init__(self, parent: Any, toolkit: TkToolkit) -> None:
        self.toolkit = toolkit
        self.frame = toolkit.ttk.Frame(parent)
        self.frame.columnconfigure(1, weight=1)
        self.variables: dict[str, Any] = {}
        self._rendered: tuple[str, ...] = ()

    def show(self, fields) -> bool:
        """Rebuild the form when the step changed it. True when it was rebuilt."""
        signature = tuple(field.id for field in fields)
        if signature == self._rendered:
            return False
        self._rendered = signature
        for child in self.frame.winfo_children():
            child.destroy()
        self.variables.clear()
        tk, ttk = self.toolkit.tk, self.toolkit.ttk
        for row, field in enumerate(fields):
            if field.kind == "bool":
                variable = tk.BooleanVar()
                ttk.Checkbutton(self.frame, text=field.label, variable=variable).grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=2
                )
            elif field.kind == "choice":
                variable = tk.StringVar(value=field.choices[0] if field.choices else "")
                ttk.Label(self.frame, text=field.label).grid(row=row, column=0, sticky="w")
                ttk.Combobox(
                    self.frame,
                    textvariable=variable,
                    values=list(field.choices),
                    state="readonly",
                    width=10,
                ).grid(row=row, column=1, sticky="w", padx=4)
            else:
                variable = tk.StringVar()
                ttk.Label(self.frame, text=field.label).grid(row=row, column=0, sticky="w")
                ttk.Entry(self.frame, textvariable=variable).grid(
                    row=row, column=1, sticky="ew", padx=4, pady=2
                )
            self.variables[field.id] = variable
            if field.help:
                ttk.Label(self.frame, text=field.help, foreground="#6b7680").grid(
                    row=row, column=2, sticky="w", padx=6
                )
        return True

    def seed(self, values: dict[str, str]) -> None:
        """Fill freshly built fields with what is already saved.

        Only on a rebuild: doing it every frame would fight the operator for the
        keyboard, overwriting each character as it is typed.
        """
        for name, value in values.items():
            if name in self.variables:
                self.variables[name].set(value)

    def values(self) -> dict[str, object]:
        return {name: variable.get() for name, variable in self.variables.items()}


class ActionBar:
    """One button per action the current step declares."""

    def __init__(self, parent: Any, toolkit: TkToolkit, *, on_trigger) -> None:
        self.toolkit = toolkit
        self.frame = toolkit.ttk.Frame(parent)
        self._on_trigger = on_trigger
        self._rendered: tuple[str, ...] = ()
        self.buttons: dict[str, Any] = {}

    def show(self, step) -> None:
        signature = (step.id, *(action.id for action in step.actions))
        if signature == self._rendered:
            return
        self._rendered = signature
        for child in self.frame.winfo_children():
            child.destroy()
        self.buttons.clear()
        for column, action in enumerate(step.actions):
            button = self.toolkit.ttk.Button(
                self.frame,
                text=action.label,
                command=lambda a=action.id: self._on_trigger(step.id, a),
            )
            button.grid(row=0, column=column, padx=(0, 8))
            self.buttons[action.id] = button

    def paint(self, step, overview, *, busy: bool) -> None:
        ready = step.precondition(overview).ready
        for action in step.actions:
            usable = ready and action.enabled(overview) and not busy
            self.buttons[action.id].configure(state="normal" if usable else "disabled")

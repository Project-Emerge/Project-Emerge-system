"""The calibration window: header, step sidebar, step panel and console."""

from __future__ import annotations

from pathlib import Path

from ....gui.toolkit import Ticker, TkToolkit
from ....gui.widgets import LogPane, PathRow
from .. import presentation
from ..controller import CalibrationController
from .widgets import ActionBar, FieldForm, StatusTable, StepSidebar

REFRESH_MS = 200

# Which GuiSettings attribute each declared field writes to, and how to read it.
FIELD_BINDINGS = {
    "board_format": ("board_format", str),
    "board_output": ("board_output", Path),
    "photo_root": ("photo_root", Path),
    "reference_markers": ("reference_markers_path", Path),
    "allow_low_quality": ("allow_low_quality", bool),
}


class CalibrationWindow:
    """Assembles the widgets and runs the refresh tick."""

    def __init__(self, toolkit: TkToolkit, controller: CalibrationController) -> None:
        self.toolkit = toolkit
        self.controller = controller
        tk, ttk = toolkit.tk, toolkit.ttk

        self.root = tk.Tk()
        self.root.title(presentation.WINDOW_TITLE)
        self.root.geometry("1100x760")
        self.root.minsize(900, 620)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, padding=(8, 8, 8, 0))
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(1, weight=1)
        self.config_var = tk.StringVar(
            value="" if controller.settings.config_path is None
            else str(controller.settings.config_path)
        )
        PathRow(
            header,
            toolkit,
            row=0,
            label=presentation.HEADER_CONFIG,
            variable=self.config_var,
            directory=False,
            browse_label=presentation.BROWSE_BUTTON,
            all_files_label=presentation.ALL_FILES,
        )
        buttons = ttk.Frame(header)
        buttons.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Button(buttons, text=presentation.REFRESH_BUTTON, command=self.reload).pack(
            side="left"
        )
        self.cancel_button = ttk.Button(
            buttons, text=presentation.CANCEL_BUTTON, command=self.cancel, state="disabled"
        )
        self.cancel_button.pack(side="left", padx=(8, 0))

        self.sidebar = StepSidebar(self.root, toolkit, on_select=self.select_step)
        self.sidebar.build(controller.steps)
        self.sidebar.frame.grid(row=1, column=0, rowspan=2, sticky="nsw")

        panel = ttk.Frame(self.root, padding=8)
        panel.grid(row=1, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)
        self.title_label = ttk.Label(panel, text="", font=("TkDefaultFont", 12, "bold"))
        self.title_label.grid(row=0, column=0, sticky="w")
        self.summary_label = ttk.Label(panel, text="", wraplength=700)
        self.summary_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.hint_label = ttk.Label(panel, text="", foreground="#b06a3c", wraplength=700)
        self.hint_label.grid(row=2, column=0, sticky="w", pady=(4, 6))
        self.table = StatusTable(panel, toolkit)
        self.table.frame.grid(row=3, column=0, sticky="nsew")
        self.form = FieldForm(panel, toolkit)
        self.form.frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.actions = ActionBar(panel, toolkit, on_trigger=self.trigger)
        self.actions.frame.grid(row=5, column=0, sticky="w", pady=(10, 0))

        self.console = LogPane(self.root, toolkit, clear_label=presentation.CLEAR_BUTTON)
        self.console.frame.grid(row=2, column=1, sticky="nsew", padx=8, pady=(4, 8))

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._stopped = False
        self.ticker = Ticker(self.root, REFRESH_MS, self.tick)
        self.reload()
        self.paint()
        self.ticker.start()

    # ---------------------------------------------------------------- commands
    def _apply_fields(self) -> None:
        """Push the step's form back into the settings before anything runs."""
        for name, value in self.form.values().items():
            binding = FIELD_BINDINGS.get(name)
            if binding is None:
                continue
            attribute, convert = binding
            if convert is Path:
                text = str(value).strip()
                setattr(self.controller.settings, attribute, Path(text) if text else None)
            else:
                setattr(self.controller.settings, attribute, convert(value))

    def reload(self) -> None:
        text = self.config_var.get().strip()
        self.controller.set_config_path(Path(text) if text else None)

    def select_step(self, step_id: str) -> None:
        self.controller.select_step(step_id)
        self.paint()

    def trigger(self, step_id: str, action_id: str) -> None:
        self._apply_fields()
        self.controller.trigger(step_id, action_id)

    def cancel(self) -> None:
        self.controller.cancel()

    # -------------------------------------------------------------------- tick
    def tick(self) -> None:
        lines, notices = self.controller.poll()
        self.console.extend(lines)
        self.console.extend(presentation.format_notice(notice) for notice in notices)
        self.paint()

    def paint(self) -> None:
        controller = self.controller
        step = controller.current_step
        overview = controller.overview
        self.sidebar.paint(controller.steps, overview, step.id)
        self.title_label.configure(text=f"{step.index}. {step.title}")
        self.summary_label.configure(text=step.summary)
        readiness = controller.readiness(step)
        self.hint_label.configure(text="" if readiness.ready else readiness.reason)
        self.table.show(step.table)
        if step.table != "none":
            self.table.paint(presentation.table_rows(step.table, overview))
        self.form.show(step.fields)
        self.actions.show(step)
        self.actions.paint(step, overview, busy=controller.busy)
        self.cancel_button.configure(state="normal" if controller.busy else "disabled")

    # ------------------------------------------------------------------- close
    def shutdown(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.ticker.stop()
        self.controller.shutdown()

    def request_stop(self) -> None:
        self.root.quit()

    def on_close(self) -> None:
        self.shutdown()
        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

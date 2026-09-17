"""The Tk window. Builds widgets, forwards clicks, and paints what it is given.

Every component creates its own widgets in its own ``__init__``, which is what
replaced the single 100-line layout method: nothing here reaches into a widget a
different object made, and no branch here decides anything about the deployment.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...gui.toolkit import Ticker, TkToolkit
from ...gui.widgets import Indicator, LogPane, PathRow
from ...gui.world_view import WorldCanvas
from ...monitoring.deployment import CameraRow
from . import presenters, strings
from .controller import ServerPanelController, options_from_fields
from .models import LauncherFields, PanelSnapshot
from .supervisor import ServerLaunchOptions

REFRESH_MS = 250


class LauncherPanel:
    """The launch form: broker, paths, flags and the two action buttons."""

    def __init__(
        self,
        parent: Any,
        toolkit: TkToolkit,
        *,
        options: ServerLaunchOptions,
        on_reconnect,
        on_start,
        on_stop,
    ) -> None:
        tk, ttk = toolkit.tk, toolkit.ttk
        self.frame = ttk.LabelFrame(parent, text=strings.LAUNCHER_FRAME, padding=8)
        for column in (1, 4):
            self.frame.columnconfigure(column, weight=1)

        self.host_var = tk.StringVar(value=options.mqtt_host)
        self.port_var = tk.StringVar(value=str(options.mqtt_port))
        self.config_var = tk.StringVar(value="" if options.config is None else str(options.config))
        self.calibrations_var = tk.StringVar(value=str(options.calibrations))
        self.cache_var = tk.StringVar(value=str(options.cache))
        self.debug_var = tk.BooleanVar(value=options.debug)
        self.no_mqtt_var = tk.BooleanVar(value=options.no_mqtt)
        self.verbose_var = tk.BooleanVar(value=options.verbose)

        ttk.Label(self.frame, text=strings.MQTT_HOST_LABEL).grid(row=0, column=0, sticky="w")
        ttk.Entry(self.frame, textvariable=self.host_var).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Label(self.frame, text=strings.MQTT_PORT_LABEL).grid(row=0, column=2, sticky="w")
        ttk.Entry(self.frame, textvariable=self.port_var, width=8).grid(row=0, column=3, sticky="w")
        ttk.Button(self.frame, text=strings.RECONNECT_BUTTON, command=on_reconnect).grid(
            row=0, column=4, sticky="e"
        )

        rows = (
            (1, strings.CONFIG_LABEL, self.config_var, False),
            (2, strings.CALIBRATIONS_LABEL, self.calibrations_var, True),
            (3, strings.CACHE_LABEL, self.cache_var, False),
        )
        for row, label, variable, directory in rows:
            PathRow(
                self.frame,
                toolkit,
                row=row,
                label=label,
                variable=variable,
                directory=directory,
                browse_label=strings.BROWSE_BUTTON,
                all_files_label=strings.ALL_FILES,
            )

        flags = ttk.Frame(self.frame)
        flags.grid(row=4, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Checkbutton(flags, text="--debug", variable=self.debug_var).pack(side="left")
        ttk.Checkbutton(flags, text="--no-mqtt", variable=self.no_mqtt_var).pack(
            side="left", padx=(12, 0)
        )
        ttk.Checkbutton(flags, text="--verbose", variable=self.verbose_var).pack(
            side="left", padx=(12, 0)
        )

        actions = ttk.Frame(self.frame)
        actions.grid(row=5, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(actions, text=strings.START_BUTTON, command=on_start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            actions, text=strings.STOP_BUTTON, command=on_stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(8, 0))

        self.server_indicator = Indicator(actions, toolkit, initial=strings.SERVER_STOPPED)
        self.server_indicator.pack(side="left", padx=(16, 0))
        self.broker_indicator = Indicator(actions, toolkit, initial=strings.BROKER_DISCONNECTED)
        self.broker_indicator.pack(side="left", padx=(16, 0))
        self.fusion_indicator = Indicator(
            actions,
            toolkit,
            initial=strings.FUSION_SUMMARY.format(poses=0, tags=strings.UNKNOWN),
        )
        self.fusion_indicator.pack(side="left", padx=(16, 0))

    def fields(self) -> LauncherFields:
        """The form exactly as typed; validation belongs to the controller."""
        return LauncherFields(
            config=self.config_var.get(),
            calibrations=self.calibrations_var.get(),
            cache=self.cache_var.get(),
            host=self.host_var.get(),
            port=self.port_var.get(),
            debug=self.debug_var.get(),
            no_mqtt=self.no_mqtt_var.get(),
            verbose=self.verbose_var.get(),
        )

    def set_port(self, port: int) -> None:
        self.port_var.set(str(port))

    def paint(self, snapshot: PanelSnapshot) -> None:
        self.start_button.configure(state="disabled" if snapshot.process_running else "normal")
        self.stop_button.configure(state="normal" if snapshot.process_running else "disabled")
        texts = presenters.indicator_texts(snapshot)
        self.server_indicator.set(texts.server)
        self.broker_indicator.set(texts.broker)
        self.fusion_indicator.set(texts.fusion)


class RosterTable:
    """The per-camera table, updated by diffing rather than rebuilt."""

    def __init__(self, parent: Any, toolkit: TkToolkit) -> None:
        ttk = toolkit.ttk
        self.frame = ttk.LabelFrame(parent, text=strings.ROSTER_FRAME, padding=8)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        columns = tuple(column for column, _, _ in strings.ROSTER_COLUMNS)
        self.table = ttk.Treeview(self.frame, columns=columns, show="headings", height=6)
        for column, title, width in strings.ROSTER_COLUMNS:
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w", stretch=column == "issues")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self.frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

    def paint(self, rows: Sequence[CameraRow]) -> None:
        existing = set(self.table.get_children())
        for row in rows:
            values = presenters.roster_values(row)
            if row.camera_id in existing:
                current = tuple(self.table.item(row.camera_id, "values"))
                if current != tuple(str(value) for value in values):
                    self.table.item(row.camera_id, values=values)
                existing.discard(row.camera_id)
            else:
                self.table.insert("", "end", iid=row.camera_id, values=values)
        for stale in existing:
            self.table.delete(stale)


class WorldTab:
    """The arena canvas plus its legend."""

    def __init__(self, parent: Any, toolkit: TkToolkit, model) -> None:
        ttk = toolkit.ttk
        self.frame = ttk.Frame(parent, padding=6)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.canvas = WorldCanvas(self.frame, model, toolkit.tk)
        self.canvas.widget.grid(row=0, column=0, sticky="nsew")
        ttk.Label(self.frame, text=strings.WORLD_CAPTION).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )

    def redraw(self, now_ns: int) -> None:
        self.canvas.redraw(now_ns)


class ServerPanelWindow:
    """The window: wires the components to the controller and runs the tick."""

    def __init__(self, toolkit: TkToolkit, controller: ServerPanelController) -> None:
        self.toolkit = toolkit
        self.controller = controller
        self.root = toolkit.tk.Tk()
        self.root.title(strings.WINDOW_TITLE)
        self.root.geometry("1040x720")
        self.root.minsize(820, 560)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=2)

        self.launcher = LauncherPanel(
            self.root,
            toolkit,
            options=controller.options,
            on_reconnect=self.reconnect,
            on_start=self.start_server,
            on_stop=self.stop_server,
        )
        self.launcher.frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self.roster = RosterTable(self.root, toolkit)
        self.roster.frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)

        notebook = toolkit.ttk.Notebook(self.root)
        notebook.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))
        self.world = WorldTab(notebook, toolkit, controller.world)
        notebook.add(self.world.frame, text=strings.WORLD_TAB)
        self.console = LogPane(notebook, toolkit, clear_label=strings.CLEAR_BUTTON)
        notebook.add(self.console.frame, text=strings.CONSOLE_TAB)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._stopped = False
        self.ticker = Ticker(self.root, REFRESH_MS, self.tick)
        self.reconnect()
        self.ticker.start()

    # ---------------------------------------------------------------- commands
    def _options(self) -> ServerLaunchOptions:
        options, notices = options_from_fields(self.launcher.fields(), self.controller.options)
        self._report(notices)
        self.launcher.set_port(options.mqtt_port)
        return options

    def reconnect(self) -> None:
        self._report(self.controller.connect(self._options()))

    def start_server(self) -> None:
        self._report(self.controller.start_server(self._options()))

    def stop_server(self) -> None:
        self._report(self.controller.stop_server())

    def _report(self, notices) -> None:
        self.console.extend(presenters.format_notice(notice) for notice in notices)

    # -------------------------------------------------------------------- tick
    def tick(self) -> None:
        snapshot, lines, notices = self.controller.tick()
        self.console.extend(lines)
        self._report(notices)
        self.launcher.paint(snapshot)
        self.roster.paint(snapshot.rows)
        self.world.redraw(snapshot.now_ns)

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

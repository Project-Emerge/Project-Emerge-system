"""Tkinter control panel that launches the fusion server and watches a distributed deployment.

The distributed runtime is a set of independent processes (one ``vision-node`` per
camera PC plus one ``vision-server``), so the usual debugging question is not "what
does the fusion compute" but "who is actually talking to whom". This panel starts and
stops the local ``vision-server`` process, streams its console output, and renders the
roster exactly as the two sides see it: what each node claims to publish and what the
coordinator claims to receive.
"""

from __future__ import annotations

import argparse
import os
import signal
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from ...core.config import AppConfig, initial_config
from ...gui.world_view import VIEWPORT_QUANTUM_M, WorldCanvas, fit_viewport
from ...monitoring.deployment import PRESENCE_TIMEOUT_NS, CameraRow, DeploymentStatus
from ...monitoring.listener import StatusMonitor
from ...monitoring.world import STALE_POSE_NS, WORLD_MARGIN_M, TrackedTag, WorldModel
from ...pipeline.calibration_store import CalibrationStore
from ...transport.diagnostics import configure_diagnostics
from ...transport.mqtt import MqttSettings
from ...transport.payloads import PoseUpdate, parse
from . import strings
from .presenters import describe_issues, describe_message
from .supervisor import (
    DEFAULT_CACHE,
    DEFAULT_CALIBRATIONS,
    MAX_LOG_LINES,
    STOP_TIMEOUT_S,
    ServerLaunchOptions,
    ServerProcess,
    build_server_command,
    build_server_environment,
)

__all__ = [
    "DEFAULT_CACHE",
    "DEFAULT_CALIBRATIONS",
    "MAX_LOG_LINES",
    "PRESENCE_TIMEOUT_NS",
    "STALE_POSE_NS",
    "STOP_TIMEOUT_S",
    "VIEWPORT_QUANTUM_M",
    "WorldCanvas",
    "WORLD_MARGIN_M",
    "CameraRow",
    "DeploymentStatus",
    "ServerLaunchOptions",
    "ServerProcess",
    "StatusMonitor",
    "TrackedTag",
    "WorldModel",
    "build_server_command",
    "build_server_environment",
    "fit_viewport",
    "roster_from_config",
    "server_gui_main",
]

REFRESH_MS = 250










def roster_from_config(config_path: Path | None, cache_path: Path) -> tuple[AppConfig, list[str]]:
    config = initial_config(config_path, cache_path)
    return config, [camera.id for camera in config.cameras]


def _format_flag(value: bool) -> str:
    return "●" if value else "○"


def _format_optional(value: object) -> str:
    return "—" if value is None else str(value)



class ServerGuiApp:
    """The Tk window: launcher on top, roster in the middle, server console at the bottom."""

    def __init__(self, options: ServerLaunchOptions, cameras: Sequence[str] = ()) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self.options = options
        self.process = ServerProcess()
        self.monitor: StatusMonitor | None = None
        self.status = DeploymentStatus(cameras)
        self.world = WorldModel()
        self.app_config: AppConfig | None = None
        self.calibration_store: CalibrationStore | None = None
        self.root = tk.Tk()
        self.root.title(strings.WINDOW_TITLE)
        self.root.geometry("1040x720")
        self.root.minsize(820, 560)
        self.host_var = tk.StringVar(value=options.mqtt_host)
        self.port_var = tk.StringVar(value=str(options.mqtt_port))
        self.config_var = tk.StringVar(value="" if options.config is None else str(options.config))
        self.calibrations_var = tk.StringVar(value=str(options.calibrations))
        self.cache_var = tk.StringVar(value=str(options.cache))
        self.debug_var = tk.BooleanVar(value=options.debug)
        self.no_mqtt_var = tk.BooleanVar(value=options.no_mqtt)
        self.verbose_var = tk.BooleanVar(value=options.verbose)
        self.server_state_var = tk.StringVar(value=strings.SERVER_STOPPED)
        self.broker_state_var = tk.StringVar(value=strings.BROKER_DISCONNECTED)
        self.fusion_state_var = tk.StringVar(
            value=strings.FUSION_SUMMARY.format(poses=0, tags=strings.UNKNOWN)
        )
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.connect_monitor()
        self.root.after(REFRESH_MS, self._refresh)

    # ------------------------------------------------------------------ layout
    def _build_layout(self) -> None:
        ttk = self._ttk
        tk = self._tk
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        root.rowconfigure(2, weight=2)

        launcher = ttk.LabelFrame(root, text=strings.LAUNCHER_FRAME, padding=8)
        launcher.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        for column in (1, 4):
            launcher.columnconfigure(column, weight=1)

        ttk.Label(launcher, text=strings.MQTT_HOST_LABEL).grid(row=0, column=0, sticky="w")
        ttk.Entry(launcher, textvariable=self.host_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(launcher, text=strings.MQTT_PORT_LABEL).grid(row=0, column=2, sticky="w")
        ttk.Entry(launcher, textvariable=self.port_var, width=8).grid(row=0, column=3, sticky="w")
        ttk.Button(launcher, text=strings.RECONNECT_BUTTON, command=self.connect_monitor).grid(
            row=0, column=4, sticky="e"
        )

        self._path_row(launcher, 1, strings.CONFIG_LABEL, self.config_var, directory=False)
        self._path_row(
            launcher, 2, strings.CALIBRATIONS_LABEL, self.calibrations_var, directory=True
        )
        self._path_row(launcher, 3, strings.CACHE_LABEL, self.cache_var, directory=False)

        flags = ttk.Frame(launcher)
        flags.grid(row=4, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Checkbutton(flags, text="--debug (vista world)", variable=self.debug_var).pack(
            side="left"
        )
        ttk.Checkbutton(flags, text="--no-mqtt", variable=self.no_mqtt_var).pack(
            side="left", padx=12
        )
        ttk.Checkbutton(flags, text="--verbose", variable=self.verbose_var).pack(side="left")

        actions = ttk.Frame(launcher)
        actions.grid(row=5, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(
            actions, text=strings.START_BUTTON, command=self.start_server
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            actions, text=strings.STOP_BUTTON, command=self.stop_server, state="disabled"
        )
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(actions, textvariable=self.server_state_var).pack(side="left", padx=12)
        ttk.Label(actions, textvariable=self.broker_state_var).pack(side="left", padx=12)
        ttk.Label(actions, textvariable=self.fusion_state_var).pack(side="right")

        table_frame = ttk.LabelFrame(root, text=strings.ROSTER_FRAME, padding=8)
        table_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = tuple(column for column, _, _ in strings.ROSTER_COLUMNS)
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=6)
        for column, title, width in strings.ROSTER_COLUMNS:
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w", stretch=column == "issues")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        notebook = ttk.Notebook(root)
        notebook.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))

        world_frame = ttk.Frame(notebook, padding=6)
        world_frame.columnconfigure(0, weight=1)
        world_frame.rowconfigure(0, weight=1)
        self.world_canvas = WorldCanvas(world_frame, self.world, tk)
        self.world_canvas.widget.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            world_frame,
            text=strings.WORLD_CAPTION,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        notebook.add(world_frame, text=strings.WORLD_TAB)

        log_frame = ttk.Frame(notebook, padding=6)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="none", height=12, state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")
        ttk.Button(log_frame, text=strings.CLEAR_BUTTON, command=self.clear_log).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        notebook.add(log_frame, text=strings.CONSOLE_TAB)

    def _path_row(self, parent, row: int, label: str, variable, directory: bool) -> None:
        ttk = self._ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, columnspan=3, sticky="ew", padx=4, pady=2
        )
        ttk.Button(
            parent, text=strings.BROWSE_BUTTON, command=lambda: self._browse(variable, directory)
        ).grid(row=row, column=4, sticky="e", pady=2)

    def _browse(self, variable, directory: bool) -> None:
        from tkinter import filedialog

        current = variable.get()
        initial = str(Path(current).parent if current and not directory else current or ".")
        chosen = (
            filedialog.askdirectory(initialdir=initial)
            if directory
            else filedialog.askopenfilename(
                initialdir=initial, filetypes=[("JSON", "*.json"), (strings.ALL_FILES, "*.*")]
            )
        )
        if chosen:
            variable.set(chosen)

    # ----------------------------------------------------------------- actions
    def current_options(self) -> ServerLaunchOptions:
        config = self.config_var.get().strip()
        try:
            port = int(self.port_var.get())
        except ValueError:
            port = self.options.mqtt_port
            self.port_var.set(str(port))
            self.append_log(strings.INVALID_PORT)
        return ServerLaunchOptions(
            config=Path(config) if config else None,
            calibrations=Path(self.calibrations_var.get().strip() or DEFAULT_CALIBRATIONS),
            cache=Path(self.cache_var.get().strip() or DEFAULT_CACHE),
            mqtt_host=self.host_var.get().strip() or "localhost",
            mqtt_port=port,
            debug=self.debug_var.get(),
            no_mqtt=self.no_mqtt_var.get(),
            verbose=self.verbose_var.get(),
        )

    def connect_monitor(self) -> None:
        """(Re)subscribe to the deployment topics implied by the current config."""
        options = self.current_options()
        self.options = options
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
        try:
            config, cameras = roster_from_config(options.config, options.cache)
        except (OSError, ValueError) as error:
            self.append_log(strings.CONFIG_UNREADABLE.format(error=error))
            return
        self.status = DeploymentStatus(cameras)
        self.app_config = config
        self.world.reset()
        self.calibration_store = CalibrationStore(options.calibrations)
        self.world.update_scene(config, self.calibration_store.calibrations)
        settings = MqttSettings.from_environment()
        settings = MqttSettings(
            host=options.mqtt_host,
            port=options.mqtt_port,
            username=settings.username,
            password=settings.password,
            tls=settings.tls,
        )
        self.monitor = StatusMonitor(config.base_topic, settings)
        self.monitor.start()
        self.append_log(
            strings.LISTENING.format(
                topic=config.base_topic,
                cameras=", ".join(cameras) or strings.NO_CAMERAS,
            )
        )

    def start_server(self) -> None:
        if self.process.running:
            return
        options = self.current_options()
        self.options = options
        if self.status.coordinator_online(time.monotonic_ns()):
            self.append_log(
                strings.SECOND_COORDINATOR
            )
        try:
            self.process.start(options)
        except (OSError, RuntimeError) as error:
            self.append_log(strings.START_FAILED.format(error=error))
            return
        # Reconnecting resets the roster view, so only do it when the launcher fields
        # no longer match the broker the panel is listening to.
        if self.monitor is None or (self.monitor.settings.host, self.monitor.settings.port) != (
            options.mqtt_host,
            options.mqtt_port,
        ):
            self.connect_monitor()

    def stop_server(self) -> None:
        if not self.process.running:
            return
        self.append_log(strings.STOPPING)
        self.process.stop()

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        excess = int(self.log.index("end-1c").split(".")[0]) - MAX_LOG_LINES
        if excess > 0:
            self.log.delete("1.0", f"{excess + 1}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ----------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        # One clock reading for the whole tick: presence, staleness and the drawn
        # frame must agree, and reading the clock per call let them disagree.
        now_ns = time.monotonic_ns()
        for line in self.process.drain_logs():
            self.append_log(line)
        if self.monitor is not None:
            for topic, body in self.monitor.drain():
                message = parse(topic, body)
                if message is None:
                    continue
                if isinstance(message, PoseUpdate):
                    self.world.apply(message, now_ns)
                    continue
                self.status.apply(message, now_ns)
                line = describe_message(message)
                if line:
                    self.append_log(line)
        self.world.expire(now_ns)
        # Extrinsics change while calibrating a node: the store throttles the rescan
        # itself, so polling every tick costs nothing.
        if self.calibration_store is not None and self.calibration_store.reload_if_changed():
            self.world.update_scene(self.app_config, self.calibration_store.calibrations)
            self.append_log(strings.CALIBRATIONS_RELOADED)
        self._refresh_indicators(now_ns)
        self._refresh_table(now_ns)
        self.world_canvas.redraw(now_ns)
        self.root.after(REFRESH_MS, self._refresh)

    @staticmethod
    def _set_if_changed(variable, value: str) -> None:
        if variable.get() != value:
            variable.set(value)

    def _refresh_indicators(self, now_ns: int) -> None:
        # Widgets are only touched when their value actually changes: reassigning
        # the same text four times a second repaints them for nothing.
        running = self.process.running
        for button, state in (
            (self.start_button, "disabled" if running else "normal"),
            (self.stop_button, "normal" if running else "disabled"),
        ):
            if str(button.cget("state")) != state:
                button.configure(state=state)
        if running:
            server_state = strings.SERVER_RUNNING.format(pid=self.process.pid)
        elif self.process.exit_code is None:
            server_state = strings.SERVER_STOPPED
        else:
            server_state = strings.SERVER_EXITED.format(code=self.process.exit_code)
        self._set_if_changed(self.server_state_var, server_state)
        if self.monitor is None:
            broker_state = strings.BROKER_DISCONNECTED
        elif self.monitor.connected.is_set():
            fusion = (
                strings.FUSION_ACTIVE
                if self.status.coordinator_online(now_ns)
                else strings.FUSION_WAITING
            )
            broker_state = (
                strings.BROKER_CONNECTED.format(
                    host=self.monitor.settings.host, port=self.monitor.settings.port
                )
                + f" · {fusion}"
            )
        else:
            broker_state = (
                strings.BROKER_ERROR.format(error=self.monitor.error)
                if self.monitor.error
                else strings.BROKER_CONNECTING
            )
        self._set_if_changed(self.broker_state_var, broker_state)
        tags = ", ".join(str(tag) for tag in self.status.tracked_tags) or strings.UNKNOWN
        self._set_if_changed(
            self.fusion_state_var,
            strings.FUSION_SUMMARY.format(poses=self.status.poses_published, tags=tags),
        )

    def _refresh_table(self, now_ns: int) -> None:
        rows = self.status.rows(now_ns)
        existing = set(self.table.get_children())
        for row in rows:
            values = (
                row.camera_id,
                _format_flag(row.node_online),
                _format_optional(row.node_observations),
                _format_flag(row.server_online),
                _format_optional(row.observations_received),
                _format_optional(row.age_ms),
                strings.UNKNOWN
                if row.calibrated is None
                else (strings.YES if row.calibrated else strings.NO),
                describe_issues(row.issues),
            )
            if row.camera_id in existing:
                if tuple(self.table.item(row.camera_id, "values")) != tuple(
                    str(value) for value in values
                ):
                    self.table.item(row.camera_id, values=values)
                existing.discard(row.camera_id)
            else:
                self.table.insert("", "end", iid=row.camera_id, values=values)
        for stale in existing:
            self.table.delete(stale)

    def shutdown(self) -> None:
        """Stop the child server and the MQTT listener; safe to call more than once.

        The server runs in its own session so that a Ctrl-C in the launching
        terminal cannot kill it behind the panel's back; the flip side is that the
        panel must always stop it itself, including when it exits via a signal.
        """
        if self.process.running:
            self.process.stop()
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None

    def request_stop(self) -> None:
        """Leave the main loop from a signal handler; cleanup happens in `run`."""
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


def server_gui_main() -> None:
    parser = argparse.ArgumentParser(
        description=strings.CLI_DESCRIPTION
    )
    parser.add_argument("--config", type=Path, help=strings.CLI_CONFIG_HELP)
    parser.add_argument("--calibrations", type=Path, default=DEFAULT_CALIBRATIONS)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--mqtt-host",
        default=os.getenv("VISION_MQTT_HOST", "localhost"),
        help=strings.CLI_MQTT_HOST_HELP,
    )
    parser.add_argument(
        "--mqtt-port", type=int, default=int(os.getenv("VISION_MQTT_PORT", "1883"))
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--no-debug-window",
        action="store_true",
        help=strings.CLI_NO_DEBUG_HELP,
    )
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics("vision-server-gui", verbose=args.verbose)
    print(strings.DIAGNOSTIC_LOG.format(path=diagnostic_path))
    options = ServerLaunchOptions(
        config=args.config,
        calibrations=args.calibrations,
        cache=args.cache,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        debug=not args.no_debug_window,
        verbose=args.verbose,
    )
    cameras: Iterable[str] = ()
    try:
        _, cameras = roster_from_config(options.config, options.cache)
    except (OSError, ValueError) as error:
        print(strings.CONFIG_UNREADABLE.format(error=error))
    try:
        import tkinter
    except ImportError:
        parser.error(strings.TKINTER_MISSING)
    try:
        app = ServerGuiApp(options, list(cameras))
    except tkinter.TclError as error:
        parser.error(strings.DISPLAY_UNAVAILABLE.format(error=error))
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda signum, frame: app.request_stop())
    app.run()


if __name__ == "__main__":
    server_gui_main()

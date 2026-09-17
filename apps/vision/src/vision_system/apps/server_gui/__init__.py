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
import math
import os
import signal
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from ...core.config import AppConfig, initial_config
from ...monitoring.deployment import PRESENCE_TIMEOUT_NS, CameraRow, DeploymentStatus
from ...monitoring.listener import StatusMonitor
from ...monitoring.world import STALE_POSE_NS, WORLD_MARGIN_M, TrackedTag, WorldModel
from ...pipeline.calibration_store import CalibrationStore
from ...transport.diagnostics import configure_diagnostics
from ...transport.mqtt import MqttSettings
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
CALIBRATION_POLL_S = 2.0
# The drawn viewport snaps to this grid so that a moving tag does not rescale
# the whole scene on every frame.
VIEWPORT_QUANTUM_M = 0.5










def roster_from_config(config_path: Path | None, cache_path: Path) -> tuple[AppConfig, list[str]]:
    config = initial_config(config_path, cache_path)
    return config, [camera.id for camera in config.cameras]


def _format_flag(value: bool) -> str:
    return "●" if value else "○"


def _format_optional(value: object) -> str:
    return "—" if value is None else str(value)


def fit_viewport(
    current: tuple[float, float, float, float] | None,
    content: tuple[float, float, float, float],
    quantum: float = VIEWPORT_QUANTUM_M,
) -> tuple[float, float, float, float]:
    """Pick a viewport that stays put while the tracked tags move.

    Deriving the extent from the tag positions on every frame rescales the whole
    scene a few times per second, which reads as flicker: the viewport is kept as
    long as the content still fits and is not absurdly smaller than the view.
    """
    min_x, min_y, max_x, max_y = content
    candidate = (
        math.floor(min_x / quantum) * quantum,
        math.floor(min_y / quantum) * quantum,
        max(math.ceil(max_x / quantum) * quantum, math.floor(min_x / quantum) * quantum + quantum),
        max(math.ceil(max_y / quantum) * quantum, math.floor(min_y / quantum) * quantum + quantum),
    )
    if current is None:
        return candidate
    fits = (
        current[0] <= min_x
        and current[1] <= min_y
        and current[2] >= max_x
        and current[3] >= max_y
    )
    oversized = (current[2] - current[0]) > 2 * (candidate[2] - candidate[0]) or (
        current[3] - current[1]
    ) > 2 * (candidate[3] - candidate[1])
    return current if fits and not oversized else candidate


class WorldCanvas:
    """Top-down 2D view of the arena: cameras, reference markers and tracked tags.

    Drawing is incremental. The static layer (grid, axes, cameras, references) is
    rebuilt only when the viewport, the window size or the scene changes, and each
    tag keeps its own canvas items which are moved with ``coords``; wiping the
    canvas on every tick made the view flash.
    """

    BACKGROUND = "#0f1216"
    GRID = "#1d242b"
    AXIS_X = "#e2553d"
    AXIS_Y = "#5bbd6a"
    REFERENCE = "#b07cd6"
    CAMERA = "#f0a33c"
    TRAIL = "#3d4a56"
    TAG_FRESH = "#4ec9e0"
    TAG_STALE = "#7a848c"
    TEXT = "#c8d2da"
    GRID_STEPS_M = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0)
    MIN_GRID_PX = 55
    MARGIN_PX = 28
    STATIC = "static"

    def __init__(self, parent, model: WorldModel, tk_module) -> None:
        self.model = model
        self.canvas = tk_module.Canvas(
            parent, background=self.BACKGROUND, highlightthickness=0, width=640, height=360
        )
        self._scale_px_m = 1.0
        self._center = (0.0, 0.0)
        self._size = (0, 0)
        self._viewport: tuple[float, float, float, float] | None = None
        self._static_signature: tuple | None = None
        self._tag_items: dict[int, dict[str, int]] = {}
        self._summary_item: int | None = None

    @property
    def widget(self):
        return self.canvas

    def _to_pixels(self, x_m: float, y_m: float) -> tuple[float, float]:
        width, height = self._size
        center_x, center_y = self._center
        # World Y points "up" in the arena frame, canvas Y grows downwards.
        return (
            width / 2 + (x_m - center_x) * self._scale_px_m,
            height / 2 - (y_m - center_y) * self._scale_px_m,
        )

    def redraw(self, now_ns: int | None = None) -> None:
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 2 * self.MARGIN_PX or height < 2 * self.MARGIN_PX:
            return
        self._size = (width, height)
        viewport = fit_viewport(self._viewport, self.model.bounds())
        self._viewport = viewport
        min_x, min_y, max_x, max_y = viewport
        self._scale_px_m = min(
            (width - 2 * self.MARGIN_PX) / max(max_x - min_x, 0.5),
            (height - 2 * self.MARGIN_PX) / max(max_y - min_y, 0.5),
        )
        self._center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        signature = (
            viewport,
            width,
            height,
            tuple(self.model.references),
            tuple(sorted(self.model.cameras.items())),
        )
        if signature != self._static_signature:
            self.canvas.delete(self.STATIC)
            self._draw_static(*viewport)
            self.canvas.tag_lower(self.STATIC)
            self._static_signature = signature
        tags = self.model.tags(now_ns)
        self._sync_tags(tags, now_ns)
        self._sync_summary(tags, now_ns)

    # ------------------------------------------------------------ static layer
    def _grid_step_m(self) -> float:
        for step in self.GRID_STEPS_M:
            if step * self._scale_px_m >= self.MIN_GRID_PX:
                return step
        return self.GRID_STEPS_M[-1]

    def _draw_static(self, min_x: float, min_y: float, max_x: float, max_y: float) -> None:
        self._draw_grid(min_x, min_y, max_x, max_y)
        self._draw_axes()
        self._draw_references()
        self._draw_cameras()
        self._draw_scale_bar()

    def _draw_grid(self, min_x: float, min_y: float, max_x: float, max_y: float) -> None:
        step = self._grid_step_m()
        line_x = math.floor(min_x / step) * step
        while line_x <= max_x:
            x_px, _ = self._to_pixels(line_x, 0.0)
            self.canvas.create_line(
                x_px, 0, x_px, self._size[1], fill=self.GRID, tags=self.STATIC
            )
            line_x += step
        line_y = math.floor(min_y / step) * step
        while line_y <= max_y:
            _, y_px = self._to_pixels(0.0, line_y)
            self.canvas.create_line(
                0, y_px, self._size[0], y_px, fill=self.GRID, tags=self.STATIC
            )
            line_y += step

    def _draw_axes(self) -> None:
        origin = self._to_pixels(0.0, 0.0)
        for end, color, label in (
            ((0.5, 0.0), self.AXIS_X, "X"),
            ((0.0, 0.5), self.AXIS_Y, "Y"),
        ):
            tip = self._to_pixels(*end)
            self.canvas.create_line(
                *origin, *tip, fill=color, width=2, arrow="last", tags=self.STATIC
            )
            self.canvas.create_text(
                *tip, text=label, fill=color, anchor="sw", font=("TkDefaultFont", 8),
                tags=self.STATIC,
            )

    def _draw_references(self) -> None:
        for marker_id, x_m, y_m in self.model.references:
            x_px, y_px = self._to_pixels(x_m, y_m)
            self.canvas.create_polygon(
                x_px, y_px - 5, x_px + 5, y_px, x_px, y_px + 5, x_px - 5, y_px,
                outline=self.REFERENCE, fill="", width=1, tags=self.STATIC,
            )
            self.canvas.create_text(
                x_px + 8, y_px, text=f"R{marker_id}", fill=self.REFERENCE, anchor="w",
                font=("TkDefaultFont", 7), tags=self.STATIC,
            )

    def _draw_cameras(self) -> None:
        for camera_id, (x_m, y_m, heading) in sorted(self.model.cameras.items()):
            x_px, y_px = self._to_pixels(x_m, y_m)
            self.canvas.create_oval(
                x_px - 5, y_px - 5, x_px + 5, y_px + 5,
                outline=self.CAMERA, width=2, tags=self.STATIC,
            )
            tip = self._to_pixels(x_m + 0.4 * math.cos(heading), y_m + 0.4 * math.sin(heading))
            self.canvas.create_line(
                x_px, y_px, *tip, fill=self.CAMERA, width=1, arrow="last", tags=self.STATIC
            )
            self.canvas.create_text(
                x_px + 8, y_px - 8, text=camera_id, fill=self.CAMERA, anchor="w",
                font=("TkDefaultFont", 8), tags=self.STATIC,
            )

    def _draw_scale_bar(self) -> None:
        step = self._grid_step_m()
        bar_px = step * self._scale_px_m
        base_y = self._size[1] - 14
        self.canvas.create_line(
            12, base_y, 12 + bar_px, base_y, fill=self.TEXT, width=2, tags=self.STATIC
        )
        self.canvas.create_text(
            16 + bar_px, base_y, text=f"{step:g} m", fill=self.TEXT, anchor="w",
            font=("TkDefaultFont", 8), tags=self.STATIC,
        )

    # ----------------------------------------------------------- dynamic layer
    def _sync_tags(self, tags: Sequence[TrackedTag], now_ns: int) -> None:
        for tag in tags:
            items = self._tag_items.get(tag.tag_id) or self._create_tag_items()
            self._tag_items[tag.tag_id] = items
            stale = tag.stale(now_ns)
            color = self.TAG_STALE if stale else self.TAG_FRESH
            x_px, y_px = self._to_pixels(tag.x_m, tag.y_m)
            self.canvas.coords(items["body"], x_px - 7, y_px - 7, x_px + 7, y_px + 7)
            self.canvas.itemconfigure(
                items["body"], outline=color, fill="" if stale or tag.predicted else color
            )
            tip = self._to_pixels(
                tag.x_m + 0.3 * math.cos(tag.heading_rad),
                tag.y_m + 0.3 * math.sin(tag.heading_rad),
            )
            self.canvas.coords(items["heading"], x_px, y_px, *tip)
            self.canvas.itemconfigure(items["heading"], fill=color)
            suffix = " (ferma)" if stale else (" (predetta)" if tag.predicted else "")
            self.canvas.coords(items["label"], x_px + 10, y_px + 10)
            self.canvas.itemconfigure(
                items["label"],
                text=f"ID {tag.tag_id}  {tag.x_m:.2f}, {tag.y_m:.2f} m{suffix}",
                fill=color,
            )
            self._sync_trail(items["trail"], tag.tag_id, now_ns)
        for tag_id in set(self._tag_items) - {tag.tag_id for tag in tags}:
            for item in self._tag_items.pop(tag_id).values():
                self.canvas.delete(item)

    def _create_tag_items(self) -> dict[str, int]:
        return {
            "trail": self.canvas.create_line(0, 0, 0, 0, fill=self.TRAIL, width=1, state="hidden"),
            "body": self.canvas.create_oval(0, 0, 0, 0, width=2),
            "heading": self.canvas.create_line(0, 0, 0, 0, width=2, arrow="last"),
            "label": self.canvas.create_text(
                0, 0, anchor="w", font=("TkDefaultFont", 8), text=""
            ),
        }

    def _sync_trail(self, item: int, tag_id: int, now_ns: int) -> None:
        trail = self.model.trail(tag_id, now_ns)
        if len(trail) < 2:
            self.canvas.itemconfigure(item, state="hidden")
            return
        points: list[float] = []
        for x_m, y_m in trail:
            points.extend(self._to_pixels(x_m, y_m))
        self.canvas.coords(item, *points)
        self.canvas.itemconfigure(item, state="normal")

    def _sync_summary(self, tags: Sequence[TrackedTag], now_ns: int) -> None:
        live = sum(1 for tag in tags if not tag.stale(now_ns))
        text = (
            f"tag visibili: {live}/{len(tags)}"
            if tags
            else "nessuna posa ricevuta: il server pubblica su <base>/pose/<tag_id>"
        )
        if self._summary_item is None:
            self._summary_item = self.canvas.create_text(
                0, 14, fill=self.TEXT, anchor="e", font=("TkDefaultFont", 9)
            )
        self.canvas.coords(self._summary_item, self._size[0] - 12, 14)
        self.canvas.itemconfigure(self._summary_item, text=text)


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
        self.root.title("VisionSystem · server di fusione (modalità distribuita)")
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
        self.server_state_var = tk.StringVar(value="server: fermo")
        self.broker_state_var = tk.StringVar(value="broker: non collegato")
        self.fusion_state_var = tk.StringVar(value="pose pubblicate: 0 · tag: —")
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

        launcher = ttk.LabelFrame(root, text="Avvio server", padding=8)
        launcher.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        for column in (1, 4):
            launcher.columnconfigure(column, weight=1)

        ttk.Label(launcher, text="MQTT host").grid(row=0, column=0, sticky="w")
        ttk.Entry(launcher, textvariable=self.host_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(launcher, text="porta").grid(row=0, column=2, sticky="w")
        ttk.Entry(launcher, textvariable=self.port_var, width=8).grid(row=0, column=3, sticky="w")
        ttk.Button(launcher, text="Riconnetti", command=self.connect_monitor).grid(
            row=0, column=4, sticky="e"
        )

        self._path_row(launcher, 1, "config", self.config_var, directory=False)
        self._path_row(launcher, 2, "calibrations", self.calibrations_var, directory=True)
        self._path_row(launcher, 3, "cache stato", self.cache_var, directory=False)

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
        self.start_button = ttk.Button(actions, text="Avvia server", command=self.start_server)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            actions, text="Ferma server", command=self.stop_server, state="disabled"
        )
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(actions, textvariable=self.server_state_var).pack(side="left", padx=12)
        ttk.Label(actions, textvariable=self.broker_state_var).pack(side="left", padx=12)
        ttk.Label(actions, textvariable=self.fusion_state_var).pack(side="right")

        table_frame = ttk.LabelFrame(root, text="Camere del deployment", padding=8)
        table_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("camera", "nodo", "pubblicate", "server", "ricevute", "age", "calib", "note")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=6)
        headings = {
            "camera": ("Camera", 90),
            "nodo": ("Nodo", 60),
            "pubblicate": ("Oss. pubblicate", 120),
            "server": ("Server", 70),
            "ricevute": ("Oss. ricevute", 110),
            "age": ("Età (ms)", 90),
            "calib": ("Calibrata", 90),
            "note": ("Note", 320),
        }
        for column, (title, width) in headings.items():
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w", stretch=column == "note")
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
            text=(
                "Pose fuse pubblicate dal server: ● tag visibile, ○ posa predetta o ferma, "
                "arancio = camere calibrate, viola = reference marker."
            ),
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        notebook.add(world_frame, text="Vista world (robot tracciati)")

        log_frame = ttk.Frame(notebook, padding=6)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="none", height=12, state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")
        ttk.Button(log_frame, text="Pulisci", command=self.clear_log).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        notebook.add(log_frame, text="Console ed eventi")

    def _path_row(self, parent, row: int, label: str, variable, directory: bool) -> None:
        ttk = self._ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, columnspan=3, sticky="ew", padx=4, pady=2
        )
        ttk.Button(
            parent, text="Sfoglia…", command=lambda: self._browse(variable, directory)
        ).grid(row=row, column=4, sticky="e", pady=2)

    def _browse(self, variable, directory: bool) -> None:
        from tkinter import filedialog

        current = variable.get()
        initial = str(Path(current).parent if current and not directory else current or ".")
        chosen = (
            filedialog.askdirectory(initialdir=initial)
            if directory
            else filedialog.askopenfilename(
                initialdir=initial, filetypes=[("JSON", "*.json"), ("Tutti i file", "*.*")]
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
            self.append_log("[gui] porta MQTT non valida: uso quella precedente")
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
            self.append_log(f"[gui] configurazione non leggibile: {error}")
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
            f"[gui] in ascolto su {options.mqtt_host}:{options.mqtt_port} "
            f"topic {config.base_topic}/# (camere: {', '.join(cameras) or 'nessuna'})"
        )

    def start_server(self) -> None:
        if self.process.running:
            return
        options = self.current_options()
        self.options = options
        if self.status.coordinator_online():
            self.append_log(
                "[gui] attenzione: un coordinatore sta già pubblicando metriche su questo "
                "broker; due server pubblicano le stesse pose"
            )
        try:
            self.process.start(options)
        except (OSError, RuntimeError) as error:
            self.append_log(f"[gui] avvio fallito: {error}")
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
        self.append_log("[gui] arresto del server in corso…")
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
        for line in self.process.drain_logs():
            self.append_log(line)
        if self.monitor is not None:
            for topic, body in self.monitor.drain():
                if "/pose/" in topic:
                    self.world.apply_pose(body)
                    continue
                line = self.status.apply(topic, body)
                if line:
                    self.append_log(line)
        # Extrinsics change while calibrating a node: the store throttles the rescan
        # itself, so polling every tick costs nothing.
        if self.calibration_store is not None and self.calibration_store.reload_if_changed():
            self.world.update_scene(self.app_config, self.calibration_store.calibrations)
            self.append_log("[gui] calibrazioni ricaricate dal disco")
        self._refresh_indicators()
        self._refresh_table()
        self.world_canvas.redraw()
        self.root.after(REFRESH_MS, self._refresh)

    @staticmethod
    def _set_if_changed(variable, value: str) -> None:
        if variable.get() != value:
            variable.set(value)

    def _refresh_indicators(self) -> None:
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
            server_state = f"server: in esecuzione (pid {self.process.pid})"
        elif self.process.exit_code is None:
            server_state = "server: fermo"
        else:
            server_state = f"server: uscito (codice {self.process.exit_code})"
        self._set_if_changed(self.server_state_var, server_state)
        if self.monitor is None:
            broker_state = "broker: non collegato"
        elif self.monitor.connected.is_set():
            fusion = "coordinatore attivo" if self.status.coordinator_online() else "in attesa"
            broker_state = (
                f"broker: {self.monitor.settings.host}:{self.monitor.settings.port} · {fusion}"
            )
        else:
            broker_state = f"broker: {self.monitor.error or 'connessione in corso…'}"
        self._set_if_changed(self.broker_state_var, broker_state)
        tags = ", ".join(str(tag) for tag in self.status.tracked_tags) or "—"
        self._set_if_changed(
            self.fusion_state_var, f"pose pubblicate: {self.status.poses_published} · tag: {tags}"
        )

    def _refresh_table(self) -> None:
        rows = self.status.rows()
        existing = set(self.table.get_children())
        for row in rows:
            values = (
                row.camera_id,
                _format_flag(row.node_online),
                _format_optional(row.node_observations),
                _format_flag(row.server_online),
                _format_optional(row.observations_received),
                _format_optional(row.age_ms),
                "—" if row.calibrated is None else ("sì" if row.calibrated else "no"),
                row.note,
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
        description=(
            "Pannello grafico per avviare il server di fusione e sorvegliare "
            "nodi e camere nel deployment distribuito"
        )
    )
    parser.add_argument("--config", type=Path, help="config iniziale mostrata nel pannello")
    parser.add_argument("--calibrations", type=Path, default=DEFAULT_CALIBRATIONS)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--mqtt-host", default=os.getenv("VISION_MQTT_HOST", "localhost"), help="broker MQTT"
    )
    parser.add_argument(
        "--mqtt-port", type=int, default=int(os.getenv("VISION_MQTT_PORT", "1883"))
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--no-debug-window",
        action="store_true",
        help="non pre-selezionare --debug (vista world) per il server",
    )
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics("vision-server-gui", verbose=args.verbose)
    print(f"Log diagnostico: {diagnostic_path}")
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
        print(f"Configurazione non leggibile ({error}): il roster verrà scoperto da MQTT")
    try:
        import tkinter
    except ImportError:
        parser.error(
            "tkinter non è disponibile: installare il pacchetto python3-tk "
            "(Debian/Ubuntu: sudo apt install python3-tk)"
        )
    try:
        app = ServerGuiApp(options, list(cameras))
    except tkinter.TclError as error:
        parser.error(f"impossibile aprire la finestra grafica (DISPLAY assente?): {error}")
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda signum, frame: app.request_stop())
    app.run()


if __name__ == "__main__":
    server_gui_main()

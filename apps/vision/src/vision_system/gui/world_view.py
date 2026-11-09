"""Top-down 2D rendering of the arena onto a Tk canvas."""

from __future__ import annotations

import math
from collections.abc import Sequence

from ..monitoring.world import TrackedTag, WorldModel

# The drawn viewport snaps to this grid so that a moving tag does not rescale
# the whole scene on every frame.
VIEWPORT_QUANTUM_M = 0.5


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

    def redraw(self, now_ns: int) -> None:
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
        tags = self.model.tags()
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
            self._sync_trail(items["trail"], tag.tag_id)
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

    def _sync_trail(self, item: int, tag_id: int) -> None:
        trail = self.model.trail(tag_id)
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


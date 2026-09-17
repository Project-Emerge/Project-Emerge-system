"""The arena renderer: viewport hysteresis and incremental canvas updates."""

import pytest

from vision_system.gui.world_view import VIEWPORT_QUANTUM_M, WorldCanvas, fit_viewport
from vision_system.monitoring.world import STALE_POSE_NS, WorldModel
from vision_system.transport.payloads import PoseUpdate


def test_viewport_snaps_to_the_quantum_grid():
    assert fit_viewport(None, (-0.1, 0.2, 2.6, 1.1)) == (-0.5, 0.0, 3.0, 1.5)


def test_viewport_is_kept_while_the_content_still_fits():
    """Rescaling on every frame is what made the view flicker."""
    viewport = fit_viewport(None, (0.0, 0.0, 4.0, 3.0))
    for x_max in (3.2, 3.9, 2.5, 4.0):
        assert fit_viewport(viewport, (0.0, 0.0, x_max, 3.0)) == viewport


def test_viewport_expands_when_a_tag_leaves_it():
    viewport = fit_viewport(None, (0.0, 0.0, 4.0, 3.0))
    grown = fit_viewport(viewport, (0.0, 0.0, 4.2, 3.0))
    assert grown[2] >= 4.2
    assert grown != viewport
    assert grown[2] % VIEWPORT_QUANTUM_M == pytest.approx(0.0)


def test_viewport_shrinks_only_when_far_too_large():
    viewport = fit_viewport(None, (0.0, 0.0, 10.0, 10.0))
    assert fit_viewport(viewport, (0.0, 0.0, 6.0, 6.0)) == viewport
    assert fit_viewport(viewport, (0.0, 0.0, 2.0, 2.0)) == (0.0, 0.0, 2.0, 2.0)




class FakeCanvas:
    """Records the canvas calls the renderer makes, so drawing can be asserted."""

    def __init__(self, **options):
        self.options = options
        self.items: dict[int, str] = {}
        self.deleted: list[object] = []
        self.coords_calls: list[int] = []
        self.texts: dict[int, str] = {}
        self._next_id = 0
        self.width = 640
        self.height = 360

    def _create(self, kind):
        self._next_id += 1
        self.items[self._next_id] = kind
        return self._next_id

    def create_line(self, *args, **kwargs):
        return self._create("line")

    def create_oval(self, *args, **kwargs):
        return self._create("oval")

    def create_text(self, *args, **kwargs):
        item = self._create("text")
        self.texts[item] = kwargs.get("text", "")
        return item

    def create_polygon(self, *args, **kwargs):
        return self._create("polygon")

    def create_rectangle(self, *args, **kwargs):
        return self._create("rectangle")

    def coords(self, item, *args):
        self.coords_calls.append(item)

    def itemconfigure(self, item, **kwargs):
        if "text" in kwargs:
            self.texts[item] = kwargs["text"]

    def delete(self, target):
        self.deleted.append(target)

    def tag_lower(self, tag):
        self.lowered = tag

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def created(self, kind):
        return [item for item, made in self.items.items() if made == kind]


class FakeTk:
    """Just enough of the tkinter module for the canvas to build itself."""

    def __init__(self):
        self.canvas = None

    def Canvas(self, parent, **options):  # noqa: N802 - mirrors the tkinter name
        self.canvas = FakeCanvas(**options)
        return self.canvas


def _canvas(trail_seconds: float = 3.0):
    model = WorldModel(trail_seconds=trail_seconds)
    tk = FakeTk()
    return WorldCanvas(parent=None, model=model, tk_module=tk), model, tk.canvas


def _pose(tag_id: int, x: float, y: float) -> PoseUpdate:
    body = {"tag_id": tag_id, "position_m": {"x": x, "y": y}}
    pose = PoseUpdate.from_body(body)
    assert pose is not None
    return pose


def test_canvas_draws_a_static_layer_on_the_first_frame():
    canvas, model, fake = _canvas()
    canvas.redraw(now_ns=STALE_POSE_NS)
    assert fake.created("line"), "the grid and axes should have been drawn"


def test_canvas_rebuilds_the_static_layer_only_when_the_scene_changes():
    # Wiping and redrawing the whole canvas every tick is what made the view flash,
    # so an unchanged frame must not touch the static layer again.
    canvas, model, fake = _canvas()
    canvas.redraw(now_ns=STALE_POSE_NS)
    deletions = len(fake.deleted)
    canvas.redraw(now_ns=STALE_POSE_NS)
    assert len(fake.deleted) == deletions


def test_canvas_reuses_the_items_of_a_tag_that_only_moved():
    canvas, model, fake = _canvas()
    model.apply(_pose(7, 0.0, 0.0), now_ns=STALE_POSE_NS)
    canvas.redraw(now_ns=STALE_POSE_NS)
    ovals = len(fake.created("oval"))
    model.apply(_pose(7, 0.4, 0.2), now_ns=STALE_POSE_NS + 1)
    canvas.redraw(now_ns=STALE_POSE_NS + 1)
    assert len(fake.created("oval")) == ovals, "a moved tag must be repositioned, not recreated"
    assert fake.coords_calls


def test_canvas_forgets_the_items_of_a_tag_that_vanished():
    canvas, model, fake = _canvas()
    model.apply(_pose(7, 0.0, 0.0), now_ns=STALE_POSE_NS)
    canvas.redraw(now_ns=STALE_POSE_NS)
    model.expire(STALE_POSE_NS + 100 * STALE_POSE_NS)
    canvas.redraw(now_ns=STALE_POSE_NS + 100 * STALE_POSE_NS)
    assert fake.deleted, "the vanished tag's canvas items should have been deleted"


def test_canvas_summary_counts_live_tags_against_the_total():
    canvas, model, fake = _canvas()
    model.apply(_pose(7, 0.0, 0.0), now_ns=STALE_POSE_NS)
    model.apply(_pose(9, 1.0, 1.0), now_ns=STALE_POSE_NS)
    later = STALE_POSE_NS * 3
    model.apply(_pose(9, 1.0, 1.0), now_ns=later)
    canvas.redraw(now_ns=later)
    summary = " ".join(fake.texts.values())
    assert "1" in summary and "2" in summary

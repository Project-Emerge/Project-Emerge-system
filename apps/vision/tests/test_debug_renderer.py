import numpy as np
import pytest

from vision_system.core.config import AppConfig, DebugConfig
from vision_system.core.geometry import pose_matrix
from vision_system.pipeline.debug_renderer import POSE_HOLD_S, RENDER_PERIOD_S, DebugRenderer
from vision_system.pipeline.fusion import FusedPose


def _config(trail_seconds: float = 3.0) -> AppConfig:
    return AppConfig(debug=DebugConfig(world_view=True, trail_seconds=trail_seconds))


def _pose(tag_id: int, x: float, y: float) -> FusedPose:
    position = np.array([x, y, 0.0])
    return FusedPose(
        tag_id=tag_id,
        monotonic_ns=0,
        utc_ns=0,
        world_from_tag=pose_matrix(position, np.array([0.0, 0.0, 0.0, 1.0])),
        position_m=position,
        orientation_xyzw=np.array([0.0, 0.0, 0.0, 1.0]),
        velocity_m_s=np.zeros(3),
        angular_velocity_rad_s=np.zeros(3),
        cameras=["cam_0"],
        reprojection_error_px=0.2,
        quality=0.9,
    )


class _Clock:
    """Monotonic clock under test control."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr("vision_system.pipeline.debug_renderer.time.monotonic", fake)
    return fake


def _drawn_tag_ids(renderer: DebugRenderer) -> set[int]:
    return set(renderer._last_poses)


def test_world_keeps_drawing_a_tag_between_fusion_updates(clock):
    """The loop runs far faster than fusion: without the cache the marker blinks."""
    renderer = DebugRenderer(_config(), {})
    renderer._ingest_poses([_pose(7, 1.0, 2.0)])
    with_pose = renderer._world()
    for _ in range(20):
        clock.now += RENDER_PERIOD_S
        renderer._ingest_poses([])
    assert _drawn_tag_ids(renderer) == {7}
    assert np.array_equal(renderer._world(), with_pose)


def test_world_forgets_a_tag_that_stopped_being_fused(clock):
    renderer = DebugRenderer(_config(trail_seconds=0.5), {})
    renderer._ingest_poses([_pose(7, 1.0, 2.0)])
    clock.now += POSE_HOLD_S + 0.1
    renderer._ingest_poses([])
    assert _drawn_tag_ids(renderer) == set()
    assert 7 not in renderer.trails


def test_a_long_trail_keeps_the_tag_alive(clock):
    renderer = DebugRenderer(_config(trail_seconds=5.0), {})
    renderer._ingest_poses([_pose(7, 1.0, 2.0)])
    clock.now += POSE_HOLD_S + 0.1
    renderer._ingest_poses([])
    assert _drawn_tag_ids(renderer) == {7}


def test_world_extent_does_not_change_on_a_tick_without_poses(clock):
    renderer = DebugRenderer(_config(), {})
    renderer._ingest_poses([_pose(7, 4.0, 3.0)])
    reference = renderer._world()
    clock.now += RENDER_PERIOD_S
    renderer._ingest_poses([])
    assert np.array_equal(renderer._world(), reference)


def test_render_is_rate_limited_but_never_drops_poses(clock, monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(
        "vision_system.pipeline.debug_renderer.cv2.imshow",
        lambda window, image: shown.append(window),
    )
    monkeypatch.setattr("vision_system.pipeline.debug_renderer.cv2.waitKey", lambda delay: -1)
    renderer = DebugRenderer(_config(), {})
    renderer.render({}, {}, [_pose(7, 1.0, 2.0)])
    for step in range(10):
        renderer.render({}, {}, [_pose(8, float(step), 0.0)])
    assert shown == ["VisionSystem - world"]
    assert _drawn_tag_ids(renderer) == {7, 8}
    clock.now += 2 * RENDER_PERIOD_S
    renderer.render({}, {}, [])
    assert shown == ["VisionSystem - world"] * 2

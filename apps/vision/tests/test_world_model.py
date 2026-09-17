"""The world model: poses, trails, expiry and the static scene."""

import math

import pytest

from vision_system.core.config import (
    AppConfig,
    ArucoConfig,
    CameraCalibration,
    ReferenceMarkerConfig,
)
from vision_system.monitoring.world import STALE_POSE_NS, WorldModel
from vision_system.transport.payloads import PoseUpdate


def _apply_pose(world, body, now_ns):
    pose = PoseUpdate.from_body(body)
    if pose is None:
        return None
    return world.apply(pose, now_ns)


def _pose(tag_id: int, x: float, y: float, yaw_deg: float = 0.0, **overrides) -> dict:
    body = {
        "tag_id": tag_id,
        "position_m": {"x": x, "y": y, "z": 0.0},
        "euler_deg": {"roll": 0.0, "pitch": 0.0, "yaw": yaw_deg},
        "quality": 0.9,
        "predicted": False,
        "visible_by": ["cam_0", "cam_1"],
    }
    body.update(overrides)
    return body


def test_world_model_tracks_positions_and_trail():
    world = WorldModel(trail_seconds=2.0)
    start = 10 * STALE_POSE_NS
    _apply_pose(world, _pose(7, 1.0, 2.0, yaw_deg=90.0), now_ns=start)
    _apply_pose(world, _pose(7, 1.2, 2.1), now_ns=start + 100_000_000)
    tag = world.tags()[0]
    assert (tag.tag_id, tag.x_m, tag.y_m) == (7, 1.2, 2.1)
    assert tag.cameras == ("cam_0", "cam_1")
    assert tag.stale(start + 100_000_000) is False
    assert world.trail(7) == [(1.0, 2.0), (1.2, 2.1)]


def test_world_model_drops_trail_points_older_than_the_window():
    world = WorldModel(trail_seconds=1.0)
    start = 10 * STALE_POSE_NS
    _apply_pose(world, _pose(7, 0.0, 0.0), now_ns=start)
    _apply_pose(world, _pose(7, 1.0, 0.0), now_ns=start + 2_000_000_000)
    world.expire(start + 2_000_000_000)
    assert world.trail(7) == [(1.0, 0.0)]


def test_world_model_falls_back_to_the_quaternion_for_heading():
    world = WorldModel()
    body = _pose(3, 0.0, 0.0)
    del body["euler_deg"]
    body["orientation_xyzw"] = {
        "x": 0.0,
        "y": 0.0,
        "z": math.sin(math.pi / 4),
        "w": math.cos(math.pi / 4),
    }
    tag = _apply_pose(world, body, now_ns=STALE_POSE_NS)
    assert tag is not None
    assert tag.heading_rad == pytest.approx(math.pi / 2, abs=1e-6)


def test_world_model_ignores_malformed_pose_payloads():
    world = WorldModel()
    assert _apply_pose(world, {"tag_id": 1}, now_ns=STALE_POSE_NS) is None
    assert _apply_pose(world, {"position_m": {"x": 1.0, "y": 2.0}}, now_ns=STALE_POSE_NS) is None
    assert (
        _apply_pose(
            world,
            {"tag_id": "sette", "position_m": {"x": 1.0, "y": 2.0}}, now_ns=STALE_POSE_NS
        )
        is None
    )
    assert world.tags() == []


def test_world_model_marks_stale_tags_and_forgets_the_oldest():
    world = WorldModel()
    start = 10 * STALE_POSE_NS
    _apply_pose(world, _pose(7, 1.0, 1.0), now_ns=start)
    stale_moment = start + STALE_POSE_NS + 1
    assert world.tags()[0].stale(stale_moment) is True
    world.expire(start + 6 * STALE_POSE_NS)
    assert world.tags() == []


def test_world_model_scene_uses_references_and_calibrated_cameras():
    world = WorldModel()
    config = AppConfig(
        aruco=ArucoConfig(
            reference_markers=[
                ReferenceMarkerConfig(
                    id=40, size_m=0.15, position_m=(3.0, 1.0, 0.0), orientation_xyzw=(0, 0, 0, 1)
                )
            ]
        )
    )
    calibration = CameraCalibration(
        camera_id="cam_0",
        source=0,
        image_size=(1920, 1080),
        camera_matrix=[[1200, 0, 960], [0, 1200, 540], [0, 0, 1]],
        distortion=[0.0] * 8,
        # Camera mounted at (2.0, 1.0, 2.5) looking along world -Y.
        world_from_camera=[[1, 0, 0, 2.0], [0, 0, -1, 1.0], [0, 1, 0, 2.5], [0, 0, 0, 1]],
        intrinsic_median_error_px=0.2,
        intrinsic_p95_error_px=0.4,
        captured_at="2026-09-16T00:00:00+00:00",
        opencv_version="test",
        board_checksum="test",
    )
    uncalibrated = calibration.model_copy(update={"camera_id": "cam_1", "world_from_camera": None})
    world.update_scene(config, {"cam_0": calibration, "cam_1": uncalibrated})
    assert world.references == [(40, 3.0, 1.0)]
    x_m, y_m, heading = world.cameras["cam_0"]
    assert (x_m, y_m) == (2.0, 1.0)
    assert heading == pytest.approx(-math.pi / 2)
    assert "cam_1" not in world.cameras


def test_world_bounds_contain_scene_and_tags():
    world = WorldModel()
    _apply_pose(world, _pose(7, 4.0, -1.0), now_ns=STALE_POSE_NS)
    min_x, min_y, max_x, max_y = world.bounds(margin_m=0.5)
    assert min_x <= -0.5 and min_y <= -1.5
    assert max_x >= 4.5 and max_y >= 0.5


def test_world_reset_forgets_every_tag():
    world = WorldModel()
    _apply_pose(world, _pose(7, 1.0, 1.0), now_ns=STALE_POSE_NS)
    world.reset()
    assert world.tags() == []
    assert world.trail(7) == []



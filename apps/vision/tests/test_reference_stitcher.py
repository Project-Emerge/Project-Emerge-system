import json
import math

import cv2
import numpy as np
import pytest

from vision_system.mapping.stitching import (
    _camera_overlap_status,
    _load_stitch_config,
    render_stitched_homography,
    select_anchor_frame_from_homography,
    stitch_reference_markers,
)

MARKER_SIZE = 0.07
ANCHOR_IDS = [100, 101, 102, 103]
ANCHOR_POSITIONS = {
    100: (0.6, 0.5),
    101: (11.4, 0.5),
    102: (11.4, 7.5),
    103: (0.6, 7.5),
}


def _world_corners(position, yaw, size=MARKER_SIZE):
    half = size / 2
    local = np.array([[-half, half], [half, half], [half, -half], [-half, -half]], dtype=float)
    cosine, sine = math.cos(yaw), math.sin(yaw)
    rotation = np.array([[cosine, -sine], [sine, cosine]])
    return np.asarray(position) + local @ rotation.T


def _photo_observations(world_markers, region, yaws, noise_px=0.5):
    pixel_rect = np.array([[0, 0], [1920, 0], [1920, 1080], [0, 1080]], dtype=np.float32)
    world_rect = np.array(
        [
            [region[0], region[2]],
            [region[1], region[2]],
            [region[1], region[3]],
            [region[0], region[3]],
        ],
        dtype=np.float32,
    )
    homography = cv2.getPerspectiveTransform(world_rect, pixel_rect)
    observations = {}
    for marker_id, position in world_markers.items():
        x, y = position
        if not (region[0] <= x <= region[1] and region[2] <= y <= region[3]):
            continue
        corners = _world_corners(position, yaws[marker_id])
        homogeneous = np.concatenate([corners, np.ones((4, 1))], axis=1)
        projected = (homography @ homogeneous.T).T
        pixels = projected[:, :2] / projected[:, 2, None]
        pixels += np.random.default_rng(marker_id).normal(0, noise_px, pixels.shape)
        observations[marker_id] = pixels
    return observations


def _arena_markers():
    positions = {}
    marker_id = 1
    for row in range(4):
        for column in range(5):
            positions[marker_id] = (1.2 + column * 2.6, 1.0 + row * 2.2)
            marker_id += 1
    positions.update(ANCHOR_POSITIONS)
    yaws = {marker_id: (marker_id * 37 % 120 - 60) * math.pi / 180 for marker_id in positions}
    return positions, yaws


def _assert_recovered(result, world_markers, yaws, tolerance_m=0.03, tolerance_deg=4.0):
    recovered = {marker.id: marker for marker in result.references}
    assert set(recovered) == set(world_markers)
    for marker_id, position in world_markers.items():
        marker = recovered[marker_id]
        np.testing.assert_allclose(
            marker.position_m[:2], position, atol=tolerance_m, err_msg=f"marker {marker_id}"
        )
        yaw = 2 * math.atan2(marker.orientation_xyzw[2], marker.orientation_xyzw[3])
        difference = abs((yaw - yaws[marker_id] + math.pi) % (2 * math.pi) - math.pi)
        assert math.degrees(difference) < tolerance_deg, f"marker {marker_id} yaw"


def test_stitch_combines_photos_with_and_without_anchors() -> None:
    world_markers, yaws = _arena_markers()
    regions = {
        "left": (0.0, 7.0, 0.0, 8.0),
        "right": (5.0, 12.0, 0.0, 8.0),
        "top": (0.0, 12.0, 3.0, 8.0),
        "corner": (8.0, 12.0, 0.0, 3.0),
    }
    observations = {
        name: _photo_observations(world_markers, region, yaws) for name, region in regions.items()
    }
    anchors = {marker_id: np.array(position) for marker_id, position in ANCHOR_POSITIONS.items()}

    result = stitch_reference_markers(observations, anchors, MARKER_SIZE)

    assert not result.detached_images
    assert not result.dropped_markers
    _assert_recovered(result, world_markers, yaws)
    recovered = {marker.id: marker for marker in result.references}
    for marker_id, position in ANCHOR_POSITIONS.items():
        np.testing.assert_allclose(
            recovered[marker_id].position_m,
            (*position, 0.0),
            atol=1e-12,
        )
    assert result.rms_total < 0.01


def test_stitch_without_anchors_preserves_geometry() -> None:
    world_markers, yaws = _arena_markers()
    observations = {
        "first": _photo_observations(world_markers, (0.0, 7.0, 0.0, 8.0), yaws),
        "second": _photo_observations(world_markers, (5.0, 12.0, 0.0, 8.0), yaws),
    }

    result = stitch_reference_markers(observations, {}, MARKER_SIZE)

    recovered = {marker.id: marker for marker in result.references}
    assert set(recovered) == set(world_markers)
    for left in world_markers:
        for right in world_markers:
            if left >= right:
                continue
            expected = np.linalg.norm(
                np.asarray(world_markers[left]) - np.asarray(world_markers[right])
            )
            actual = np.linalg.norm(
                np.asarray(recovered[left].position_m[:2])
                - np.asarray(recovered[right].position_m[:2])
            )
            # With no measured anchors the metric scale comes only from marker
            # sides that are roughly 10-20 pixels wide in these noisy images.
            assert actual == pytest.approx(expected, rel=0.04), f"coppia {left}-{right}"


def test_detached_images_and_markers_are_reported() -> None:
    world_markers, yaws = _arena_markers()
    observations = {
        "main": _photo_observations(world_markers, (0.0, 7.0, 0.0, 8.0), yaws),
        "isolated": {77: _world_corners((3.0, 3.0), 0.3)},
    }
    anchors = {marker_id: np.array(position) for marker_id, position in ANCHOR_POSITIONS.items()}

    result = stitch_reference_markers(observations, anchors, MARKER_SIZE)

    assert "isolated" in result.detached_images
    assert 77 in result.dropped_markers
    assert all(marker.id != 77 for marker in result.references)


def test_empty_first_image_is_reported_instead_of_breaking_bootstrap() -> None:
    world_markers, yaws = _arena_markers()
    observations = {
        "aaa-empty": {},
        "left": _photo_observations(world_markers, (0.0, 7.0, 0.0, 8.0), yaws),
        "right": _photo_observations(world_markers, (5.0, 12.0, 0.0, 8.0), yaws),
    }
    anchors = {marker_id: np.array(position) for marker_id, position in ANCHOR_POSITIONS.items()}

    result = stitch_reference_markers(observations, anchors, MARKER_SIZE)

    assert result.references
    assert result.detached_images == ["aaa-empty"]


def test_one_shared_marker_connects_two_views() -> None:
    world_markers = {1: (4.0, 2.0), 2: (1.0, 1.0), 3: (7.0, 3.0)}
    yaws = {1: 0.2, 2: -0.4, 3: 0.7}
    observations = {
        "left": _photo_observations(world_markers, (0.0, 4.1, 0.0, 4.0), yaws, noise_px=0.0),
        "right": _photo_observations(world_markers, (3.9, 8.0, 0.0, 4.0), yaws, noise_px=0.0),
    }

    result = stitch_reference_markers(observations, {}, MARKER_SIZE)

    assert not result.detached_images
    assert {marker.id for marker in result.references} == {1, 2, 3}

    mosaic, canvas_from_world, centers = render_stitched_homography(
        {
            "left": np.full((1080, 1920, 3), 80, dtype=np.uint8),
            "right": np.full((1080, 1920, 3), 160, dtype=np.uint8),
        },
        result,
        MARKER_SIZE,
    )
    assert mosaic.shape[0] >= 480
    assert mosaic.shape[1] >= 640
    assert canvas_from_world.shape == (3, 3)
    assert set(centers) == {1, 2, 3}
    assert all(0 <= x < mosaic.shape[1] and 0 <= y < mosaic.shape[0] for x, y in centers.values())


def test_two_configured_references_per_camera_and_overlap_enable_auto_capture() -> None:
    links, connected = _camera_overlap_status(
        {
            "cam_0": {13, 21},
            "cam_1": {13, 15},
            "cam_2": {15, 19},
            "cam_3": {19, 21},
        }
    )

    assert connected
    assert [(left, right) for left, right, _ in links] == [
        ("cam_0", "cam_1"),
        ("cam_0", "cam_3"),
        ("cam_1", "cam_2"),
        ("cam_2", "cam_3"),
    ]

    _, connected = _camera_overlap_status(
        {
            "cam_0": {13, 15},
            "cam_1": {13, 19},
            "cam_2": {19},
        }
    )
    assert not connected


def test_interactive_frame_selection_asks_for_measured_dimensions(monkeypatch) -> None:
    selected_ids = [13, 21, 15, 19]

    def select_four_markers(picker) -> np.ndarray:
        picker.selected_ids = selected_ids
        return np.zeros((4, 2), dtype=np.float64)

    answers = iter(["12.5", "8.25"])
    monkeypatch.setattr(
        "vision_system.mapping.stitching.ControlPointPicker.run",
        select_four_markers,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    selected = select_anchor_frame_from_homography(
        np.zeros((480, 640, 3), dtype=np.uint8),
        {
            13: (50.0, 400.0),
            21: (590.0, 400.0),
            15: (590.0, 50.0),
            19: (50.0, 50.0),
        },
        None,
        None,
    )

    assert selected == (selected_ids, 12.5, 8.25)


def test_stitch_loader_recovers_anchor_frame_with_missing_references(tmp_path) -> None:
    path = tmp_path / "orphaned-anchor-frame.json"
    payload = {
        "schema_version": 1,
        "cameras": [{"id": "cam_0", "source": 0}],
        "aruco": {
            "anchor_frame": {
                "origin_id": 13,
                "x_axis_id": 18,
                "opposite_id": 19,
                "y_axis_id": 15,
                "x_distance_m": 12.0,
                "y_distance_m": 8.0,
            },
            "reference_markers": [
                {
                    "id": 19,
                    "size_m": 0.09,
                    "position_m": [12.0, 8.0, 0.0],
                    "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
                }
            ],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    config, recovered_frame = _load_stitch_config(path)

    assert config.aruco.anchor_frame is None
    assert [marker.id for marker in config.aruco.reference_markers] == [19]
    assert recovered_frame is not None
    assert recovered_frame.origin_id == 13
    assert recovered_frame.x_distance_m == 12.0

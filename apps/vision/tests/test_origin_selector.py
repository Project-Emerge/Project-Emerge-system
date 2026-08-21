import math

import pytest

from vision_system.core.config import (
    AnchorFrameConfig,
    AppConfig,
    ArucoConfig,
    AutoMobileMarkerConfig,
    ReferenceMarkerConfig,
)
from vision_system.mapping.origin import rebase_anchor_frame


def _config() -> AppConfig:
    positions = {
        18: (0.0, 0.0, 0.0),
        13: (0.6, 0.0, 0.0),
        19: (0.6, 0.5, 0.0),
        15: (0.0, 0.5, 0.0),
        30: (0.3, 0.25, 0.0),
    }
    references = [
        ReferenceMarkerConfig(
            id=marker_id,
            size_m=0.07,
            position_m=position,
            orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
        )
        for marker_id, position in positions.items()
    ]
    return AppConfig(
        revision=8,
        aruco=ArucoConfig(
            reference_markers=references,
            auto_mobile_markers=AutoMobileMarkerConfig(enabled=True, default_size_m=0.12),
            anchor_frame=AnchorFrameConfig(
                origin_id=18,
                x_axis_id=13,
                y_axis_id=15,
                opposite_id=19,
                x_distance_m=0.6,
                y_distance_m=0.5,
            ),
        ),
    )


def test_rebase_to_next_corner_rotates_right_handed_frame() -> None:
    updated = rebase_anchor_frame(_config(), 13)

    frame = updated.aruco.anchor_frame
    assert frame is not None
    assert frame.origin_id == 13
    assert frame.x_axis_id == 19
    assert frame.y_axis_id == 18
    assert frame.opposite_id == 15
    assert frame.x_distance_m == pytest.approx(0.5)
    assert frame.y_distance_m == pytest.approx(0.6)
    positions = {marker.id: marker.position_m for marker in updated.aruco.reference_markers}
    assert positions[13] == pytest.approx((0.0, 0.0, 0.0))
    assert positions[19] == pytest.approx((0.5, 0.0, 0.0))
    assert positions[18] == pytest.approx((0.0, 0.6, 0.0))
    assert positions[15] == pytest.approx((0.5, 0.6, 0.0))
    assert positions[30] == pytest.approx((0.25, 0.3, 0.0))
    orientation = updated.references_by_id()[30].orientation_xyzw
    assert orientation == pytest.approx((0.0, 0.0, -math.sqrt(0.5), math.sqrt(0.5)), abs=1e-12)
    assert updated.revision == 9
    assert updated.aruco.auto_mobile_markers.enabled is True


def test_rebase_to_current_origin_keeps_frame_geometry() -> None:
    updated = rebase_anchor_frame(_config(), 18)

    frame = updated.aruco.anchor_frame
    assert frame is not None
    assert (frame.origin_id, frame.x_axis_id, frame.y_axis_id, frame.opposite_id) == (
        18,
        13,
        15,
        19,
    )
    assert updated.references_by_id()[30].position_m == pytest.approx((0.3, 0.25, 0.0))
    assert updated.references_by_id()[30].orientation_xyzw == pytest.approx((0, 0, 0, 1))


def test_rebase_rejects_non_anchor_marker() -> None:
    with pytest.raises(ValueError, match="non appartiene"):
        rebase_anchor_frame(_config(), 30)

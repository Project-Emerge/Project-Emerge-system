import numpy as np

from vision_system.core.config import CameraCalibration, ReferenceMarkerConfig
from vision_system.pipeline import drift as drift_module
from vision_system.pipeline.drift import ReferenceDriftMonitor, evaluate_reference_drift


def _references() -> dict[int, ReferenceMarkerConfig]:
    return {
        13: ReferenceMarkerConfig(
            id=13,
            size_m=0.07,
            position_m=(0.0, 0.0, 0.0),
            orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
        ),
        15: ReferenceMarkerConfig(
            id=15,
            size_m=0.07,
            position_m=(0.71, 0.0, 0.0),
            orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
        ),
    }


def _calibration() -> CameraCalibration:
    return CameraCalibration(
        camera_id="cam_0",
        source=5,
        image_size=(1920, 1080),
        camera_matrix=[[1200, 0, 960], [0, 1200, 540], [0, 0, 1]],
        distortion=[0.0] * 8,
        world_from_camera=[
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        intrinsic_median_error_px=0.2,
        intrinsic_p95_error_px=0.4,
        captured_at="2026-08-18T00:00:00+00:00",
        opencv_version="test",
        board_checksum="test",
    )


def test_evaluate_reports_unavailable_without_extrinsics() -> None:
    status = evaluate_reference_drift(
        None,
        None,
        _references(),
        _calibration().model_copy(update={"world_from_camera": None}),
    )
    assert status["status"] == "unavailable"
    assert status["has_calibration"] is True
    assert status["has_extrinsics"] is False


def test_evaluate_reports_drift_on_large_translation(monkeypatch) -> None:
    def fake_estimate(*args, **kwargs):
        far_transform = np.eye(4, dtype=np.float64)
        far_transform[0, 3] = 0.5
        return far_transform, 0.5, [13, 15]

    monkeypatch.setattr(drift_module, "estimate_world_from_camera", fake_estimate)
    ids = np.array([[13, 15]], dtype=np.int32)
    status = evaluate_reference_drift(None, ids, _references(), _calibration())
    assert status["status"] == "drift"
    assert status["translation_error_m"] == 0.5
    assert status["used_reference_ids"] == [13, 15]


def test_monitor_excludes_after_sustained_drift(monkeypatch) -> None:
    excluded: list[str] = []

    def fake_estimate(*args, **kwargs):
        far_transform = np.eye(4, dtype=np.float64)
        far_transform[0, 3] = 0.5
        return far_transform, 0.5, [13, 15]

    monkeypatch.setattr(drift_module, "estimate_world_from_camera", fake_estimate)
    now = [1000.0]
    monkeypatch.setattr(drift_module.time, "monotonic", lambda: now[0])
    monitor = ReferenceDriftMonitor(on_exclude=lambda camera_id, status: excluded.append(camera_id))
    monitor.check("cam_0", None, None, _calibration(), _references())
    now[0] += 3.0
    monitor.check("cam_0", None, None, _calibration(), _references())
    assert excluded == ["cam_0"]
    assert "cam_0" in monitor.excluded_cameras
    monitor.reset()
    assert not monitor.excluded_cameras
    assert not monitor.drift_since


def test_monitor_respects_check_interval(monkeypatch) -> None:
    calls: list[int] = []

    def fake_estimate(*args, **kwargs):
        calls.append(1)
        return np.eye(4, dtype=np.float64), 0.1, [13, 15]

    monkeypatch.setattr(drift_module, "estimate_world_from_camera", fake_estimate)
    now = [1000.0]
    monkeypatch.setattr(drift_module.time, "monotonic", lambda: now[0])
    monitor = ReferenceDriftMonitor()
    assert monitor.check("cam_0", None, None, _calibration(), _references()) is not None
    assert monitor.check("cam_0", None, None, _calibration(), _references()) is None
    assert len(calls) == 1

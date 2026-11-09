from pathlib import Path

import numpy as np

from vision_system.apps.localizer import VisionRuntime
from vision_system.core.config import (
    AppConfig,
    ArucoConfig,
    CameraCalibration,
    CameraConfig,
    ReferenceMarkerConfig,
    save_json,
)
from vision_system.pipeline import drift as drift_module
from vision_system.pipeline.calibration_store import calibration_compatibility_error
from vision_system.transport.mqtt import OfflineBridge


def test_runtime_can_be_constructed_without_mqtt(tmp_path: Path) -> None:
    runtime = VisionRuntime(
        AppConfig(),
        calibration_dir=tmp_path / "calibrations",
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
        print_poses=True,
    )
    assert isinstance(runtime.bridge, OfflineBridge)
    assert runtime.bridge.print_poses
    assert not runtime.bridge.connected.is_set()
    assert not runtime.allow_low_quality


def test_calibration_must_match_camera_zoom_and_image_size() -> None:
    camera = CameraConfig(id="cam_0", source=5, width=1920, height=1080, zoom=125)
    calibration = CameraCalibration(
        camera_id="cam_0",
        source=5,
        image_size=(1920, 1080),
        camera_zoom=125,
        camera_matrix=[[1200, 0, 960], [0, 1200, 540], [0, 0, 1]],
        distortion=[0] * 8,
        intrinsic_median_error_px=0.2,
        intrinsic_p95_error_px=0.4,
        captured_at="2026-08-18T00:00:00+00:00",
        opencv_version="test",
        board_checksum="test",
    )
    assert calibration_compatibility_error(camera, calibration) is None
    assert (
        calibration_compatibility_error(camera.model_copy(update={"zoom": 150}), calibration)
        == "zoom differs"
    )
    assert (
        calibration_compatibility_error(camera.model_copy(update={"width": 1280}), calibration)
        == "image size differs"
    )
    assert (
        calibration_compatibility_error(
            camera.model_copy(update={"digital_zoom": 1.2}), calibration
        )
        == "digital zoom differs"
    )


def _drift_config() -> AppConfig:
    return AppConfig(
        aruco=ArucoConfig(
            reference_markers=[
                ReferenceMarkerConfig(
                    id=13,
                    size_m=0.07,
                    position_m=(0.0, 0.0, 0.0),
                    orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
                ),
                ReferenceMarkerConfig(
                    id=15,
                    size_m=0.07,
                    position_m=(0.71, 0.0, 0.0),
                    orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
                ),
            ]
        )
    )


def _write_calibration_with_extrinsics(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    calibration = CameraCalibration(
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
    save_json(directory / "cam_0.json", calibration)


def _check_drift_twice(
    runtime: VisionRuntime, monkeypatch, now: list[float]
) -> list[bool]:
    calls: list[bool] = []

    def fake_estimate(*args, **kwargs):
        calls.append(kwargs.get("allow_low_quality", False))
        far_transform = np.eye(4, dtype=np.float64)
        far_transform[0, 3] = 0.5
        return far_transform, 0.5, [13, 15]

    monkeypatch.setattr(drift_module, "estimate_world_from_camera", fake_estimate)
    monkeypatch.setattr(drift_module.time, "monotonic", lambda: now[0])
    runtime.drift.last_reference_check["cam_0"] = 0.0
    runtime._check_reference_drift("cam_0", None, None, None)
    now[0] += 3.0
    runtime.drift.last_reference_check["cam_0"] = 0.0
    runtime._check_reference_drift("cam_0", None, None, None)
    return calls


def test_allow_low_quality_prevents_drift_exclusion(tmp_path, monkeypatch) -> None:
    calibration_dir = tmp_path / "calibrations"
    _write_calibration_with_extrinsics(calibration_dir)

    strict = VisionRuntime(
        _drift_config(),
        calibration_dir=calibration_dir,
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
    )
    strict_calls = _check_drift_twice(strict, monkeypatch, [1000.0])
    assert strict_calls == [False, False]
    assert "cam_0" in strict.drift.excluded_cameras

    relaxed = VisionRuntime(
        _drift_config(),
        calibration_dir=calibration_dir,
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
        allow_low_quality=True,
    )
    relaxed_calls = _check_drift_twice(relaxed, monkeypatch, [2000.0])
    assert relaxed_calls == [True, True]
    assert "cam_0" not in relaxed.drift.excluded_cameras
    assert relaxed.drift.reference_check_status["cam_0"]["status"] == "drift"

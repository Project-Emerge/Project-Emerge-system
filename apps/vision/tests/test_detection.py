from datetime import UTC, datetime

import cv2
import numpy as np

from vision_system.core.aruco import aruco_dictionary
from vision_system.core.config import (
    ArucoConfig,
    AutoMobileMarkerConfig,
    CameraCalibration,
    MobileMarkerConfig,
    ReferenceMarkerConfig,
)
from vision_system.pipeline.detection import MarkerDetector


def test_detector_parameters_follow_aruco_config() -> None:
    detector = MarkerDetector(
        ArucoConfig(perspective_remove_pixel_per_cell=2, error_correction_rate=0.8),
        {},
    )
    parameters = detector.detector.getDetectorParameters()
    assert parameters.perspectiveRemovePixelPerCell == 2
    assert parameters.errorCorrectionRate == 0.8


def test_generated_marker_is_detected_and_localized() -> None:
    image = np.full((600, 800, 3), 255, dtype=np.uint8)
    marker = cv2.aruco.generateImageMarker(aruco_dictionary("DICT_4X4_50"), 23, 200)
    image[200:400, 300:500] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    calibration = CameraCalibration(
        camera_id="cam_0",
        source=5,
        image_size=(800, 600),
        camera_matrix=[[800, 0, 400], [0, 800, 300], [0, 0, 1]],
        distortion=[0] * 8,
        world_from_camera=np.eye(4).tolist(),
        intrinsic_median_error_px=0.1,
        intrinsic_p95_error_px=0.2,
        captured_at=datetime.now(UTC).isoformat(),
        opencv_version=cv2.__version__,
        board_checksum="test",
    )
    detector = MarkerDetector(
        ArucoConfig(mobile_markers=[MobileMarkerConfig(id=23, size_m=0.12)]),
        {"cam_0": calibration},
    )
    observations, corners, ids = detector.detect("cam_0", image, 1, 2)
    assert ids is not None and ids.reshape(-1).tolist() == [23]
    assert len(corners) == 1
    assert len(observations) == 1
    assert observations[0].world_from_tag[2, 3] > 0
    assert observations[0].reprojection_error_px < 0.1
    assert len(observations[0].candidate_world_from_tags) == 2


def test_unlisted_marker_is_automatically_a_mobile_target() -> None:
    image = np.full((600, 800, 3), 255, dtype=np.uint8)
    marker = cv2.aruco.generateImageMarker(aruco_dictionary("DICT_4X4_50"), 31, 200)
    image[200:400, 300:500] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    calibration = CameraCalibration(
        camera_id="cam_0",
        source=5,
        image_size=(800, 600),
        camera_matrix=[[800, 0, 400], [0, 800, 300], [0, 0, 1]],
        distortion=[0] * 8,
        world_from_camera=np.eye(4).tolist(),
        intrinsic_median_error_px=0.1,
        intrinsic_p95_error_px=0.2,
        captured_at=datetime.now(UTC).isoformat(),
        opencv_version=cv2.__version__,
        board_checksum="test",
    )
    detector = MarkerDetector(
        ArucoConfig(auto_mobile_markers=AutoMobileMarkerConfig(enabled=True, default_size_m=0.08)),
        {"cam_0": calibration},
    )
    observations, _, _ = detector.detect("cam_0", image, 1, 2)
    assert [observation.tag_id for observation in observations] == [31]
    assert np.ptp(observations[0].object_points[:, 0]) == 0.08


def test_reference_marker_is_not_auto_classified_as_mobile() -> None:
    image = np.full((600, 800, 3), 255, dtype=np.uint8)
    marker = cv2.aruco.generateImageMarker(aruco_dictionary("DICT_4X4_50"), 31, 200)
    image[200:400, 300:500] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    calibration = CameraCalibration(
        camera_id="cam_0",
        source=5,
        image_size=(800, 600),
        camera_matrix=[[800, 0, 400], [0, 800, 300], [0, 0, 1]],
        distortion=[0] * 8,
        world_from_camera=np.eye(4).tolist(),
        intrinsic_median_error_px=0.1,
        intrinsic_p95_error_px=0.2,
        captured_at=datetime.now(UTC).isoformat(),
        opencv_version=cv2.__version__,
        board_checksum="test",
    )
    detector = MarkerDetector(
        ArucoConfig(
            auto_mobile_markers=AutoMobileMarkerConfig(enabled=True),
            reference_markers=[
                ReferenceMarkerConfig(
                    id=31,
                    size_m=0.07,
                    position_m=(0, 0, 0),
                    orientation_xyzw=(0, 0, 0, 1),
                )
            ],
        ),
        {"cam_0": calibration},
    )
    observations, _, ids = detector.detect("cam_0", image, 1, 2)
    assert ids is not None
    assert observations == []

"""OpenCV ArUco dictionary resolution and detector parameter factories."""

from __future__ import annotations

import cv2

from .config import ArucoConfig


def aruco_dictionary(name: str):
    """Resolve one of the predefined OpenCV ArUco dictionaries by name."""
    dictionary_id = getattr(cv2.aruco, name, None)
    if dictionary_id is None:
        raise ValueError(f"unknown ArUco dictionary: {name}")
    return cv2.aruco.getPredefinedDictionary(dictionary_id)


def detector_parameters_from_config(aruco: ArucoConfig) -> cv2.aruco.DetectorParameters:
    """Detector parameters driven by the configured ArUco settings."""
    parameters = cv2.aruco.DetectorParameters()
    parameters.adaptiveThreshWinSizeMin = aruco.adaptive_thresh_win_size_min
    parameters.adaptiveThreshWinSizeMax = aruco.adaptive_thresh_win_size_max
    parameters.perspectiveRemovePixelPerCell = aruco.perspective_remove_pixel_per_cell
    parameters.errorCorrectionRate = aruco.error_correction_rate
    parameters.cornerRefinementMethod = (
        cv2.aruco.CORNER_REFINE_SUBPIX
        if aruco.corner_refinement
        else cv2.aruco.CORNER_REFINE_NONE
    )
    return parameters


def subpixel_detector_parameters() -> cv2.aruco.DetectorParameters:
    """Default parameters with subpixel corner refinement, for mapping tools."""
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return parameters


def create_detector(dictionary_name: str, parameters: cv2.aruco.DetectorParameters | None = None):
    return cv2.aruco.ArucoDetector(
        aruco_dictionary(dictionary_name), parameters or cv2.aruco.DetectorParameters()
    )


def create_subpixel_detector(dictionary_name: str):
    return cv2.aruco.ArucoDetector(
        aruco_dictionary(dictionary_name), subpixel_detector_parameters()
    )


def create_marker_detector(aruco: ArucoConfig):
    return cv2.aruco.ArucoDetector(
        aruco_dictionary(aruco.dictionary), detector_parameters_from_config(aruco)
    )

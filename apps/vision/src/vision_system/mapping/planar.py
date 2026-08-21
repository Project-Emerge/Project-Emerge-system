"""2D planar world mapping from single photos, live views, or reference frames."""

from __future__ import annotations

import argparse
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.aruco import aruco_dictionary
from ..core.config import (
    AppConfig,
    ArucoConfig,
    CameraConfig,
    ReferenceMarkerConfig,
    load_config,
    save_json,
)
from ..pipeline.capture import apply_camera_properties, apply_digital_zoom, open_video_capture
from ..transport.diagnostics import configure_diagnostics
from ..transport.diagnostics import event as diagnostic_event

WINDOW_NAME = "VisionSystem - mappa reference marker"
HEADER_HEIGHT = 105
MAX_DISPLAY_WIDTH = 1400
MAX_DISPLAY_HEIGHT = 800
CAPTURE_WINDOW_NAME = "VisionSystem - acquisizione scena reference"
CAPTURE_HEADER_HEIGHT = 120
CAPTURE_TILE_WIDTH = 560
CAPTURE_TILE_HEIGHT = 315
CAPTURE_GRID_COLUMNS = 2
AUTO_CAPTURE_STABLE_FRAMES = 4
LOGGER = logging.getLogger(__name__)


def planar_mapping(
    image_points: NDArray[np.float64],
    width_m: float,
    height_m: float,
    mode: Literal["axes", "rectangle"],
) -> NDArray[np.float64]:
    if width_m <= 0 or height_m <= 0:
        raise ValueError("width_m e height_m devono essere positivi")
    points = np.asarray(image_points, dtype=np.float32)
    if mode == "axes":
        if points.shape != (3, 2):
            raise ValueError("la modalita axes richiede origine, punto +X e punto +Y")
        world = np.array([[0, 0], [width_m, 0], [0, height_m]], dtype=np.float32)
        affine = cv2.getAffineTransform(points, world)
        return np.vstack((affine, [0.0, 0.0, 1.0]))
    if points.shape != (4, 2):
        raise ValueError("la modalita rectangle richiede quattro angoli")
    world = np.array(
        [[0, 0], [width_m, 0], [width_m, height_m], [0, height_m]],
        dtype=np.float32,
    )
    homography, _ = cv2.findHomography(points, world, method=0)
    if homography is None:
        raise ValueError("impossibile calcolare la trasformazione 2D")
    return homography


def image_to_world(
    points: NDArray[np.floating], mapping: NDArray[np.floating]
) -> NDArray[np.float64]:
    values = np.asarray(points, dtype=np.float64).reshape(1, -1, 2)
    return cv2.perspectiveTransform(values, np.asarray(mapping, dtype=np.float64))[0]


def detect_marker_centers(
    image: NDArray[np.uint8], dictionary_name: str
) -> tuple[
    list[tuple[float, float]], list[int], list[NDArray[np.float32]], NDArray[np.int32] | None
]:
    """Return detected marker centers and ids, in image pixel coordinates."""
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(aruco_dictionary(dictionary_name), parameters)
    corners, ids, _ = detector.detectMarkers(image)
    if ids is None:
        return [], [], corners, ids
    centers = [
        tuple(float(value) for value in np.mean(np.asarray(marker_corners).reshape(4, 2), axis=0))
        for marker_corners in corners
    ]
    return centers, [int(value) for value in ids.reshape(-1)], corners, ids


def detect_planar_references(
    image: NDArray[np.uint8],
    mapping: NDArray[np.float64],
    dictionary_name: str,
    marker_size_m: float,
    plane_z_m: float = 0.0,
    uncertainty_m: float = 0.005,
    selected_ids: set[int] | None = None,
) -> tuple[list[ReferenceMarkerConfig], list[NDArray[np.float32]], NDArray[np.int32] | None]:
    if marker_size_m <= 0:
        raise ValueError("marker_size_m deve essere positivo")
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(aruco_dictionary(dictionary_name), parameters)
    corners, ids, _ = detector.detectMarkers(image)
    references: list[ReferenceMarkerConfig] = []
    if ids is None:
        return references, corners, ids
    for marker_corners, marker_id_raw in zip(corners, ids.reshape(-1), strict=True):
        marker_id = int(marker_id_raw)
        if selected_ids is not None and marker_id not in selected_ids:
            continue
        pixels = np.asarray(marker_corners, dtype=np.float64).reshape(4, 2)
        world = image_to_world(pixels, mapping)
        center = np.mean(world, axis=0)
        local_x = world[1] - world[0]
        yaw = math.atan2(float(local_x[1]), float(local_x[0]))
        half_yaw = yaw / 2
        references.append(
            ReferenceMarkerConfig(
                id=marker_id,
                size_m=marker_size_m,
                position_m=(float(center[0]), float(center[1]), plane_z_m),
                orientation_xyzw=(0.0, 0.0, math.sin(half_yaw), math.cos(half_yaw)),
                uncertainty_m=uncertainty_m,
                name=f"ref-{marker_id}",
            )
        )
    references.sort(key=lambda marker: marker.id)
    return references, corners, ids


def config_with_references(config: AppConfig, references: list[ReferenceMarkerConfig]) -> AppConfig:
    aruco_payload = config.aruco.model_dump(mode="python")
    aruco_payload["reference_markers"] = references
    aruco = ArucoConfig.model_validate(aruco_payload)
    return config.model_copy(update={"aruco": aruco, "revision": config.revision + 1})


def config_with_anchor_frame(
    config: AppConfig,
    anchor_ids: list[int],
    x_distance_m: float,
    y_distance_m: float,
    plane_z_m: float,
) -> AppConfig:
    """Fix the world frame on the four selected anchor markers (origin, +X, +X+Y, +Y)."""
    if len(anchor_ids) != 4:
        raise ValueError("sono necessari quattro anchor marker")
    aruco_payload = config.aruco.model_dump(mode="python")
    aruco_payload["anchor_frame"] = {
        "origin_id": anchor_ids[0],
        "x_axis_id": anchor_ids[1],
        "opposite_id": anchor_ids[2],
        "y_axis_id": anchor_ids[3],
        "x_distance_m": x_distance_m,
        "y_distance_m": y_distance_m,
        "plane_z_m": plane_z_m,
    }
    aruco = ArucoConfig.model_validate(aruco_payload)
    return config.model_copy(update={"aruco": aruco})


def selected_capture_cameras(config: AppConfig, camera_id: str) -> list[CameraConfig]:
    if camera_id == "all":
        return config.cameras
    selected = [camera for camera in config.cameras if camera.id == camera_id]
    if not selected:
        raise ValueError(f"camera sconosciuta: {camera_id}")
    return selected


@dataclass
class LiveCameraPanel:
    camera: CameraConfig
    capture: cv2.VideoCapture
    frame: NDArray[np.uint8]
    online: bool
    detected_ids: list[int] = field(default_factory=list)

    def close(self) -> None:
        self.capture.release()


class LiveSceneCapture:
    def __init__(
        self,
        cameras: list[CameraConfig],
        dictionary_name: str,
        auto_anchor_ids: list[int] | None = None,
        auto_min_markers: int | None = None,
    ) -> None:
        self.cameras = cameras
        parameters = cv2.aruco.DetectorParameters()
        parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self.detector = cv2.aruco.ArucoDetector(aruco_dictionary(dictionary_name), parameters)
        self.panels: list[LiveCameraPanel] = []
        self.selected_index = 0
        self.auto_anchor_ids = list(auto_anchor_ids or [])
        self.auto_min_markers = auto_min_markers
        self.auto_stable: dict[str, int] = {}
        if self.auto_anchor_ids:
            self.message = "Auto: attendo che i quattro anchor siano visibili..."
        elif self.auto_min_markers is not None:
            self.message = f"Auto: attendo almeno {self.auto_min_markers} marker..."
        else:
            self.message = "Seleziona una camera e premi SPAZIO per scattare"

    @staticmethod
    def _placeholder(camera: CameraConfig) -> NDArray[np.uint8]:
        frame = np.full((CAPTURE_TILE_HEIGHT, CAPTURE_TILE_WIDTH, 3), 28, dtype=np.uint8)
        cv2.putText(
            frame,
            f"{camera.id}: non disponibile",
            (24, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (80, 180, 255),
            2,
        )
        return frame

    def open(self, timeout_s: float = 2.0) -> None:
        for camera in self.cameras:
            capture = open_video_capture(camera.source)
            accepted = apply_camera_properties(capture, camera)
            deadline = time.monotonic() + timeout_s
            frame = None
            while capture.isOpened() and time.monotonic() < deadline:
                ok, candidate = capture.read()
                if ok:
                    frame = apply_digital_zoom(candidate, camera.digital_zoom)
                    break
            self.panels.append(
                LiveCameraPanel(
                    camera=camera,
                    capture=capture,
                    frame=frame if frame is not None else self._placeholder(camera),
                    online=frame is not None,
                )
            )
            diagnostic_event(
                LOGGER,
                "reference_capture_camera_opened",
                level=logging.INFO if frame is not None else logging.WARNING,
                camera_id=camera.id,
                source=camera.source,
                online=frame is not None,
                requested_size=[camera.width, camera.height],
                frame_shape=frame.shape if frame is not None else None,
                digital_zoom=camera.digital_zoom,
                accepted_controls=accepted,
            )

    def close(self) -> None:
        for panel in self.panels:
            panel.close()
        self.panels.clear()

    def _mouse(self, event: int, x: int, y: int, flags: int, userdata) -> None:
        if event != cv2.EVENT_LBUTTONDOWN or y < CAPTURE_HEADER_HEIGHT:
            return
        column = x // CAPTURE_TILE_WIDTH
        row = (y - CAPTURE_HEADER_HEIGHT) // CAPTURE_TILE_HEIGHT
        index = row * CAPTURE_GRID_COLUMNS + column
        if 0 <= index < len(self.panels):
            self.selected_index = index
            self.message = f"Selezionata {self.panels[index].camera.id}; premi SPAZIO"

    def _refresh(self) -> None:
        for panel in self.panels:
            ok, frame = panel.capture.read()
            panel.online = ok
            if not ok:
                continue
            panel.frame = apply_digital_zoom(frame, panel.camera.digital_zoom)
            _, ids, _ = self.detector.detectMarkers(panel.frame)
            detected_ids = (
                sorted(int(value) for value in ids.reshape(-1)) if ids is not None else []
            )
            if detected_ids != panel.detected_ids:
                diagnostic_event(
                    LOGGER,
                    "reference_capture_ids_changed",
                    camera_id=panel.camera.id,
                    detected_ids=detected_ids,
                )
            panel.detected_ids = detected_ids

    def _render(self) -> NDArray[np.uint8]:
        rows = max(1, math.ceil(len(self.panels) / CAPTURE_GRID_COLUMNS))
        image = np.full(
            (
                CAPTURE_HEADER_HEIGHT + rows * CAPTURE_TILE_HEIGHT,
                CAPTURE_GRID_COLUMNS * CAPTURE_TILE_WIDTH,
                3,
            ),
            25,
            dtype=np.uint8,
        )
        lines = [
            "Acquisizione live della scena dei reference marker",
            self.message,
            "Mouse o tasti 1-4: seleziona | SPAZIO: scatta | ESC: annulla",
        ]
        for index, line in enumerate(lines):
            cv2.putText(
                image,
                line,
                (18, 30 + index * 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62 if index else 0.74,
                (80, 220, 255) if index == 1 else (240, 240, 240),
                2,
            )
        for index, panel in enumerate(self.panels):
            row, column = divmod(index, CAPTURE_GRID_COLUMNS)
            x = column * CAPTURE_TILE_WIDTH
            y = CAPTURE_HEADER_HEIGHT + row * CAPTURE_TILE_HEIGHT
            tile = cv2.resize(panel.frame, (CAPTURE_TILE_WIDTH, CAPTURE_TILE_HEIGHT))
            if not panel.online:
                tile = (tile * 0.4).astype(np.uint8)
            image[y : y + CAPTURE_TILE_HEIGHT, x : x + CAPTURE_TILE_WIDTH] = tile
            selected = index == self.selected_index
            color = (0, 220, 255) if selected else (100, 100, 100)
            cv2.rectangle(
                image,
                (x + 2, y + 2),
                (x + CAPTURE_TILE_WIDTH - 3, y + CAPTURE_TILE_HEIGHT - 3),
                color,
                4,
            )
            ids_text = ",".join(map(str, panel.detected_ids)) or "nessuno"
            label = (
                f"{index + 1}: {panel.camera.id} source {panel.camera.source} | marker: {ids_text}"
            )
            cv2.rectangle(
                image,
                (x + 7, y + 7),
                (x + CAPTURE_TILE_WIDTH - 7, y + 45),
                (0, 0, 0),
                -1,
            )
            cv2.putText(
                image,
                label,
                (x + 15, y + 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.57,
                (255, 255, 255),
                2,
            )
        return image

    def run(self) -> tuple[NDArray[np.uint8], str] | None:
        self.open()
        cv2.namedWindow(CAPTURE_WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(CAPTURE_WINDOW_NAME, self._mouse)
        try:
            while True:
                self._refresh()
                auto_panel = self._auto_capture_candidate()
                cv2.imshow(CAPTURE_WINDOW_NAME, self._render())
                key = cv2.waitKey(30) & 0xFF
                if key in (27, ord("q")):
                    diagnostic_event(LOGGER, "reference_live_capture_cancelled")
                    return None
                if auto_panel is not None:
                    diagnostic_event(
                        LOGGER,
                        "reference_live_frame_captured",
                        camera_id=auto_panel.camera.id,
                        source=auto_panel.camera.source,
                        detected_ids=auto_panel.detected_ids,
                        frame_shape=auto_panel.frame.shape,
                        trigger="auto_anchors",
                    )
                    return auto_panel.frame.copy(), auto_panel.camera.id
                if ord("1") <= key <= ord("4"):
                    index = key - ord("1")
                    if index < len(self.panels):
                        self.selected_index = index
                        self.message = f"Selezionata {self.panels[index].camera.id}"
                elif key == ord(" "):
                    panel = self.panels[self.selected_index]
                    if panel.online:
                        diagnostic_event(
                            LOGGER,
                            "reference_live_frame_captured",
                            camera_id=panel.camera.id,
                            source=panel.camera.source,
                            detected_ids=panel.detected_ids,
                            frame_shape=panel.frame.shape,
                            trigger="manual",
                        )
                        return panel.frame.copy(), panel.camera.id
                    self.message = f"{panel.camera.id} non e disponibile"
        finally:
            self.close()
            cv2.destroyWindow(CAPTURE_WINDOW_NAME)

    def _auto_capture_candidate(self) -> LiveCameraPanel | None:
        if not self.auto_anchor_ids and self.auto_min_markers is None:
            return None
        required = set(self.auto_anchor_ids)
        best: LiveCameraPanel | None = None
        for panel in self.panels:
            if not panel.online:
                continue
            if self.auto_anchor_ids:
                visible = required <= set(panel.detected_ids)
            else:
                assert self.auto_min_markers is not None
                visible = len(panel.detected_ids) >= self.auto_min_markers
            count = self.auto_stable.get(panel.camera.id, 0)
            count = count + 1 if visible else 0
            self.auto_stable[panel.camera.id] = count
            if count >= AUTO_CAPTURE_STABLE_FRAMES:
                best = panel
                break
        if best is not None:
            self.message = "Marker visibili: cattura automatica"
        elif self.auto_stable:
            progress = "/".join(
                f"{camera_id}:{self.auto_stable.get(camera_id, 0)}/{AUTO_CAPTURE_STABLE_FRAMES}"
                for camera_id in sorted(self.auto_stable)
            )
            target = (
                "i quattro anchor" if self.auto_anchor_ids
                else f"almeno {self.auto_min_markers} marker"
            )
            self.message = f"Auto: attendo {target}... {progress}"
        return best


@dataclass
class ControlPointPicker:
    image: NDArray[np.uint8]
    labels: list[str]
    points: list[tuple[float, float]] = field(default_factory=list)
    snap_centers: list[tuple[float, float]] = field(default_factory=list)
    snap_ids: list[int] = field(default_factory=list)
    snap_radius_px: float = 40.0
    selected_ids: list[int] = field(default_factory=list)
    message: str | None = None

    def __post_init__(self) -> None:
        height, width = self.image.shape[:2]
        self.scale = min(MAX_DISPLAY_WIDTH / width, MAX_DISPLAY_HEIGHT / height, 1.0)
        self.header_height = HEADER_HEIGHT + (34 if self.snap_centers else 0)
        self.preview = cv2.resize(
            self.image,
            (max(1, round(width * self.scale)), max(1, round(height * self.scale))),
            interpolation=cv2.INTER_AREA,
        )

    def _mouse(self, event: int, x: int, y: int, flags: int, userdata) -> None:
        if event == cv2.EVENT_RBUTTONDOWN and self.points:
            removed = self.points.pop()
            if self.selected_ids:
                self.selected_ids.pop()
            self.message = None
            diagnostic_event(
                LOGGER,
                "reference_control_point_removed",
                point=removed,
                remaining=len(self.points),
            )
            return
        if event != cv2.EVENT_LBUTTONDOWN or y < self.header_height:
            return
        if len(self.points) >= len(self.labels):
            return
        image_y = y - self.header_height
        if x >= self.preview.shape[1] or image_y >= self.preview.shape[0]:
            return
        picked = (x / self.scale, image_y / self.scale)
        if self.snap_centers:
            best_index = None
            best_distance = self.snap_radius_px
            for index, center in enumerate(self.snap_centers):
                if self.snap_ids[index] in self.selected_ids:
                    continue
                distance = math.hypot(center[0] - picked[0], center[1] - picked[1])
                distance *= self.scale
                if distance <= best_distance:
                    best_distance = distance
                    best_index = index
            if best_index is None:
                self.message = (
                    f"Nessun marker entro {self.snap_radius_px:.0f}px dal click; "
                    "clicca vicino al centro di un marker non ancora selezionato"
                )
                return
            picked = self.snap_centers[best_index]
            marker_id = self.snap_ids[best_index]
            self.selected_ids.append(marker_id)
            diagnostic_event(
                LOGGER,
                "reference_anchor_marker_selected",
                marker_id=marker_id,
                label=self.labels[len(self.points)],
                point=picked,
            )
        self.points.append(picked)
        self.message = None
        diagnostic_event(
            LOGGER,
            "reference_control_point_added",
            label=self.labels[len(self.points) - 1],
            point=self.points[-1],
        )

    def _render(self) -> NDArray[np.uint8]:
        canvas = np.full(
            (self.header_height + self.preview.shape[0], self.preview.shape[1], 3),
            28,
            dtype=np.uint8,
        )
        canvas[self.header_height :] = self.preview
        next_label = (
            self.labels[len(self.points)] if len(self.points) < len(self.labels) else "fine"
        )
        lines = [
            "Definizione del piano world XY",
            f"Prossimo punto: {next_label}",
        ]
        if self.snap_centers:
            lines.append(
                self.message
                or "Clicca vicino al centro del marker per selezionarlo come anchor"
            )
        lines.append("Click sinistro: aggiungi | destro: annulla | ENTER: conferma | ESC: esci")
        for index, line in enumerate(lines):
            cv2.putText(
                canvas,
                line,
                (16, 28 + index * 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62 if index else 0.75,
                (255, 255, 255) if index != 1 else (80, 220, 255),
                2,
            )
        for center, marker_id in zip(self.snap_centers, self.snap_ids, strict=True):
            display = (
                round(center[0] * self.scale),
                round(center[1] * self.scale) + self.header_height,
            )
            used = marker_id in self.selected_ids
            color = (0, 220, 0) if used else (150, 150, 150)
            cv2.circle(canvas, display, 7, color, 2)
            cv2.putText(
                canvas,
                f"ID {marker_id}",
                (display[0] + 10, display[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
            )
        for index, point in enumerate(self.points):
            display = (
                round(point[0] * self.scale),
                round(point[1] * self.scale) + self.header_height,
            )
            cv2.circle(canvas, display, 7, (0, 220, 255), -1)
            cv2.putText(
                canvas,
                self.labels[index],
                (display[0] + 10, display[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 220, 255),
                2,
            )
        return canvas

    def run(self) -> NDArray[np.float64] | None:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW_NAME, self._mouse)
        try:
            while True:
                cv2.imshow(WINDOW_NAME, self._render())
                key = cv2.waitKey(20) & 0xFF
                if key in (27, ord("q")):
                    diagnostic_event(
                        LOGGER, "reference_control_points_cancelled", points=self.points
                    )
                    return None
                if key in (8, 127) and self.points:
                    removed = self.points.pop()
                    if self.selected_ids:
                        self.selected_ids.pop()
                    self.message = None
                    diagnostic_event(
                        LOGGER,
                        "reference_control_point_removed",
                        point=removed,
                        remaining=len(self.points),
                    )
                if key in (10, 13) and len(self.points) == len(self.labels):
                    diagnostic_event(
                        LOGGER,
                        "reference_control_points_confirmed",
                        points=self.points,
                        selected_ids=self.selected_ids,
                    )
                    return np.asarray(self.points, dtype=np.float64)
        finally:
            cv2.destroyWindow(WINDOW_NAME)


def annotated_reference_image(
    image: NDArray[np.uint8],
    corners: list[NDArray[np.float32]],
    ids: NDArray[np.int32] | None,
    references: list[ReferenceMarkerConfig],
    control_points: NDArray[np.float64],
    control_labels: list[str],
) -> NDArray[np.uint8]:
    result = image.copy()
    if ids is not None:
        cv2.aruco.drawDetectedMarkers(result, corners, ids)
    by_id = {marker.id: marker for marker in references}
    if ids is not None:
        for marker_corners, marker_id_raw in zip(corners, ids.reshape(-1), strict=True):
            marker_id = int(marker_id_raw)
            marker = by_id.get(marker_id)
            if marker is None:
                continue
            center = np.mean(np.asarray(marker_corners).reshape(4, 2), axis=0).astype(int)
            yaw = 2 * math.atan2(marker.orientation_xyzw[2], marker.orientation_xyzw[3])
            label = (
                f"ID {marker_id}: X={marker.position_m[0]:.3f} "
                f"Y={marker.position_m[1]:.3f} yaw={math.degrees(yaw):.1f}deg"
            )
            cv2.putText(
                result,
                label,
                (int(center[0]) + 8, int(center[1]) - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 0, 255),
                2,
            )
    for point, label in zip(control_points, control_labels, strict=True):
        pixel = tuple(np.rint(point).astype(int))
        cv2.circle(result, pixel, 8, (0, 220, 255), -1)
        cv2.putText(
            result,
            label,
            (pixel[0] + 10, pixel[1] - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 160, 255),
            2,
        )
    return result


def _positive_value(value: float | None, prompt: str) -> float:
    if value is None:
        value = float(input(prompt).strip())
    if value <= 0:
        raise ValueError(f"{prompt.split(':', 1)[0]} deve essere positivo")
    return value


def reference_mapper_main() -> None:
    parser = argparse.ArgumentParser(
        description="Crea reference-markers.json da una camera live, foto o piantina 2D"
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--image", type=Path, help="usa un'immagine esistente")
    source_group.add_argument(
        "--camera",
        help="camera configurata da acquisire, es. cam_0; 'all' mostra il mosaico",
    )
    parser.add_argument("--output", type=Path, default=Path("reference-markers.json"))
    parser.add_argument("--annotated", type=Path)
    parser.add_argument(
        "--capture-output",
        type=Path,
        help="per acquisizione live, percorso dello scatto originale",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="configurazione da aggiornare con i reference marker rilevati",
    )
    parser.add_argument(
        "--config-output",
        type=Path,
        help="scrive la config aggiornata qui; senza questa opzione sovrascrive --config",
    )
    parser.add_argument(
        "--mode",
        choices=("rectangle", "axes", "anchors"),
        default="rectangle",
        help="anchors: seleziona con il mouse i quattro marker anchor (origine, +X, +X+Y, +Y)",
    )
    parser.add_argument("--width-m", type=float, help="distanza reale dall'origine al punto +X")
    parser.add_argument("--height-m", type=float, help="distanza reale dall'origine al punto +Y")
    parser.add_argument("--marker-size-m", type=float, help="lato nero dei marker in metri")
    parser.add_argument("--plane-z-m", type=float, default=0.0)
    parser.add_argument("--uncertainty-m", type=float, default=0.005)
    parser.add_argument("--dictionary", default="DICT_4X4_50")
    parser.add_argument("--ids", type=int, nargs="+", help="considera solo questi ID")
    parser.add_argument(
        "--anchor-ids",
        type=int,
        nargs=4,
        metavar=("ORIGINE", "+X", "+X+Y", "+Y"),
        help=(
            "quattro ID in ordine origine, +X, +X+Y, +Y; con --mode anchors sostituisce "
            "la selezione manuale dei marker"
        ),
    )
    parser.add_argument(
        "--auto-capture",
        action="store_true",
        help=(
            "cattura automaticamente appena i marker sono visibili stabilmente; richiede "
            "--mode anchors. Usa gli ID dell'anchor_frame del config o --anchor-ids; senza "
            "nessuno dei due scatta quando vede almeno 4 marker e poi chiede la selezione"
        ),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics("vision-reference-map")
    print(f"Log diagnostico: {diagnostic_path}")
    diagnostic_event(
        LOGGER,
        "reference_mapping_started",
        image=args.image,
        config=args.config,
        camera=args.camera or "all",
        output=args.output,
        mode=args.mode,
        dictionary=args.dictionary,
        selected_ids=args.ids,
    )

    if args.config_output and not args.config:
        parser.error("--config-output richiede --config")
    if args.image is None and args.config is None:
        parser.error("senza --image serve --config per aprire le camere")
    if args.output.exists() and not args.force:
        parser.error(f"{args.output} esiste gia; usa --force per sovrascriverlo")
    config_target = args.config_output or args.config
    if config_target and config_target != args.config and config_target.exists() and not args.force:
        parser.error(f"{config_target} esiste gia; usa --force per sovrascriverlo")
    base_config: AppConfig | None = None
    if args.config:
        try:
            base_config = load_config(args.config)
            diagnostic_event(
                LOGGER,
                "reference_mapping_config_loaded",
                revision=base_config.revision,
                camera_sources={camera.id: camera.source for camera in base_config.cameras},
                existing_reference_ids=sorted(base_config.references_by_id()),
            )
        except (OSError, ValueError) as error:
            parser.error(f"configurazione non valida: {error}")

    auto_anchor_ids: list[int] | None = None
    auto_min_markers: int | None = None
    if args.anchor_ids:
        if len(set(args.anchor_ids)) != 4:
            parser.error("--anchor-ids richiede quattro ID diversi")
        auto_anchor_ids = list(args.anchor_ids)
    if args.auto_capture:
        if args.mode != "anchors":
            parser.error("--auto-capture richiede --mode anchors")
        if args.image is not None:
            parser.error("--auto-capture richiede l'acquisizione live, non --image")
        if auto_anchor_ids is None:
            anchor_frame = base_config.aruco.anchor_frame if base_config is not None else None
            if anchor_frame is not None:
                if anchor_frame.opposite_id is None:
                    parser.error("--auto-capture richiede anchor_frame con opposite_id")
                auto_anchor_ids = [
                    anchor_frame.origin_id,
                    anchor_frame.x_axis_id,
                    anchor_frame.opposite_id,
                    anchor_frame.y_axis_id,
                ]
            else:
                auto_min_markers = 4
        diagnostic_event(
            LOGGER,
            "reference_auto_capture_armed",
            anchor_ids=auto_anchor_ids,
            min_markers=auto_min_markers,
        )

    capture_path: Path | None = None
    captured_camera_id: str | None = None
    if args.image is not None:
        image = cv2.imread(str(args.image))
        if image is None:
            parser.error(f"impossibile leggere l'immagine: {args.image}")
        diagnostic_event(
            LOGGER, "reference_source_image_loaded", path=args.image, shape=image.shape
        )
    else:
        assert base_config is not None
        try:
            cameras = selected_capture_cameras(base_config, args.camera or "all")
        except ValueError as error:
            parser.error(str(error))
        captured = LiveSceneCapture(
            cameras, args.dictionary, auto_anchor_ids, auto_min_markers
        ).run()
        if captured is None:
            print("Operazione annullata: nessun file scritto")
            return
        image, captured_camera_id = captured
        capture_path = args.capture_output or args.output.with_name(
            f"{args.output.stem}-capture.jpg"
        )
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(capture_path), image):
            parser.error(f"impossibile scrivere lo scatto: {capture_path}")
        diagnostic_event(
            LOGGER,
            "reference_capture_written",
            path=capture_path,
            camera_id=captured_camera_id,
            shape=image.shape,
        )
    width_m = args.width_m
    height_m = args.height_m
    if args.mode == "anchors" and base_config is not None:
        anchor_frame = base_config.aruco.anchor_frame
        if anchor_frame is not None:
            width_m = width_m if width_m is not None else anchor_frame.x_distance_m
            height_m = height_m if height_m is not None else anchor_frame.y_distance_m
    try:
        width_m = _positive_value(
            width_m,
            "Distanza origine -> +X in metri (distanza tra gli anchor): "
            if args.mode == "anchors"
            else "Distanza origine -> +X in metri: ",
        )
        height_m = _positive_value(
            height_m,
            "Distanza origine -> +Y in metri (distanza tra gli anchor): "
            if args.mode == "anchors"
            else "Distanza origine -> +Y in metri: ",
        )
        marker_size_m = _positive_value(args.marker_size_m, "Lato nero marker in metri: ")
    except (ValueError, EOFError) as error:
        parser.error(str(error))

    labels = ["origine (0,0)", "+X", "+Y"]
    if args.mode in ("rectangle", "anchors"):
        labels = ["origine (0,0)", "+X", "+X,+Y", "+Y"]
    anchor_ids: list[int] = []
    if auto_anchor_ids is not None:
        centers, detected_ids, _, _ = detect_marker_centers(image, args.dictionary)
        by_id = dict(zip(detected_ids, centers, strict=True))
        missing = [marker_id for marker_id in auto_anchor_ids if marker_id not in by_id]
        if missing:
            parser.error(f"anchor non rilevati nello scatto: {sorted(missing)}")
        points = np.asarray(
            [by_id[marker_id] for marker_id in auto_anchor_ids], dtype=np.float64
        )
        anchor_ids = list(auto_anchor_ids)
        diagnostic_event(
            LOGGER,
            "reference_anchor_selection_completed",
            mode=args.mode,
            anchor_ids=anchor_ids,
            trigger="auto_capture",
        )
    else:
        snap_centers: list[tuple[float, float]] = []
        snap_ids: list[int] = []
        if args.mode == "anchors":
            snap_centers, snap_ids, _, _ = detect_marker_centers(image, args.dictionary)
            if not snap_ids:
                parser.error(
                    "nessun marker ArUco rilevato nell'immagine; "
                    "impossibile selezionare gli anchor"
                )
        picker = ControlPointPicker(
            image,
            labels,
            snap_centers=snap_centers,
            snap_ids=snap_ids,
        )
        points = picker.run()
        if points is None:
            print("Operazione annullata: nessun file scritto")
            return
        anchor_ids = list(picker.selected_ids)
        diagnostic_event(
            LOGGER,
            "reference_anchor_selection_completed",
            mode=args.mode,
            anchor_ids=anchor_ids,
        )
    try:
        mapping = planar_mapping(
            points,
            width_m,
            height_m,
            "rectangle" if args.mode == "anchors" else args.mode,
        )
        references, corners, ids = detect_planar_references(
            image,
            mapping,
            args.dictionary,
            marker_size_m,
            args.plane_z_m,
            args.uncertainty_m,
            set(args.ids) if args.ids else None,
        )
        detected_ids = sorted(int(value) for value in ids.reshape(-1)) if ids is not None else []
        diagnostic_event(
            LOGGER,
            "reference_mapping_computed",
            width_m=width_m,
            height_m=height_m,
            marker_size_m=marker_size_m,
            plane_z_m=args.plane_z_m,
            control_points=points.tolist(),
            anchor_ids=anchor_ids,
            mapping=mapping.tolist(),
            mapping_condition_number=float(np.linalg.cond(mapping)),
            detected_ids=detected_ids,
            selected_reference_ids=[marker.id for marker in references],
            references=[
                {
                    "id": marker.id,
                    "position_m": marker.position_m,
                    "orientation_xyzw": marker.orientation_xyzw,
                }
                for marker in references
            ],
        )
    except ValueError as error:
        parser.error(str(error))
    if not references:
        parser.error("nessun marker ArUco rilevato nell'immagine")

    updated_config: AppConfig | None = None
    if base_config is not None:
        try:
            updated_config = config_with_references(base_config, references)
            if args.mode == "anchors":
                updated_config = config_with_anchor_frame(
                    updated_config, anchor_ids, width_m, height_m, args.plane_z_m
                )
        except ValueError as error:
            parser.error(f"configurazione non valida: {error}")

    save_json(
        args.output,
        {"reference_markers": [marker.model_dump(mode="json") for marker in references]},
    )
    annotation_path = args.annotated or args.output.with_name(f"{args.output.stem}-annotated.jpg")
    annotation_path.parent.mkdir(parents=True, exist_ok=True)
    annotated = annotated_reference_image(image, corners, ids, references, points, labels)
    if not cv2.imwrite(str(annotation_path), annotated):
        parser.error(f"impossibile scrivere l'immagine annotata: {annotation_path}")
    if updated_config is not None and config_target is not None:
        save_json(config_target, updated_config)
    diagnostic_event(
        LOGGER,
        "reference_mapping_written",
        output=args.output,
        annotation=annotation_path,
        config_output=config_target if updated_config is not None else None,
        new_revision=updated_config.revision if updated_config is not None else None,
        reference_ids=[marker.id for marker in references],
    )
    print(f"Reference marker scritti in {args.output}")
    if capture_path is not None:
        print(f"Scatto {captured_camera_id} scritto in {capture_path}")
    print(f"Immagine di controllo scritta in {annotation_path}")
    if updated_config is not None and config_target is not None:
        print(f"Configurazione aggiornata in {config_target}")
    print("ID rilevati:", [marker.id for marker in references])
    if args.mode == "anchors":
        print(
            "Anchor selezionati: "
            f"origine={anchor_ids[0]} +X={anchor_ids[1]} +X+Y={anchor_ids[2]} +Y={anchor_ids[3]}"
        )
    if len(references) < 2:
        print("ATTENZIONE: per le estrinseche devono essere visibili almeno due riferimenti")


if __name__ == "__main__":
    reference_mapper_main()

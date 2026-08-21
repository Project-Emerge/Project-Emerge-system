"""World Cartesian frame selection, anchor realignment, and rigid coordinate rebasing."""

from __future__ import annotations

import argparse
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.aruco import aruco_dictionary
from ..core.config import (
    AnchorFrameConfig,
    AppConfig,
    ArucoConfig,
    ReferenceMarkerConfig,
    load_config,
    save_json,
)
from ..core.geometry import matrix_to_quaternion, quaternion_to_matrix
from ..transport.diagnostics import configure_diagnostics
from ..transport.diagnostics import event as diagnostic_event
from .planar import LiveSceneCapture, selected_capture_cameras

WINDOW_NAME = "VisionSystem - selezione origine"
HEADER_HEIGHT = 105
MAX_DISPLAY_WIDTH = 1400
MAX_DISPLAY_HEIGHT = 800
LOGGER = logging.getLogger(__name__)


def _anchor_cycle(frame: AnchorFrameConfig) -> list[int]:
    if frame.opposite_id is None:
        raise ValueError("la selezione visuale richiede quattro ancore e opposite_id")
    return [frame.origin_id, frame.x_axis_id, frame.opposite_id, frame.y_axis_id]


def rebase_anchor_frame(config: AppConfig, origin_id: int) -> AppConfig:
    """Move and rotate the world XY frame to one of its four rectangular anchors."""
    old_frame = config.aruco.anchor_frame
    if old_frame is None:
        raise ValueError("la configurazione non contiene aruco.anchor_frame")
    cycle = _anchor_cycle(old_frame)
    if origin_id not in cycle:
        raise ValueError(f"il marker {origin_id} non appartiene alle quattro ancore")

    index = cycle.index(origin_id)
    new_origin_id = cycle[index]
    new_x_id = cycle[(index + 1) % 4]
    new_opposite_id = cycle[(index + 2) % 4]
    new_y_id = cycle[(index - 1) % 4]

    references = config.references_by_id()
    origin = np.asarray(references[new_origin_id].position_m, dtype=np.float64)
    x_point = np.asarray(references[new_x_id].position_m, dtype=np.float64)
    y_point = np.asarray(references[new_y_id].position_m, dtype=np.float64)
    x_delta = x_point[:2] - origin[:2]
    y_delta = y_point[:2] - origin[:2]
    x_distance = float(np.linalg.norm(x_delta))
    y_distance = float(np.linalg.norm(y_delta))
    if x_distance < 1e-9 or y_distance < 1e-9:
        raise ValueError("le ancore selezionate hanno coordinate coincidenti")

    theta = math.atan2(float(x_delta[1]), float(x_delta[0]))
    cosine, sine = math.cos(theta), math.sin(theta)
    new_from_old_rotation = np.array(
        [[cosine, sine, 0.0], [-sine, cosine, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )

    transformed: list[ReferenceMarkerConfig] = []
    for marker in config.aruco.reference_markers:
        old_position = np.asarray(marker.position_m, dtype=np.float64)
        displacement = old_position - origin
        new_position = new_from_old_rotation @ displacement
        # Keep the configured reference plane at plane_z_m rather than moving it to Z=0.
        new_position[2] += old_frame.plane_z_m
        new_orientation = new_from_old_rotation @ quaternion_to_matrix(
            np.asarray(marker.orientation_xyzw, dtype=np.float64)
        )
        transformed.append(
            marker.model_copy(
                update={
                    "position_m": tuple(float(value) for value in new_position),
                    "orientation_xyzw": tuple(
                        float(value) for value in matrix_to_quaternion(new_orientation)
                    ),
                }
            )
        )

    frame = AnchorFrameConfig(
        origin_id=new_origin_id,
        x_axis_id=new_x_id,
        y_axis_id=new_y_id,
        opposite_id=new_opposite_id,
        x_distance_m=x_distance,
        y_distance_m=y_distance,
        plane_z_m=old_frame.plane_z_m,
    )
    aruco_payload = config.aruco.model_dump(mode="python")
    aruco_payload["reference_markers"] = transformed
    aruco_payload["anchor_frame"] = frame
    aruco = ArucoConfig.model_validate(aruco_payload)
    updated = config.model_copy(update={"aruco": aruco, "revision": config.revision + 1})
    diagnostic_event(
        LOGGER,
        "anchor_frame_rebased",
        old_revision=config.revision,
        new_revision=updated.revision,
        old_frame=old_frame.model_dump(mode="json"),
        new_frame=frame.model_dump(mode="json"),
        rotation_deg=math.degrees(theta),
        transformed_references={
            marker.id: {
                "position_m": marker.position_m,
                "orientation_xyzw": marker.orientation_xyzw,
            }
            for marker in transformed
        },
    )
    return updated


def detect_anchor_corners(
    image: NDArray[np.uint8], config: AppConfig
) -> tuple[list[NDArray[np.float32]], NDArray[np.int32] | None, dict[int, NDArray[np.float32]]]:
    parameters = cv2.aruco.DetectorParameters()
    parameters.adaptiveThreshWinSizeMin = config.aruco.adaptive_thresh_win_size_min
    parameters.adaptiveThreshWinSizeMax = config.aruco.adaptive_thresh_win_size_max
    parameters.perspectiveRemovePixelPerCell = config.aruco.perspective_remove_pixel_per_cell
    parameters.errorCorrectionRate = config.aruco.error_correction_rate
    if config.aruco.corner_refinement:
        parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(aruco_dictionary(config.aruco.dictionary), parameters)
    corners, ids, _ = detector.detectMarkers(image)
    frame = config.aruco.anchor_frame
    if frame is None:
        raise ValueError("la configurazione non contiene aruco.anchor_frame")
    allowed = set(_anchor_cycle(frame))
    anchors: dict[int, NDArray[np.float32]] = {}
    if ids is not None:
        for marker_corners, marker_id_raw in zip(corners, ids.reshape(-1), strict=True):
            marker_id = int(marker_id_raw)
            if marker_id in allowed:
                anchors[marker_id] = np.asarray(marker_corners, dtype=np.float32).reshape(4, 2)
    return corners, ids, anchors


@dataclass
class OriginPicker:
    image: NDArray[np.uint8]
    anchors: dict[int, NDArray[np.float32]]
    current_origin_id: int
    selected_id: int | None = None
    message: str = "Clicca una delle ancore rilevate"

    def __post_init__(self) -> None:
        height, width = self.image.shape[:2]
        self.scale = min(MAX_DISPLAY_WIDTH / width, MAX_DISPLAY_HEIGHT / height, 1.0)
        self.preview = cv2.resize(
            self.image,
            (max(1, round(width * self.scale)), max(1, round(height * self.scale))),
            interpolation=cv2.INTER_AREA,
        )

    def _marker_at(self, point: tuple[float, float]) -> int | None:
        for marker_id, polygon in self.anchors.items():
            if cv2.pointPolygonTest(polygon, point, False) >= 0:
                return marker_id
        if not self.anchors:
            return None
        centers = {
            marker_id: np.mean(polygon, axis=0) for marker_id, polygon in self.anchors.items()
        }
        nearest = min(centers, key=lambda marker_id: np.linalg.norm(centers[marker_id] - point))
        distance = float(np.linalg.norm(centers[nearest] - point))
        sides = np.linalg.norm(
            self.anchors[nearest] - np.roll(self.anchors[nearest], 1, axis=0), axis=1
        )
        return nearest if distance <= max(25.0, float(np.mean(sides))) else None

    def _mouse(self, event: int, x: int, y: int, flags: int, userdata) -> None:
        if event == cv2.EVENT_RBUTTONDOWN:
            self.selected_id = None
            self.message = "Selezione cancellata"
            diagnostic_event(LOGGER, "origin_selection_cleared")
            return
        if event != cv2.EVENT_LBUTTONDOWN or y < HEADER_HEIGHT:
            return
        image_point = (x / self.scale, (y - HEADER_HEIGHT) / self.scale)
        marker_id = self._marker_at(image_point)
        if marker_id is None:
            self.message = "Nessuna ancora in quel punto"
            diagnostic_event(
                LOGGER,
                "origin_click_missed",
                point=image_point,
                visible_anchor_ids=sorted(self.anchors),
            )
            return
        self.selected_id = marker_id
        diagnostic_event(LOGGER, "origin_anchor_selected", marker_id=marker_id)
        self.message = f"ID {marker_id} selezionato; premi ENTER per confermare"

    def _render(self) -> NDArray[np.uint8]:
        annotated = self.preview.copy()
        for marker_id, polygon in self.anchors.items():
            display_polygon = np.rint(polygon * self.scale).astype(np.int32)
            selected = marker_id == self.selected_id
            color = (0, 220, 255) if selected else (40, 210, 40)
            cv2.polylines(annotated, [display_polygon], True, color, 4 if selected else 2)
            center = tuple(np.rint(np.mean(display_polygon, axis=0)).astype(int))
            role = "origine attuale" if marker_id == self.current_origin_id else "ancora"
            cv2.putText(
                annotated,
                f"ID {marker_id} - {role}",
                (center[0] + 10, center[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                color,
                2,
            )
        canvas = np.full(
            (HEADER_HEIGHT + annotated.shape[0], annotated.shape[1], 3), 28, dtype=np.uint8
        )
        canvas[HEADER_HEIGHT:] = annotated
        lines = [
            "Scegli l'ancora che diventera l'origine world",
            self.message,
            "Click: seleziona | destro/Backspace: annulla | ENTER: salva | ESC: esci",
        ]
        for index, line in enumerate(lines):
            cv2.putText(
                canvas,
                line,
                (16, 28 + index * 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62 if index else 0.75,
                (80, 220, 255) if index == 1 else (245, 245, 245),
                2,
            )
        return canvas

    def run(self) -> int | None:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW_NAME, self._mouse)
        try:
            while True:
                cv2.imshow(WINDOW_NAME, self._render())
                key = cv2.waitKey(20) & 0xFF
                if key in (27, ord("q")):
                    diagnostic_event(LOGGER, "origin_selection_cancelled")
                    return None
                if key in (8, 127):
                    self.selected_id = None
                    self.message = "Selezione cancellata"
                if key in (10, 13) and self.selected_id is not None:
                    diagnostic_event(
                        LOGGER, "origin_selection_confirmed", marker_id=self.selected_id
                    )
                    return self.selected_id
        finally:
            cv2.destroyWindow(WINDOW_NAME)


def annotated_origin_image(
    image: NDArray[np.uint8],
    corners: list[NDArray[np.float32]],
    ids: NDArray[np.int32] | None,
    anchors: dict[int, NDArray[np.float32]],
    origin_id: int,
) -> NDArray[np.uint8]:
    result = image.copy()
    if ids is not None:
        cv2.aruco.drawDetectedMarkers(result, corners, ids)
    polygon = np.rint(anchors[origin_id]).astype(np.int32)
    cv2.polylines(result, [polygon], True, (0, 220, 255), 7)
    center = tuple(np.rint(np.mean(polygon, axis=0)).astype(int))
    cv2.putText(
        result,
        f"ORIGINE: ID {origin_id}",
        (center[0] + 12, center[1] - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.85,
        (0, 180, 255),
        3,
    )
    return result


def origin_selector_main() -> None:
    parser = argparse.ArgumentParser(
        description="Seleziona da una camera live o foto quale ancora e l'origine world"
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--image", type=Path, help="usa una foto esistente")
    source_group.add_argument(
        "--camera",
        help="camera configurata da acquisire, es. cam_0; 'all' mostra il mosaico",
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="config di destinazione; se omesso sovrascrive --config",
    )
    parser.add_argument("--annotated", type=Path)
    parser.add_argument(
        "--capture-output",
        type=Path,
        help="per acquisizione live, percorso in cui salvare lo scatto",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics("vision-select-origin")
    print(f"Log diagnostico: {diagnostic_path}")
    diagnostic_event(
        LOGGER,
        "origin_workflow_started",
        image=args.image,
        camera=args.camera or "all",
        config=args.config,
        output=args.output or args.config,
    )

    if args.image is not None and args.capture_output is not None:
        parser.error("--capture-output si usa solo con l'acquisizione da camera")
    target = args.output or args.config
    capture_path = None
    if args.image is not None:
        annotated_path = args.annotated or args.image.with_name(
            f"{args.image.stem}-origin-annotated.jpg"
        )
    else:
        capture_path = args.capture_output or Path("origin-selection-capture.jpg")
        annotated_path = args.annotated or Path("origin-selection-annotated.jpg")
    if target != args.config and target.exists() and not args.force:
        parser.error(f"{target} esiste gia; usa --force per sovrascriverlo")
    if annotated_path.exists() and not args.force:
        parser.error(f"{annotated_path} esiste gia; usa --force per sovrascriverlo")
    if capture_path is not None and capture_path.exists() and not args.force:
        parser.error(f"{capture_path} esiste gia; usa --force per sovrascriverlo")
    try:
        config = load_config(args.config)
        diagnostic_event(
            LOGGER,
            "origin_config_loaded",
            revision=config.revision,
            anchor_frame=(
                config.aruco.anchor_frame.model_dump(mode="json")
                if config.aruco.anchor_frame is not None
                else None
            ),
            reference_ids=sorted(config.references_by_id()),
        )
    except (OSError, ValueError) as error:
        parser.error(f"configurazione non valida: {error}")
    captured_camera_id = None
    if args.image is not None:
        image = cv2.imread(str(args.image))
        if image is None:
            parser.error(f"impossibile leggere l'immagine: {args.image}")
        diagnostic_event(LOGGER, "origin_source_image_loaded", path=args.image, shape=image.shape)
    else:
        try:
            cameras = selected_capture_cameras(config, args.camera or "all")
        except ValueError as error:
            parser.error(str(error))
        captured = LiveSceneCapture(cameras, config.aruco.dictionary).run()
        if captured is None:
            print("Operazione annullata: nessun file scritto")
            return
        image, captured_camera_id = captured
        diagnostic_event(
            LOGGER,
            "origin_live_frame_captured",
            camera_id=captured_camera_id,
            shape=image.shape,
        )
    try:
        corners, ids, anchors = detect_anchor_corners(image, config)
    except ValueError as error:
        parser.error(str(error))
    if not anchors:
        detected_ids = sorted(int(value) for value in ids.reshape(-1)) if ids is not None else []
        diagnostic_event(
            LOGGER,
            "origin_no_anchor_visible",
            level=logging.ERROR,
            detected_ids=detected_ids,
            expected_anchor_ids=_anchor_cycle(config.aruco.anchor_frame),
        )
        parser.error("nessuna delle quattro ancore configurate e visibile nella foto")

    diagnostic_event(
        LOGGER,
        "origin_anchors_detected",
        detected_ids=(sorted(int(value) for value in ids.reshape(-1)) if ids is not None else []),
        visible_anchor_ids=sorted(anchors),
        anchor_centers_px={
            marker_id: np.mean(corners_px, axis=0).tolist()
            for marker_id, corners_px in anchors.items()
        },
    )

    assert config.aruco.anchor_frame is not None
    selected_id = OriginPicker(
        image=image,
        anchors=anchors,
        current_origin_id=config.aruco.anchor_frame.origin_id,
    ).run()
    if selected_id is None:
        print("Operazione annullata: nessun file scritto")
        return
    try:
        updated = rebase_anchor_frame(config, selected_id)
    except ValueError as error:
        parser.error(str(error))

    if capture_path is not None:
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(capture_path), image):
            parser.error(f"impossibile scrivere lo scatto: {capture_path}")
    annotated_path.parent.mkdir(parents=True, exist_ok=True)
    annotated = annotated_origin_image(image, corners, ids, anchors, selected_id)
    if not cv2.imwrite(str(annotated_path), annotated):
        parser.error(f"impossibile scrivere l'immagine annotata: {annotated_path}")
    save_json(target, updated)
    frame = updated.aruco.anchor_frame
    assert frame is not None
    diagnostic_event(
        LOGGER,
        "origin_outputs_written",
        config_output=target,
        capture_output=capture_path,
        annotation_output=annotated_path,
        selected_origin_id=selected_id,
        new_revision=updated.revision,
        new_frame=frame.model_dump(mode="json"),
    )
    print(f"Configurazione aggiornata in {target}")
    if capture_path is not None:
        print(f"Scatto {captured_camera_id} scritto in {capture_path}")
    print(f"Immagine di controllo scritta in {annotated_path}")
    print(
        f"Nuovo frame: origine ID {frame.origin_id}, +X ID {frame.x_axis_id}, "
        f"+Y ID {frame.y_axis_id}"
    )
    print("ATTENZIONE: ricalibra le estrinseche di tutte le camere; le intrinseche non cambiano")


if __name__ == "__main__":
    origin_selector_main()

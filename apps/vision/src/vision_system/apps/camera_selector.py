"""Interactive GUI for visual assignment of physical video sources to cameras."""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.config import AppConfig, load_config, save_json
from ..pipeline.capture import open_video_capture
from ..transport.diagnostics import configure_diagnostics, event

WINDOW_NAME = "VisionSystem - selezione camere"
HEADER_HEIGHT = 115
TILE_WIDTH = 400
TILE_HEIGHT = 225
GRID_COLUMNS = 3
LOGGER = logging.getLogger(__name__)


@dataclass
class CameraPreview:
    source: int
    capture: cv2.VideoCapture
    frame: NDArray[np.uint8]
    online: bool = True

    def close(self) -> None:
        self.capture.release()


def discover_camera_sources(max_index: int = 15) -> list[int]:
    if sys.platform.startswith("linux"):
        detected: list[int] = []
        for path in Path("/dev").glob("video*"):
            suffix = path.name.removeprefix("video")
            if suffix.isdigit() and int(suffix) <= max_index:
                detected.append(int(suffix))
        sources = sorted(set(detected))
    else:
        sources = list(range(max_index + 1))
    event(LOGGER, "camera_sources_discovered", max_index=max_index, sources=sources)
    return sources


def open_previews(sources: list[int], timeout_s: float = 1.0) -> list[CameraPreview]:
    previews: list[CameraPreview] = []
    for source in sources:
        capture = open_video_capture(source)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
        capture.set(cv2.CAP_PROP_FPS, 15)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        deadline = time.monotonic() + timeout_s
        frame = None
        while capture.isOpened() and time.monotonic() < deadline:
            ok, candidate = capture.read()
            if ok:
                frame = candidate
                break
        if frame is None:
            event(
                LOGGER,
                "camera_preview_open_failed",
                level=logging.WARNING,
                source=source,
                capture_opened=capture.isOpened(),
                timeout_s=timeout_s,
            )
            capture.release()
            continue
        event(
            LOGGER,
            "camera_preview_opened",
            source=source,
            frame_shape=frame.shape,
            backend=capture.getBackendName() if capture.isOpened() else None,
            effective_width=capture.get(cv2.CAP_PROP_FRAME_WIDTH),
            effective_height=capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
            effective_fps=capture.get(cv2.CAP_PROP_FPS),
        )
        previews.append(CameraPreview(source, capture, frame))
    return previews


def build_camera_config(
    base: AppConfig, assignments: dict[int, int], only_index: int | None = None
) -> AppConfig:
    if only_index is None:
        if not assignments:
            raise ValueError("at least one camera must be assigned")
        if not set(assignments).issubset(range(len(base.cameras))):
            raise ValueError("invalid camera index in assignments")
        if len(set(assignments.values())) != len(assignments):
            raise ValueError("the selected sources must be different")
        cameras = [
            camera.model_copy(update={"source": assignments[index]})
            for index, camera in enumerate(base.cameras)
            if index in assignments
        ]
    else:
        if set(assignments) != {only_index}:
            raise ValueError(
                f"only logical camera {base.cameras[only_index].id} must be assigned"
            )
        cameras = [
            camera.model_copy(update={"source": assignments[index]})
            if index in assignments
            else camera
            for index, camera in enumerate(base.cameras)
        ]
    return base.model_copy(update={"cameras": cameras, "revision": base.revision + 1})


class CameraSelector:
    def __init__(
        self,
        base: AppConfig,
        sources: list[int],
        max_index: int = 15,
        only_index: int | None = None,
    ) -> None:
        self.base = base
        self.requested_sources = sources
        self.max_index = max_index
        self.only_index = only_index
        self.previews: list[CameraPreview] = []
        self.assignments: dict[int, int] = {}
        self.selected_index: int | None = None
        self.message = (
            f"Clicca una camera e premi {only_index + 1}"
            if only_index is not None
            else "Clicca una camera e premi " + " ".join(
                str(index + 1) for index in range(len(base.cameras))
            )
        )

    def scan(self) -> None:
        self.close()
        sources = self.requested_sources or discover_camera_sources(self.max_index)
        self.previews = open_previews(sources)
        available = {preview.source for preview in self.previews}
        if self.only_index is None:
            self.assignments = {
                index: int(camera.source)
                for index, camera in enumerate(self.base.cameras)
                if isinstance(camera.source, int) and camera.source in available
            }
        else:
            configured = self.base.cameras[self.only_index].source
            self.assignments = (
                {self.only_index: int(configured)}
                if isinstance(configured, int) and configured in available
                else {}
            )
        self.selected_index = 0 if self.previews else None
        event(
            LOGGER,
            "camera_scan_completed",
            requested_sources=sources,
            available_sources=sorted(available),
            restored_assignments={
                self.base.cameras[index].id: source
                for index, source in self.assignments.items()
            },
        )
        if not self.previews:
            self.message = "Nessuna camera video disponibile. Premi R per riprovare o ESC."

    def close(self) -> None:
        for preview in self.previews:
            preview.close()
        self.previews.clear()

    def _mouse(self, event: int, x: int, y: int, flags: int, userdata) -> None:
        if event != cv2.EVENT_LBUTTONDOWN or y < HEADER_HEIGHT:
            return
        column = x // TILE_WIDTH
        row = (y - HEADER_HEIGHT) // TILE_HEIGHT
        index = row * GRID_COLUMNS + column
        if 0 <= index < len(self.previews):
            self.selected_index = index
            keys = " ".join(str(i + 1) for i in range(len(self.base.cameras)))
            self.message = f"Selezionata source {self.previews[index].source}: premi {keys}"

    def _refresh_frames(self) -> None:
        for preview in self.previews:
            ok, frame = preview.capture.read()
            preview.online = ok
            if ok:
                preview.frame = frame

    def _render(self) -> NDArray[np.uint8]:
        rows = max(1, math.ceil(len(self.previews) / GRID_COLUMNS))
        image = np.full(
            (HEADER_HEIGHT + rows * TILE_HEIGHT, GRID_COLUMNS * TILE_WIDTH, 3),
            28,
            dtype=np.uint8,
        )
        cv2.putText(
            image,
            "Selezione camere VisionSystem",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
        )
        cv2.putText(
            image,
            self.message,
            (20, 62),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (80, 220, 255),
            2,
        )
        assignment_items = []
        for index, camera in enumerate(self.base.cameras):
            if index in self.assignments:
                assignment_items.append(f"{camera.id}=source {self.assignments[index]}")
            elif self.only_index is not None:
                assignment_items.append(f"{camera.id}=source {camera.source}")
            else:
                assignment_items.append(f"{camera.id}=-")
        assignment_text = " | ".join(assignment_items)
        cv2.putText(
            image,
            assignment_text,
            (20, 91),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.57,
            (180, 255, 180),
            1,
        )
        help_text = (
            f"Mouse: seleziona | {self.only_index + 1}: assegna | C: pulisci | "
            f"R: riscansiona | ENTER: salva"
            if self.only_index is not None
            else f"Mouse: seleziona | 1-{len(self.base.cameras)}: assegna/rimuovi | "
            f"U: disassegna | C: pulisci | R: riscansiona | ENTER: salva"
        )
        cv2.putText(
            image,
            help_text,
            (20, 110),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.43,
            (210, 210, 210),
            1,
        )
        for index, preview in enumerate(self.previews):
            row, column = divmod(index, GRID_COLUMNS)
            x = column * TILE_WIDTH
            y = HEADER_HEIGHT + row * TILE_HEIGHT
            tile = cv2.resize(preview.frame, (TILE_WIDTH, TILE_HEIGHT))
            if not preview.online:
                tile = (tile * 0.3).astype(np.uint8)
            image[y : y + TILE_HEIGHT, x : x + TILE_WIDTH] = tile
            selected = index == self.selected_index
            color = (0, 220, 255) if selected else (100, 100, 100)
            cv2.rectangle(
                image, (x + 2, y + 2), (x + TILE_WIDTH - 3, y + TILE_HEIGHT - 3), color, 4
            )
            logical = [
                self.base.cameras[logical_index].id
                for logical_index, source in self.assignments.items()
                if source == preview.source
            ]
            label = f"source {preview.source}"
            if logical:
                label += " -> " + ",".join(logical)
            cv2.rectangle(image, (x + 5, y + 5), (x + 250, y + 38), (0, 0, 0), -1)
            cv2.putText(
                image,
                label,
                (x + 12, y + 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.72,
                (255, 255, 255),
                2,
            )
        return image

    def run(self) -> AppConfig | None:
        self.scan()
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(WINDOW_NAME, self._mouse)
        try:
            while True:
                self._refresh_frames()
                cv2.imshow(WINDOW_NAME, self._render())
                key = cv2.waitKey(30) & 0xFF
                if key in (27, ord("q")):
                    event(LOGGER, "camera_selection_cancelled", assignments=self.assignments)
                    return None
                if ord("1") <= key <= ord("1") + len(self.base.cameras) - 1:
                    if self.selected_index is None:
                        self.message = "Prima seleziona una camera con il mouse"
                        continue
                    logical_index = key - ord("1")
                    if self.only_index is not None and logical_index != self.only_index:
                        self.message = (
                            f"Premi {self.only_index + 1} per assegnare la camera a "
                            f"{self.base.cameras[self.only_index].id}"
                        )
                        continue
                    source = self.previews[self.selected_index].source
                    cam_id = self.base.cameras[logical_index].id
                    if self.assignments.get(logical_index) == source:
                        self.assignments.pop(logical_index)
                        event(
                            LOGGER,
                            "camera_unassigned",
                            camera_id=cam_id,
                            source=source,
                            assignments={
                                self.base.cameras[index].id: assigned_source
                                for index, assigned_source in self.assignments.items()
                            },
                        )
                        self.message = f"Assegnazione {cam_id} rimossa da source {source}"
                    else:
                        for previous_logical, previous_source in list(self.assignments.items()):
                            if previous_source == source:
                                self.assignments.pop(previous_logical)
                        self.assignments[logical_index] = source
                        event(
                            LOGGER,
                            "camera_assigned",
                            camera_id=cam_id,
                            source=source,
                            assignments={
                                self.base.cameras[index].id: assigned_source
                                for index, assigned_source in self.assignments.items()
                            },
                        )
                        self.message = f"source {source} assegnata a {cam_id}"
                elif key in (ord("u"), ord("0"), 8, 127):
                    if self.selected_index is not None:
                        source = self.previews[self.selected_index].source
                        removed = [
                            self.base.cameras[log_idx].id
                            for log_idx, src in list(self.assignments.items())
                            if src == source
                        ]
                        for log_idx, src in list(self.assignments.items()):
                            if src == source:
                                self.assignments.pop(log_idx)
                        if removed:
                            event(
                                LOGGER,
                                "camera_unassigned",
                                camera_id=",".join(removed),
                                source=source,
                                assignments={
                                    self.base.cameras[index].id: assigned_source
                                    for index, assigned_source in self.assignments.items()
                                },
                            )
                            self.message = f"Assegnazione rimossa per source {source}"
                        else:
                            self.message = f"Source {source} non era assegnata"
                elif key == ord("c"):
                    self.assignments.clear()
                    event(LOGGER, "camera_assignments_cleared")
                    self.message = "Assegnazioni cancellate"
                elif key == ord("r"):
                    self.message = "Scansione in corso..."
                    self.scan()
                elif key in (10, 13):
                    try:
                        result = build_camera_config(self.base, self.assignments, self.only_index)
                        event(
                            LOGGER,
                            "camera_selection_confirmed",
                            old_revision=self.base.revision,
                            new_revision=result.revision,
                            assignments={camera.id: camera.source for camera in result.cameras},
                        )
                        return result
                    except ValueError as error:
                        event(
                            LOGGER,
                            "camera_selection_rejected",
                            level=logging.WARNING,
                            assignments=self.assignments,
                            reason="at_least_one_camera_required"
                            if not self.assignments
                            else (
                                "single_camera_assignment_required"
                                if self.only_index is not None
                                else "invalid_assignment"
                            ),
                        )
                        if self.only_index is not None:
                            self.message = (
                                f"Assegna la camera a {self.base.cameras[self.only_index].id} "
                                f"(tasto {self.only_index + 1}) prima di salvare"
                            )
                        elif not self.assignments:
                            self.message = "Assegna almeno una camera prima di salvare"
                        else:
                            self.message = str(error)
        finally:
            self.close()
            cv2.destroyWindow(WINDOW_NAME)


def select_camera_config(
    output: Path,
    base_path: Path | None = None,
    sources: list[int] | None = None,
    max_index: int = 15,
    force: bool = False,
    camera_id: str | None = None,
) -> AppConfig | None:
    if output.exists() and not force:
        raise FileExistsError(f"{output} exists; use --force to overwrite it")
    base = load_config(base_path) if base_path else AppConfig()
    only_index: int | None = None
    if camera_id is not None:
        only_index = next(
            (index for index, camera in enumerate(base.cameras) if camera.id == camera_id),
            None,
        )
        if only_index is None:
            raise ValueError(f"camera sconosciuta: {camera_id}")
    selector = CameraSelector(base, sources or [], max_index, only_index)
    result = selector.run()
    if result is not None:
        save_json(output, result)
        event(
            LOGGER,
            "camera_configuration_written",
            output=output,
            revision=result.revision,
            assignments={camera.id: camera.source for camera in result.cameras},
        )
    return result


def camera_selector_main() -> None:
    parser = argparse.ArgumentParser(description="Seleziona visualmente le camere")
    parser.add_argument("--output", type=Path, default=Path("camera-config.json"))
    parser.add_argument("--base", type=Path, help="configurazione da preservare come base")
    parser.add_argument("--sources", type=int, nargs="+", help="indici da mostrare, es. 5 1 2 4")
    parser.add_argument("--max-index", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--camera",
        help="assegna una sola camera logica (cam_0..cam_3); le altre slot restano invariate",
    )
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics("vision-select-cameras")
    print(f"Log diagnostico: {diagnostic_path}")
    try:
        result = select_camera_config(
            args.output,
            args.base,
            args.sources,
            args.max_index,
            args.force,
            args.camera,
        )
    except (FileExistsError, ValueError) as error:
        parser.error(str(error))
    if result is None:
        print("Selezione annullata: nessun file scritto")
    else:
        print(f"Configurazione scritta in {args.output}")
        print("Ordine:", [camera.source for camera in result.cameras])

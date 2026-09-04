"""Interactive GUI for visual assignment of physical video sources to cameras."""

from __future__ import annotations

import argparse
import errno

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable outside Unix
    fcntl = None  # type: ignore[assignment]

import logging
import math
import os
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.config import AppConfig, load_config, save_json
from ..pipeline.capture import PREFERRED_FOURCC, capture_fourcc, open_video_capture
from ..transport.diagnostics import configure_diagnostics, event

WINDOW_NAME = "VisionSystem - selezione camere"
HEADER_HEIGHT = 115
TILE_WIDTH = 400
TILE_HEIGHT = 225
GRID_COLUMNS = 3
LOGGER = logging.getLogger(__name__)
PREVIEW_TIMEOUT_S = 2.0
PREVIEW_RETRY_DELAY_S = 0.15

# linux/videodev2.h.  QUERYCAP lets us reject UVC metadata nodes before OpenCV
# tries to treat every /dev/video* entry as a camera.
VIDIOC_QUERYCAP = 0x80685600
V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
V4L2_CAP_DEVICE_CAPS = 0x80000000
V4L2_CAPABILITY_SIZE = 104


@dataclass(frozen=True)
class PreviewProfile:
    name: str
    fourcc: str | None
    width: int
    height: int
    fps: float


# Prefer compressed video before the first read: opening several UVC cameras in
# their often-uncompressed default mode can exhaust the USB periodic bandwidth.
PREVIEW_PROFILES = (
    PreviewProfile("mjpeg", PREFERRED_FOURCC, 640, 480, 15.0),
    PreviewProfile("mjpeg-low-fps", PREFERRED_FOURCC, 640, 480, 5.0),
    PreviewProfile("native-low-fps", None, 640, 480, 5.0),
)


@dataclass(frozen=True)
class CameraOpenFailure:
    source: int
    reason: str
    attempts: tuple[dict[str, Any], ...]
    node: dict[str, Any]


FAILURE_MESSAGES = {
    "device_missing": "device assente",
    "permission_denied": "permessi negati",
    "device_busy": "device occupato",
    "not_video_capture_node": "nodo metadata",
    "frame_timeout": "nessun frame",
    "v4l2_open_failed": "apertura V4L2",
}


def _failure_message(reason: str) -> str:
    return FAILURE_MESSAGES.get(reason, reason)


@dataclass
class CameraPreview:
    source: int
    capture: cv2.VideoCapture
    frame: NDArray[np.uint8]
    profile: str = "unknown"
    online: bool = True

    def close(self) -> None:
        self.capture.release()


def _decode_c_string(value: bytes | bytearray) -> str:
    return bytes(value).split(b"\0", 1)[0].decode("utf-8", errors="replace")


def inspect_video_node(source: int) -> dict[str, Any]:
    """Return Linux V4L2 identity/capabilities and actionable access errors."""
    path = Path(f"/dev/video{source}")
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "readable": os.access(path, os.R_OK),
        "writable": os.access(path, os.W_OK),
    }
    if not sys.platform.startswith("linux") or fcntl is None or not result["exists"]:
        return result

    try:
        descriptor = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError as error:
        result.update(
            probe_errno=error.errno,
            probe_error=error.strerror or str(error),
        )
        return result

    try:
        capability = bytearray(V4L2_CAPABILITY_SIZE)
        fcntl.ioctl(descriptor, VIDIOC_QUERYCAP, capability, True)
        capabilities = struct.unpack_from("=I", capability, 84)[0]
        device_caps = struct.unpack_from("=I", capability, 88)[0]
        effective_caps = device_caps if capabilities & V4L2_CAP_DEVICE_CAPS else capabilities
        result.update(
            driver=_decode_c_string(capability[0:16]),
            card=_decode_c_string(capability[16:48]),
            bus_info=_decode_c_string(capability[48:80]),
            capabilities=f"0x{effective_caps:08x}",
            capture_capable=bool(
                effective_caps & (V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_VIDEO_CAPTURE_MPLANE)
            ),
        )
    except OSError as error:
        result.update(
            probe_errno=error.errno,
            probe_error=error.strerror or str(error),
        )
    finally:
        os.close(descriptor)
    return result


def _failure_reason(node: dict[str, Any], opened_at_least_once: bool) -> str:
    probe_errno = node.get("probe_errno")
    if not node.get("exists", True):
        return "device_missing"
    if probe_errno in (errno.EACCES, errno.EPERM) or not node.get("readable", True):
        return "permission_denied"
    if probe_errno == errno.EBUSY:
        return "device_busy"
    if node.get("capture_capable") is False:
        return "not_video_capture_node"
    if opened_at_least_once:
        return "frame_timeout"
    return "v4l2_open_failed"


def discover_camera_sources(max_index: int = 15) -> list[int]:
    if sys.platform.startswith("linux"):
        detected: list[int] = []
        ignored: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = []
        for path in Path("/dev").glob("video*"):
            suffix = path.name.removeprefix("video")
            if suffix.isdigit() and int(suffix) <= max_index:
                source = int(suffix)
                node = inspect_video_node(source)
                nodes.append({"source": source, **node})
                if node.get("capture_capable") is False:
                    ignored.append({"source": source, "reason": "not_video_capture_node"})
                else:
                    # If QUERYCAP itself fails, keep the node as a candidate and
                    # let OpenCV try it; the diagnostic retains the exact errno.
                    detected.append(source)
        sources = sorted(set(detected))
    else:
        sources = list(range(max_index + 1))
        ignored = []
        nodes = []
    event(
        LOGGER,
        "camera_sources_discovered",
        max_index=max_index,
        sources=sources,
        ignored=ignored,
        nodes=nodes,
    )
    return sources


def _apply_preview_profile(capture: cv2.VideoCapture, profile: PreviewProfile) -> dict[str, bool]:
    accepted: dict[str, bool] = {}
    if profile.fourcc is not None:
        accepted["fourcc"] = bool(
            capture.set(
                cv2.CAP_PROP_FOURCC,
                float(cv2.VideoWriter_fourcc(*profile.fourcc)),
            )
        )
    accepted.update(
        width=bool(capture.set(cv2.CAP_PROP_FRAME_WIDTH, profile.width)),
        height=bool(capture.set(cv2.CAP_PROP_FRAME_HEIGHT, profile.height)),
        fps=bool(capture.set(cv2.CAP_PROP_FPS, profile.fps)),
        buffer_size=bool(capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)),
    )
    return accepted


def _open_preview(
    source: int,
    timeout_s: float,
) -> tuple[CameraPreview | None, CameraOpenFailure | None]:
    attempts: list[dict[str, Any]] = []
    opened_at_least_once = False
    for attempt_index, profile in enumerate(PREVIEW_PROFILES, start=1):
        capture = open_video_capture(source)
        opened = capture.isOpened()
        opened_at_least_once = opened_at_least_once or opened
        accepted = _apply_preview_profile(capture, profile) if opened else {}
        deadline = time.monotonic() + timeout_s
        frame = None
        while capture.isOpened() and time.monotonic() < deadline:
            ok, candidate = capture.read()
            if ok and candidate is not None and candidate.size:
                frame = candidate
                break
        effective = {
            "width": capture.get(cv2.CAP_PROP_FRAME_WIDTH),
            "height": capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
            "fps": capture.get(cv2.CAP_PROP_FPS),
            "fourcc": capture_fourcc(capture),
        }
        attempt = {
            "attempt": attempt_index,
            "profile": profile.name,
            "requested_fourcc": profile.fourcc,
            "requested_size": [profile.width, profile.height],
            "requested_fps": profile.fps,
            "opened": opened,
            "frame_received": frame is not None,
            "accepted": accepted,
            "effective": effective,
        }
        attempts.append(attempt)
        event(LOGGER, "camera_preview_attempt", source=source, **attempt)
        if frame is not None:
            event(
                LOGGER,
                "camera_preview_opened",
                source=source,
                frame_shape=frame.shape,
                backend=capture.getBackendName() if capture.isOpened() else None,
                profile=profile.name,
                accepted=accepted,
                **{f"effective_{key}": value for key, value in effective.items()},
            )
            return CameraPreview(source, capture, frame, profile.name), None
        capture.release()
        if attempt_index < len(PREVIEW_PROFILES):
            time.sleep(PREVIEW_RETRY_DELAY_S)

    node = inspect_video_node(source)
    reason = _failure_reason(node, opened_at_least_once)
    failure = CameraOpenFailure(source, reason, tuple(attempts), node)
    event(
        LOGGER,
        "camera_preview_open_failed",
        level=logging.WARNING,
        source=source,
        reason=reason,
        timeout_s=timeout_s,
        attempts=attempts,
        node=node,
    )
    return None, failure


def open_previews_with_failures(
    sources: list[int],
    timeout_s: float = PREVIEW_TIMEOUT_S,
) -> tuple[list[CameraPreview], list[CameraOpenFailure]]:
    previews: list[CameraPreview] = []
    failures: list[CameraOpenFailure] = []
    for source in sources:
        preview, failure = _open_preview(source, timeout_s)
        if preview is not None:
            previews.append(preview)
        if failure is not None:
            failures.append(failure)
    return previews, failures


def open_previews(sources: list[int], timeout_s: float = PREVIEW_TIMEOUT_S) -> list[CameraPreview]:
    previews, _ = open_previews_with_failures(sources, timeout_s)
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
        self.open_failures: list[CameraOpenFailure] = []
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
        self.previews, self.open_failures = open_previews_with_failures(sources)
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
            failures={failure.source: failure.reason for failure in self.open_failures},
        )
        if not self.previews:
            failed = ", ".join(
                f"{failure.source}:{_failure_message(failure.reason)}"
                for failure in self.open_failures
            )
            detail = f" ({failed})" if failed else ""
            self.message = f"Nessun flusso{detail}. Vedi log; R riprova, ESC esce."
        elif self.open_failures:
            failed = ", ".join(
                f"{failure.source}:{_failure_message(failure.reason)}"
                for failure in self.open_failures
            )
            self.message = f"Aperte {len(self.previews)}; fallite {failed}. Vedi log o premi R."

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
            label = f"source {preview.source} [{preview.profile}]"
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

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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.config import AppConfig, load_config, save_json
from ..core.setup import select_cameras, source_index, stable_camera_source
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


def resolve_camera_roster(base: AppConfig, camera_ids: list[str] | None) -> AppConfig:
    """Trim the logical slots to the cameras this deployment actually owns.

    An arena can run with two, three or four cameras: every node PC and the
    fusion server must agree on the same roster, otherwise the coordinator keeps
    waiting for observations from slots nobody publishes.

    This is the *deployment* roster, not the cameras plugged into this PC. To
    assign a few local webcams without evicting the ones another PC owns, pass
    ``--local-cameras`` instead, which keeps every slot in the configuration.
    """
    if not camera_ids:
        return base
    available = {camera.id: camera for camera in base.cameras}
    unknown = [camera_id for camera_id in camera_ids if camera_id not in available]
    if unknown:
        raise ValueError(f"camere sconosciute: {', '.join(unknown)}")
    if len(set(camera_ids)) != len(camera_ids):
        raise ValueError("le camere richieste devono essere diverse")
    return base.model_copy(
        update={"cameras": [available[camera_id] for camera_id in camera_ids]}
    )


def owned_indices(base: AppConfig, camera_ids: Sequence[str] | None) -> tuple[int, ...] | None:
    """Roster positions of the cameras attached to this PC. ``None`` means all."""
    if camera_ids is None:
        return None
    selected = {camera.id for camera in select_cameras(base, list(camera_ids))}
    return tuple(
        index for index, camera in enumerate(base.cameras) if camera.id in selected
    )


def build_camera_config(
    base: AppConfig,
    assignments: dict[int, int],
    owned: Sequence[int] | None = None,
) -> AppConfig:
    """Fold the operator's assignments back into the configuration.

    Without ``owned`` this is the whole-roster selection: every slot left
    unassigned is dropped. With ``owned`` — the distributed case — only those
    slots may change and every other camera keeps the source its own PC gave it,
    which is what stops a two-webcam client from erasing the other two cameras.
    """
    if owned is None:
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
        expected = set(owned)
        if set(assignments) != expected:
            missing = [base.cameras[index].id for index in sorted(expected - set(assignments))]
            if missing:
                raise ValueError(f"assign a source to: {', '.join(missing)}")
            raise ValueError("only the cameras attached to this PC can be assigned")
        if len(set(assignments.values())) != len(assignments):
            raise ValueError("the selected sources must be different")
        cameras = [
            camera.model_copy(update={"source": assignments[index]})
            if index in assignments
            else camera
            for index, camera in enumerate(base.cameras)
        ]
    return base.model_copy(update={"cameras": cameras, "revision": base.revision + 1})


def with_stable_sources(
    config: AppConfig, owned: Sequence[int] | None = None
) -> AppConfig:
    """Replace the just-assigned ``/dev/videoN`` indices with by-id aliases.

    Only the cameras this PC assigned are rewritten: the ones another PC owns
    would be resolved against *this* machine's devices, which is exactly the kind
    of confident wrong answer that sends a node to the wrong camera.
    """
    targets = set(range(len(config.cameras)) if owned is None else owned)
    cameras = [
        camera.model_copy(update={"source": stable_camera_source(camera.source)})
        if index in targets
        else camera
        for index, camera in enumerate(config.cameras)
    ]
    if cameras == config.cameras:
        return config
    event(
        LOGGER,
        "camera_sources_stabilised",
        sources={camera.id: camera.source for camera in cameras},
    )
    return config.model_copy(update={"cameras": cameras})


class CameraSelector:
    def __init__(
        self,
        base: AppConfig,
        sources: list[int],
        max_index: int = 15,
        owned: Sequence[int] | None = None,
    ) -> None:
        self.base = base
        self.requested_sources = sources
        self.max_index = max_index
        self.owned = None if owned is None else tuple(owned)
        self.previews: list[CameraPreview] = []
        self.open_failures: list[CameraOpenFailure] = []
        self.assignments: dict[int, int] = {}
        self.selected_index: int | None = None
        self.message = f"Clicca una camera e premi {self._assignable_keys()}"

    def _assignable(self) -> tuple[int, ...]:
        """Roster positions this run is allowed to touch."""
        if self.owned is None:
            return tuple(range(len(self.base.cameras)))
        return self.owned

    def _assignable_keys(self) -> str:
        return " ".join(str(index + 1) for index in self._assignable())

    def _owns(self, index: int) -> bool:
        return index in self._assignable()

    def scan(self) -> None:
        self.close()
        sources = self.requested_sources or discover_camera_sources(self.max_index)
        self.previews, self.open_failures = open_previews_with_failures(sources)
        available = {preview.source for preview in self.previews}
        # Pre-assign the sources that are both configured and actually answering,
        # so an operator who only wants to fix one camera keeps the rest as they are.
        # A source saved as a /dev/v4l/by-id alias has to be resolved back to the
        # index the preview grid is keyed by, or reopening the selector would show
        # every camera as unassigned.
        configured = {
            index: source_index(self.base.cameras[index].source)
            for index in self._assignable()
        }
        self.assignments = {
            index: source
            for index, source in configured.items()
            if source is not None and source in available
        }
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
            self.message = (
                f"Selezionata source {self.previews[index].source}: "
                f"premi {self._assignable_keys()}"
            )

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
            elif self._owns(index):
                assignment_items.append(f"{camera.id}=-")
            else:
                # Another PC owns this camera: shown so the operator can see the
                # full roster, but it is not theirs to reassign here.
                assignment_items.append(f"{camera.id}=source {camera.source} (altro PC)")
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
            f"Mouse: seleziona | {self._assignable_keys()}: assegna/rimuovi | "
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
                    if not self._owns(logical_index):
                        owned_ids = ", ".join(
                            self.base.cameras[index].id for index in self._assignable()
                        )
                        self.message = (
                            f"{self.base.cameras[logical_index].id} e' gestita da un altro "
                            f"PC; qui puoi assegnare solo: {owned_ids}"
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
                        result = build_camera_config(self.base, self.assignments, self.owned)
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
                                "local_camera_assignment_required"
                                if self.owned is not None
                                else "invalid_assignment"
                            ),
                        )
                        self.message = (
                            "Assegna almeno una camera prima di salvare"
                            if not self.assignments
                            else str(error)
                        )
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
    camera_ids: list[str] | None = None,
    local_camera_ids: list[str] | None = None,
    stable_sources: bool = True,
) -> AppConfig | None:
    if output.exists() and not force:
        raise FileExistsError(f"{output} exists; use --force to overwrite it")
    base = load_config(base_path) if base_path else AppConfig()
    base = resolve_camera_roster(base, camera_ids)
    event(
        LOGGER,
        "camera_roster_resolved",
        requested=camera_ids,
        cameras=[camera.id for camera in base.cameras],
    )
    # --camera is --local-cameras with one entry; keeping both spellings costs one
    # line here and keeps every existing invocation working.
    local = list(local_camera_ids) if local_camera_ids else None
    if camera_id is not None:
        local = [camera_id] if local is None else [*local, camera_id]
    owned = owned_indices(base, local)
    event(
        LOGGER,
        "camera_local_selection_resolved",
        local_cameras=local,
        owned=[base.cameras[index].id for index in owned] if owned is not None else None,
        remote_cameras=(
            [c.id for i, c in enumerate(base.cameras) if i not in owned]
            if owned is not None
            else []
        ),
    )
    selector = CameraSelector(base, sources or [], max_index, owned)
    result = selector.run()
    if result is not None:
        if stable_sources:
            result = with_stable_sources(result, owned)
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
    parser = argparse.ArgumentParser(description="Visually select cameras")
    parser.add_argument("--output", type=Path, default=Path("camera-config.json"))
    parser.add_argument("--base", type=Path, help="configurazione da preservare come base")
    parser.add_argument("--sources", type=int, nargs="+", help="indici da mostrare, es. 5 1 2 4")
    parser.add_argument("--max-index", type=int, default=15)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--cameras",
        nargs="+",
        metavar="CAM_ID",
        help="camere logiche presenti nel deployment, es. --cameras cam_0 cam_1; "
        "le altre vengono rimosse dalla configurazione",
    )
    parser.add_argument(
        "--camera",
        help="assegna una sola camera logica (modalita distribuita); "
        "le altre slot del roster restano invariate",
    )
    parser.add_argument(
        "--local-cameras",
        nargs="+",
        metavar="CAM_ID",
        help="camere collegate a QUESTO PC, es. --local-cameras cam_1 cam_2; "
        "le altre restano nel roster con la sorgente che hanno gia'",
    )
    parser.add_argument(
        "--keep-source-numbers",
        action="store_true",
        help="salva gli indici /dev/videoN invece degli alias stabili /dev/v4l/by-id",
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
            args.cameras,
            args.local_cameras,
            not args.keep_source_numbers,
        )
    except (FileExistsError, ValueError) as error:
        parser.error(str(error))
    if result is None:
        print("Selezione annullata: nessun file scritto")
    else:
        print(f"Configurazione scritta in {args.output}")
        print("Ordine:", [camera.source for camera in result.cameras])

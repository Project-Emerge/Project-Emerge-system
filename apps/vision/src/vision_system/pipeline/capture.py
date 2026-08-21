"""Multi-camera video capture, UVC controls, digital zoom, and worker threads."""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.config import CameraConfig
from ..transport.diagnostics import event

LOGGER = logging.getLogger(__name__)
PREFERRED_FOURCC = "MJPG"
MIN_DIGITAL_ZOOM = 1.0
DIGITAL_ZOOM_EPSILON = 1e-9
CAPTURE_BUFFER_SIZE = 1.0
AUTOFOCUS_ENABLED = 1.0
AUTOFOCUS_DISABLED = 0.0
AUTO_EXPOSURE_ENABLED = 0.75
AUTO_EXPOSURE_DISABLED = 0.25
WORKER_JOIN_TIMEOUT_S = 3.0
RECONNECT_DELAY_INITIAL_S = 0.25
RECONNECT_DELAY_MAX_S = 5.0
PROBE_DURATION_S = 5.0
PROBE_SIGNATURE_SIZE = (32, 18)
MIN_ELAPSED_S = 1e-6


def open_video_capture(source: int | str):
    if isinstance(source, int) and sys.platform.startswith("linux"):
        return cv2.VideoCapture(source, cv2.CAP_V4L2)
    return cv2.VideoCapture(source)


def apply_digital_zoom(image: NDArray[np.uint8], factor: float) -> NDArray[np.uint8]:
    """Center-crop and resize a frame, preserving its configured dimensions."""
    if factor <= MIN_DIGITAL_ZOOM + DIGITAL_ZOOM_EPSILON:
        return image
    height, width = image.shape[:2]
    crop_width = max(1, int(round(width / factor)))
    crop_height = max(1, int(round(height / factor)))
    left = (width - crop_width) // 2
    top = (height - crop_height) // 2
    cropped = image[top : top + crop_height, left : left + crop_width]
    return cv2.resize(cropped, (width, height), interpolation=cv2.INTER_LINEAR)


def apply_camera_properties(capture, config: CameraConfig) -> dict[str, bool]:
    """Apply every persisted camera setting and report backend acceptance.

    OpenCV camera backends are allowed to reject unsupported UVC controls.  The
    returned map lets interactive tools surface that fact to the operator.
    """
    # Four 1080p uncompressed streams can saturate a USB controller and make
    # V4L2 deliver only a few frames per second. Request MJPEG before the
    # dimensions/FPS; unsupported devices simply keep their native format.
    properties: list[tuple[str, int, float]] = [
        (
            "mjpeg",
            cv2.CAP_PROP_FOURCC,
            float(cv2.VideoWriter_fourcc(*PREFERRED_FOURCC)),
        ),
        ("width", cv2.CAP_PROP_FRAME_WIDTH, float(config.width)),
        ("height", cv2.CAP_PROP_FRAME_HEIGHT, float(config.height)),
        ("fps", cv2.CAP_PROP_FPS, float(config.fps)),
        ("buffer_size", cv2.CAP_PROP_BUFFERSIZE, CAPTURE_BUFFER_SIZE),
        (
            "autofocus",
            cv2.CAP_PROP_AUTOFOCUS,
            AUTOFOCUS_ENABLED if config.autofocus else AUTOFOCUS_DISABLED,
        ),
        (
            "auto_exposure",
            cv2.CAP_PROP_AUTO_EXPOSURE,
            AUTO_EXPOSURE_ENABLED if config.auto_exposure else AUTO_EXPOSURE_DISABLED,
        ),
    ]
    if config.focus is not None:
        properties.append(("focus", cv2.CAP_PROP_FOCUS, config.focus))
    if config.exposure is not None:
        properties.append(("exposure", cv2.CAP_PROP_EXPOSURE, config.exposure))
    if config.zoom is not None:
        properties.append(("zoom", cv2.CAP_PROP_ZOOM, config.zoom))
    return {name: bool(capture.set(property_id, value)) for name, property_id, value in properties}


def capture_fourcc(capture) -> str | None:
    """Return the active capture pixel format when the backend exposes it."""
    value = int(round(capture.get(cv2.CAP_PROP_FOURCC)))
    if value <= 0:
        return None
    chars = "".join(chr((value >> (8 * index)) & 0xFF) for index in range(4))
    return chars if all(32 <= ord(char) < 127 for char in chars) else f"0x{value:08x}"


def probe_camera(
    camera: CameraConfig,
    duration_s: float = PROBE_DURATION_S,
) -> dict:
    """Measure effective video settings, throughput and duplicate frames."""
    capture = open_video_capture(camera.source)
    accepted = apply_camera_properties(capture, camera)
    started = time.monotonic()
    frames = 0
    duplicates = 0
    previous_signature: bytes | None = None
    frame = None
    while capture.isOpened() and time.monotonic() - started < duration_s:
        ok, current = capture.read()
        if not ok:
            continue
        current = apply_digital_zoom(current, camera.digital_zoom)
        frame = current
        frames += 1
        gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
        signature = cv2.resize(
            gray,
            PROBE_SIGNATURE_SIZE,
            interpolation=cv2.INTER_AREA,
        ).tobytes()
        duplicates += int(signature == previous_signature)
        previous_signature = signature
    capture.release()
    elapsed = max(time.monotonic() - started, MIN_ELAPSED_S)
    shape = tuple(frame.shape) if frame is not None else None
    expected_shape = (camera.height, camera.width)
    return {
        "camera": camera.id,
        "source": camera.source,
        "ok": frame is not None,
        "shape": shape,
        "resolution_ok": shape is not None and shape[:2] == expected_shape,
        "effective_fps": round(frames / elapsed, 2),
        "effective_fourcc": capture_fourcc(capture),
        "duplicate_frames": duplicates,
        "zoom": camera.zoom,
        "zoom_control_accepted": accepted.get("zoom"),
        "digital_zoom": camera.digital_zoom,
    }


@dataclass(frozen=True)
class Frame:
    camera_id: str
    sequence: int
    monotonic_ns: int
    utc_ns: int
    image: NDArray[np.uint8]


class CameraWorker:
    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self._frame: Frame | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.online = False
        self.error: str | None = None
        self.frames_received = 0
        self.reconnects = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"capture-{self.config.id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=WORKER_JOIN_TIMEOUT_S)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._frame

    def _open(self):
        capture = open_video_capture(self.config.source)
        accepted = apply_camera_properties(capture, self.config)
        rejected = [name for name, ok in accepted.items() if not ok]
        if rejected:
            LOGGER.warning("camera %s rejected controls: %s", self.config.id, rejected)
        event(
            LOGGER,
            "runtime_camera_open_attempt",
            level=logging.INFO if capture.isOpened() else logging.WARNING,
            camera_id=self.config.id,
            source=self.config.source,
            opened=capture.isOpened(),
            requested_size=[self.config.width, self.config.height],
            effective_size=[
                capture.get(cv2.CAP_PROP_FRAME_WIDTH),
                capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
            ],
            requested_fps=self.config.fps,
            effective_fps=capture.get(cv2.CAP_PROP_FPS),
            effective_fourcc=capture_fourcc(capture),
            accepted_controls=accepted,
            digital_zoom=self.config.digital_zoom,
        )
        return capture

    def _run(self) -> None:
        sequence = 0
        delay = RECONNECT_DELAY_INITIAL_S
        while not self._stop.is_set():
            capture = self._open()
            if not capture.isOpened():
                self.online = False
                self.error = f"cannot open source {self.config.source}"
                capture.release()
                self._stop.wait(delay)
                delay = min(delay * 2, RECONNECT_DELAY_MAX_S)
                self.reconnects += 1
                continue
            delay = RECONNECT_DELAY_INITIAL_S
            self.online = True
            self.error = None
            while not self._stop.is_set():
                ok, image = capture.read()
                monotonic_ns = time.monotonic_ns()
                utc_ns = time.time_ns()
                if not ok:
                    self.error = "frame read failed"
                    break
                image = apply_digital_zoom(image, self.config.digital_zoom)
                frame = Frame(self.config.id, sequence, monotonic_ns, utc_ns, image)
                with self._lock:
                    self._frame = frame
                sequence += 1
                self.frames_received += 1
            capture.release()
            self.online = False
            if not self._stop.is_set():
                LOGGER.warning("camera %s disconnected; retrying", self.config.id)
                self.reconnects += 1


class CaptureManager:
    def __init__(self, cameras: list[CameraConfig]) -> None:
        self.workers = {camera.id: CameraWorker(camera) for camera in cameras}

    def start(self) -> None:
        for worker in self.workers.values():
            worker.start()

    def stop(self) -> None:
        for worker in self.workers.values():
            worker.stop()

    def latest(self) -> dict[str, Frame]:
        return {
            camera_id: frame
            for camera_id, worker in self.workers.items()
            if (frame := worker.latest()) is not None
        }

    def status(self) -> dict[str, dict]:
        return {
            camera_id: {
                "online": worker.online,
                "error": worker.error,
                "frames_received": worker.frames_received,
                "reconnects": worker.reconnects,
            }
            for camera_id, worker in self.workers.items()
        }

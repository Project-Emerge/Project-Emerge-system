"""Live annotated viewer for inspecting ArUco detections and camera telemetry."""

from __future__ import annotations

import argparse
import logging
import signal
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from ..calibration.store import load_calibrations
from ..core.aruco import create_marker_detector
from ..core.config import initial_config
from ..core.geometry import marker_object_points
from ..pipeline.capture import apply_camera_properties, apply_digital_zoom, open_video_capture
from ..transport.diagnostics import configure_diagnostics, event

LOGGER = logging.getLogger(__name__)

COLORS = [
    (0, 255, 0),
    (255, 120, 0),
    (255, 0, 200),
    (0, 220, 255),
    (120, 0, 255),
    (0, 255, 200),
]


class TagStats:
    def __init__(self) -> None:
        self.frames = 0
        self.min_side_px: float | None = None
        self.max_side_px: float | None = None
        self.min_distance_m: float | None = None
        self.max_distance_m: float | None = None
        self.min_viewing_angle_deg: float | None = None
        self.max_viewing_angle_deg: float | None = None
        self.first_seen: datetime | None = None
        self.last_seen: datetime | None = None

    def update(self, side_px: float, distance_m: float | None, angle_deg: float | None) -> None:
        self.frames += 1
        self.min_side_px = (
            min(self.min_side_px, side_px) if self.min_side_px is not None else side_px
        )
        self.max_side_px = (
            max(self.max_side_px, side_px) if self.max_side_px is not None else side_px
        )
        if distance_m is not None:
            self.min_distance_m = (
                min(self.min_distance_m, distance_m)
                if self.min_distance_m is not None
                else distance_m
            )
            self.max_distance_m = (
                max(self.max_distance_m, distance_m)
                if self.max_distance_m is not None
                else distance_m
            )
        if angle_deg is not None:
            self.min_viewing_angle_deg = (
                min(self.min_viewing_angle_deg, angle_deg)
                if self.min_viewing_angle_deg is not None
                else angle_deg
            )
            self.max_viewing_angle_deg = (
                max(self.max_viewing_angle_deg, angle_deg)
                if self.max_viewing_angle_deg is not None
                else angle_deg
            )
        now = datetime.now(UTC)
        self.first_seen = self.first_seen or now
        self.last_seen = now


def viewer_main() -> None:
    parser = argparse.ArgumentParser(description="Visore ArUco live per singola camera")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cache", type=Path, default=Path(".state/last_good_config.json"))
    parser.add_argument("--calibrations", type=Path, default=Path("calibrations"))
    parser.add_argument("--camera", default="cam_0", help="cam_0..cam_3")
    parser.add_argument(
        "--snapshots", type=Path, default=Path("photo/aruco"), help="cartella screenshot"
    )
    args = parser.parse_args()
    configure_diagnostics("vision-aruco-view", verbose=args.verbose)

    config = initial_config(args.config, args.cache)
    camera = next((item for item in config.cameras if item.id == args.camera), None)
    if camera is None:
        raise SystemExit(f"camera sconosciuta: {args.camera}")
    event(
        LOGGER,
        "viewer_started",
        camera_id=camera.id,
        source=camera.source,
        dictionary=config.aruco.dictionary,
    )

    detector = create_marker_detector(config.aruco)

    calibrations = load_calibrations(args.calibrations)
    calibration = calibrations.get(camera.id)
    camera_matrix = (
        np.asarray(calibration.camera_matrix, dtype=np.float64) if calibration else None
    )
    distortion = np.asarray(calibration.distortion, dtype=np.float64) if calibration else None

    marker_sizes = {marker.id: marker.size_m for marker in config.aruco.mobile_markers}
    marker_sizes.update({marker.id: marker.size_m for marker in config.aruco.reference_markers})
    default_size = config.aruco.auto_mobile_markers.default_size_m

    capture = open_video_capture(camera.source)
    accepted = apply_camera_properties(capture, camera)
    if not capture.isOpened():
        raise SystemExit(f"impossibile aprire {camera.id} (source {camera.source})")
    if rejected := [name for name, ok in accepted.items() if not ok]:
        LOGGER.warning("controlli rifiutati: %s", rejected)

    stats: dict[int, TagStats] = defaultdict(TagStats)
    frame_times: list[float] = []
    stopped = False
    paused = False
    frames = 0
    snapshots = args.snapshots
    snapshots.mkdir(parents=True, exist_ok=True)

    def stop(*_: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    started = time.monotonic()
    while not stopped:
        if not paused:
            ok, image = capture.read()
            if not ok:
                LOGGER.warning("lettura frame fallita")
                break
            image = apply_digital_zoom(image, camera.digital_zoom)
            frames += 1
        corners, ids, _ = detector.detectMarkers(image)
        display = image.copy()
        visible: list[int] = []
        now = datetime.now(UTC)
        if ids is not None:
            for marker_corners, marker_id_raw in zip(corners, ids.reshape(-1), strict=True):
                marker_id = int(marker_id_raw)
                visible.append(marker_id)
                points = np.asarray(marker_corners, dtype=np.float64).reshape(4, 2)
                sides = [
                    np.linalg.norm(points[(index + 1) % 4] - points[index]) for index in range(4)
                ]
                side_px = float(np.mean(sides))
                color = COLORS[marker_id % len(COLORS)]
                cv2.polylines(
                    display, [points.astype(np.int32)], True, color, max(2, int(side_px * 0.02))
                )
                corner = tuple(points[0].astype(int))
                cv2.putText(
                    display,
                    f"ID {marker_id}",
                    (corner[0], corner[1] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    color,
                    2,
                )
                label = f"{marker_id}px"
                distance_m = None
                angle_deg = None
                size = marker_sizes.get(marker_id)
                if size is None and config.aruco.auto_mobile_markers.enabled:
                    size = default_size
                if size is not None and camera_matrix is not None and distortion is not None:
                    result = cv2.solvePnPGeneric(
                        marker_object_points(size),
                        points,
                        camera_matrix,
                        distortion,
                        flags=cv2.SOLVEPNP_IPPE_SQUARE,
                    )
                    rvecs = result[1]
                    tvecs = result[2]
                    if rvecs and tvecs:
                        best = 0
                        for index, tvec in enumerate(tvecs):
                            if float(np.asarray(tvec).reshape(3)[2]) > 0:
                                best = index
                                break
                        rvec, tvec = rvecs[best], tvecs[best]
                        cv2.drawFrameAxes(display, camera_matrix, distortion, rvec, tvec, size)
                        translation = np.asarray(tvec).reshape(3)
                        distance_m = float(np.linalg.norm(translation))
                        rotation, _ = cv2.Rodrigues(rvec)
                        normal = rotation[:, 2]
                        angle_deg = float(
                            np.degrees(np.arccos(np.clip(normal[2], -1.0, 1.0)))
                        )
                        label = f"{side_px:.0f}px {distance_m:.2f}m {angle_deg:.0f}deg"
                cv2.putText(
                    display,
                    label,
                    (corner[0], corner[1] + 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    color,
                    2,
                )
                if not paused:
                    stats[marker_id].update(side_px, distance_m, angle_deg)
        frame_times.append(time.monotonic())
        frame_times = [stamp for stamp in frame_times if stamp > time.monotonic() - 3.0]
        fps = len(frame_times) / 3.0 if len(frame_times) > 1 else 0.0
        bar = np.zeros((44, display.shape[1], 3), dtype=np.uint8)
        cv2.putText(
            bar,
            f"{camera.id}  FPS {fps:.1f}  tag {len(visible)}: {visible}"
            + ("  PAUSA" if paused else ""),
            (12, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )
        display = np.vstack((display, bar))
        cv2.imshow(f"ArucoView - {camera.id}", display)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            stopped = True
        elif key in (ord("p"), ord(" ")):
            paused = not paused
        elif key == ord("s"):
            path = snapshots / f"{camera.id}-{now.strftime('%Y%m%dT%H%M%SZ')}.jpg"
            cv2.imwrite(str(path), image)
            print(f"Snapshot: {path}")

    capture.release()
    cv2.destroyAllWindows()
    elapsed = max(time.monotonic() - started, 1e-9)
    print(f"\n{args.camera}: {frames} frame in {elapsed:.0f}s ({frames / elapsed:.1f} FPS)")
    if not stats:
        print("Nessun tag rilevato")
    else:
        for marker_id in sorted(stats):
            entry = stats[marker_id]
            details = [f"{entry.frames} frame", f"{entry.min_side_px:.0f}px min"]
            details.append(f"{entry.max_side_px:.0f}px max")
            if entry.min_distance_m is not None:
                details.append(f"{entry.min_distance_m:.2f}-{entry.max_distance_m:.2f}m")
            if entry.min_viewing_angle_deg is not None:
                details.append(
                    f"{entry.min_viewing_angle_deg:.0f}-{entry.max_viewing_angle_deg:.0f}deg"
                )
            print(f"ID {marker_id}: {', '.join(details)}")
    event(
        LOGGER,
        "viewer_finished",
        camera_id=camera.id,
        frames=frames,
        elapsed_s=elapsed,
        tags={marker_id: entry.frames for marker_id, entry in stats.items()},
    )

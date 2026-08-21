"""Interactive GUI for configuring camera field-of-view and digital zoom."""

from __future__ import annotations

import argparse
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.config import AppConfig, CameraConfig, load_config, save_json
from ..pipeline.capture import apply_camera_properties, apply_digital_zoom, open_video_capture
from ..transport.diagnostics import configure_diagnostics, event
from ..transport.mqtt import publish_config_update

WINDOW_NAME = "VisionSystem - configurazione campo visivo"
HEADER_HEIGHT = 145
TILE_WIDTH = 560
TILE_HEIGHT = 315
GRID_COLUMNS = 2
DIGITAL_ZOOM_STEP = 0.05
NORMAL_PRESET = 1.75
LOGGER = logging.getLogger(__name__)


@dataclass
class CameraPanel:
    config: CameraConfig
    capture: cv2.VideoCapture
    frame: NDArray[np.uint8]
    digital_zoom: float
    online: bool = True

    def close(self) -> None:
        self.capture.release()

    def set_digital_zoom(self, target: float) -> None:
        self.digital_zoom = round(min(8.0, max(1.0, target)), 2)

    def display_frame(self) -> NDArray[np.uint8]:
        return apply_digital_zoom(self.frame, self.digital_zoom)


def _placeholder(camera: CameraConfig, text: str) -> NDArray[np.uint8]:
    frame = np.full((TILE_HEIGHT, TILE_WIDTH, 3), 28, dtype=np.uint8)
    cv2.putText(
        frame,
        camera.id,
        (25, 55),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        2,
    )
    cv2.putText(frame, text, (25, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (80, 180, 255), 2)
    return frame


def open_camera_panels(config: AppConfig, timeout_s: float = 2.0) -> list[CameraPanel]:
    panels: list[CameraPanel] = []
    for camera in config.cameras:
        capture = open_video_capture(camera.source)
        # Apply native UVC properties only. The digital crop is previewed below,
        # so changing it remains instantaneous and independent of the hardware.
        accepted = apply_camera_properties(capture, camera)
        deadline = time.monotonic() + timeout_s
        frame = None
        while capture.isOpened() and time.monotonic() < deadline:
            ok, candidate = capture.read()
            if ok:
                frame = candidate
                break
        display_frame = frame if frame is not None else _placeholder(camera, "camera offline")
        panels.append(
            CameraPanel(
                config=camera,
                capture=capture,
                frame=display_frame,
                digital_zoom=camera.digital_zoom,
                online=frame is not None,
            )
        )
        event(
            LOGGER,
            "camera_configuration_preview_opened",
            level=logging.INFO if frame is not None else logging.WARNING,
            camera_id=camera.id,
            source=camera.source,
            online=frame is not None,
            requested_size=[camera.width, camera.height],
            frame_shape=frame.shape if frame is not None else None,
            requested_fps=camera.fps,
            effective_fps=capture.get(cv2.CAP_PROP_FPS),
            accepted_controls=accepted,
            digital_zoom=camera.digital_zoom,
        )
    return panels


def build_camera_settings_config(base: AppConfig, digital_zooms: dict[str, float]) -> AppConfig:
    expected = {camera.id for camera in base.cameras}
    if set(digital_zooms) != expected:
        raise ValueError("digital zoom settings must be supplied for all four cameras")
    cameras = [
        camera.model_copy(update={"digital_zoom": digital_zooms[camera.id]})
        for camera in base.cameras
    ]
    return base.model_copy(update={"cameras": cameras, "revision": base.revision + 1})


class CameraConfigurator:
    def __init__(self, base: AppConfig) -> None:
        self.base = base
        self.panels: list[CameraPanel] = []
        self.selected_index = 0
        self.message = "N imposta un FOV normale; W ripristina tutto il grandangolo"

    def close(self) -> None:
        for panel in self.panels:
            panel.close()
        self.panels.clear()

    def _mouse(self, event: int, x: int, y: int, flags: int, userdata) -> None:
        if event != cv2.EVENT_LBUTTONDOWN or y < HEADER_HEIGHT:
            return
        column = x // TILE_WIDTH
        row = (y - HEADER_HEIGHT) // TILE_HEIGHT
        index = row * GRID_COLUMNS + column
        if 0 <= index < len(self.panels):
            self.selected_index = index
            self.message = f"Selezionata {self.panels[index].config.id}"

    def _refresh(self) -> None:
        for panel in self.panels:
            ok, frame = panel.capture.read()
            panel.online = ok
            if ok:
                panel.frame = frame

    def _change_selected(self, delta: float) -> None:
        panel = self.panels[self.selected_index]
        old_zoom = panel.digital_zoom
        panel.set_digital_zoom(panel.digital_zoom + delta)
        event(
            LOGGER,
            "digital_zoom_changed",
            camera_id=panel.config.id,
            old_zoom=old_zoom,
            new_zoom=panel.digital_zoom,
        )
        self.message = f"{panel.config.id}: zoom digitale {panel.digital_zoom:.2f}x"

    def _apply_selected_to_all(self) -> None:
        selected_zoom = self.panels[self.selected_index].digital_zoom
        for panel in self.panels:
            panel.set_digital_zoom(selected_zoom)
        event(
            LOGGER,
            "digital_zoom_applied_to_all",
            source_camera_id=self.panels[self.selected_index].config.id,
            digital_zoom=selected_zoom,
        )
        self.message = f"Zoom digitale {selected_zoom:.2f}x applicato a tutte le camere"

    def _render(self) -> NDArray[np.uint8]:
        rows = math.ceil(len(self.panels) / GRID_COLUMNS)
        image = np.full(
            (HEADER_HEIGHT + rows * TILE_HEIGHT, GRID_COLUMNS * TILE_WIDTH, 3),
            25,
            dtype=np.uint8,
        )
        lines = [
            "Campo visivo software - funziona anche con ottiche fisse ELP",
            self.message,
            "Mouse: seleziona | +/-: regola | N: normale 1.75x | W: grandangolo 1.00x",
            "A: applica il valore selezionato a tutte | ENTER: salva | ESC: annulla",
        ]
        for index, line in enumerate(lines):
            cv2.putText(
                image,
                line,
                (18, 29 + index * 31),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58 if index else 0.7,
                (80, 220, 255) if index == 1 else (235, 235, 235),
                2 if index < 2 else 1,
            )
        for index, panel in enumerate(self.panels):
            row, column = divmod(index, GRID_COLUMNS)
            x = column * TILE_WIDTH
            y = HEADER_HEIGHT + row * TILE_HEIGHT
            tile = cv2.resize(panel.display_frame(), (TILE_WIDTH, TILE_HEIGHT))
            if not panel.online:
                tile = (tile * 0.4).astype(np.uint8)
            image[y : y + TILE_HEIGHT, x : x + TILE_WIDTH] = tile
            color = (0, 220, 255) if index == self.selected_index else (100, 100, 100)
            cv2.rectangle(
                image,
                (x + 2, y + 2),
                (x + TILE_WIDTH - 3, y + TILE_HEIGHT - 3),
                color,
                4,
            )
            label = (
                f"{panel.config.id} | source {panel.config.source} | "
                f"zoom digitale {panel.digital_zoom:.2f}x"
            )
            cv2.rectangle(image, (x + 7, y + 7), (x + TILE_WIDTH - 7, y + 48), (0, 0, 0), -1)
            cv2.putText(
                image,
                label,
                (x + 16, y + 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (255, 255, 255),
                2,
            )
        return image

    def run(self) -> AppConfig | None:
        self.panels = open_camera_panels(self.base)
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(WINDOW_NAME, self._mouse)
        try:
            while True:
                self._refresh()
                cv2.imshow(WINDOW_NAME, self._render())
                key = cv2.waitKey(30) & 0xFF
                if key in (27, ord("q")):
                    event(LOGGER, "camera_configuration_cancelled")
                    return None
                if key in (ord("+"), ord("=")):
                    self._change_selected(DIGITAL_ZOOM_STEP)
                elif key in (ord("-"), ord("_")):
                    self._change_selected(-DIGITAL_ZOOM_STEP)
                elif key == ord("n"):
                    panel = self.panels[self.selected_index]
                    old_zoom = panel.digital_zoom
                    panel.set_digital_zoom(NORMAL_PRESET)
                    event(
                        LOGGER,
                        "digital_zoom_preset",
                        camera_id=panel.config.id,
                        preset="normal",
                        old_zoom=old_zoom,
                        new_zoom=panel.digital_zoom,
                    )
                    self.message = f"{panel.config.id}: preset normale {NORMAL_PRESET:.2f}x"
                elif key == ord("w"):
                    panel = self.panels[self.selected_index]
                    old_zoom = panel.digital_zoom
                    panel.set_digital_zoom(1.0)
                    event(
                        LOGGER,
                        "digital_zoom_preset",
                        camera_id=panel.config.id,
                        preset="wide",
                        old_zoom=old_zoom,
                        new_zoom=panel.digital_zoom,
                    )
                    self.message = f"{panel.config.id}: grandangolo completo 1.00x"
                elif key == ord("a"):
                    self._apply_selected_to_all()
                elif key in (10, 13):
                    result = build_camera_settings_config(
                        self.base,
                        {panel.config.id: panel.digital_zoom for panel in self.panels},
                    )
                    event(
                        LOGGER,
                        "camera_configuration_confirmed",
                        old_revision=self.base.revision,
                        new_revision=result.revision,
                        old_digital_zooms={
                            camera.id: camera.digital_zoom for camera in self.base.cameras
                        },
                        new_digital_zooms={
                            camera.id: camera.digital_zoom for camera in result.cameras
                        },
                    )
                    return result
        finally:
            self.close()
            cv2.destroyWindow(WINDOW_NAME)


def configure_cameras(
    config_path: Path,
    output: Path | None = None,
    force: bool = False,
) -> tuple[AppConfig | None, Path]:
    destination = output or config_path
    if destination != config_path and destination.exists() and not force:
        raise FileExistsError(f"{destination} exists; use --force to overwrite it")
    base = load_config(config_path)
    result = CameraConfigurator(base).run()
    if result is not None:
        save_json(destination, result)
        event(
            LOGGER,
            "camera_settings_written",
            destination=destination,
            revision=result.revision,
            digital_zooms={camera.id: camera.digital_zoom for camera in result.cameras},
        )
    return result, destination


def camera_configurator_main() -> None:
    parser = argparse.ArgumentParser(
        description="Configura visualmente il campo visivo software delle camere"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="default: sovrascrive --config")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--publish-mqtt",
        action="store_true",
        help="pubblica anche la configurazione completa su config/set",
    )
    parser.add_argument("--mqtt-timeout", type=float, default=5.0)
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics("vision-configure-cameras")
    print(f"Log diagnostico: {diagnostic_path}")
    try:
        result, destination = configure_cameras(args.config, args.output, args.force)
    except (FileExistsError, OSError, ValueError) as error:
        parser.error(str(error))
    if result is None:
        print("Configurazione annullata: nessun file scritto")
        return
    print(f"Configurazione revisione {result.revision} scritta in {destination}")
    print("Zoom digitale:", {camera.id: camera.digital_zoom for camera in result.cameras})
    print("ATTENZIONE: dopo un cambio di FOV rifare calibrazione intrinseca ed estrinseca.")
    if args.publish_mqtt:
        try:
            response = publish_config_update(result, timeout=args.mqtt_timeout)
        except (OSError, ConnectionError, TimeoutError) as error:
            parser.error(f"config salvata, pubblicazione MQTT fallita: {error}")
        if response is None:
            print("Configurazione pubblicata (nessun localizzatore ha risposto entro il timeout)")
        elif response.get("accepted"):
            print(f"Configurazione MQTT accettata, revisione {response['revision']}")
        else:
            parser.error(f"config salvata ma rifiutata via MQTT: {response.get('error')}")


if __name__ == "__main__":
    camera_configurator_main()

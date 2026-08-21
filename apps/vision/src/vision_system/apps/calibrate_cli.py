"""CLI suite for board generation, camera probing, wizards, and folder calibration."""

from __future__ import annotations

import argparse
import json
import logging
import signal
from pathlib import Path

from ..calibration.board import BoardSpec, generate_board
from ..calibration.folder import calibrate_folder_tree, print_results
from ..calibration.store import load_calibrations
from ..calibration.wizards import run_extrinsic_wizard, run_intrinsic_wizard
from ..core.config import (
    AppConfig,
    ArucoConfig,
    CameraConfig,
    initial_config,
    load_reference_markers,
)
from ..pipeline.calibration_store import calibration_compatibility_error
from ..pipeline.capture import probe_camera
from ..transport.diagnostics import configure_diagnostics, event
from ..transport.mqtt import MqttBridge
from .camera_selector import select_camera_config
from .localizer import VisionRuntime

LOGGER = logging.getLogger(__name__)


def _initial_config(config_path: Path | None, cache_path: Path) -> AppConfig:
    return initial_config(config_path, cache_path)


def _configuration_with_mqtt(
    config_path: Path | None,
    cache_path: Path,
    calibration_dir: Path,
    wait_seconds: float,
    mqtt_enabled: bool = True,
) -> tuple[AppConfig, MqttBridge | None]:
    initial = _initial_config(config_path, cache_path)
    if not mqtt_enabled or config_path or wait_seconds <= 0:
        return initial, None
    bridge = MqttBridge(initial, cache_path, calibration_dir)
    bridge.start()
    return bridge.wait_for_config(wait_seconds), bridge


def _selected_cameras(config: AppConfig, name: str) -> list[CameraConfig]:
    if name == "all":
        return config.cameras
    selected = [camera for camera in config.cameras if camera.id == name]
    if not selected:
        raise SystemExit(f"camera sconosciuta: {name}")
    return selected


def _handle_board_command(args: argparse.Namespace) -> None:
    formats = ("a4", "a3") if args.format == "both" else (args.format,)
    for page_format in formats:
        pdf, png, metadata = generate_board(args.output, BoardSpec.for_format(page_format))
        print(f"{page_format.upper()} PDF: {pdf}\nPNG: {png}\nMetadati: {metadata}")


def _handle_select_cameras_command(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    try:
        selected = select_camera_config(
            output=args.output,
            base_path=args.base or args.config,
            sources=args.sources,
            max_index=args.max_index,
            force=args.force,
        )
    except FileExistsError as error:
        parser.error(str(error))
    if selected is None:
        print("Selezione annullata: nessun file scritto")
    else:
        print(f"Configurazione scritta in {args.output}")
        print("Ordine:", [camera.source for camera in selected.cameras])


def _handle_from_folder_command(
    args: argparse.Namespace,
    config: AppConfig,
    bridge: MqttBridge | None,
    parser: argparse.ArgumentParser,
) -> None:
    try:
        results = calibrate_folder_tree(
            config,
            args.input,
            args.output or args.calibrations,
            args.board_format,
            args.camera,
            args.allow_low_quality,
            args.sharpness_threshold,
        )
    except (ValueError, NotADirectoryError) as error:
        parser.error(str(error))
    print_results(results)
    if bridge:
        for result in results:
            if result.calibration_path and result.calibration:
                bridge.publish_calibration(result.calibration)
    if not all(result.written for result in results):
        raise SystemExit(2)


def _handle_probe_command(config: AppConfig) -> None:
    for camera in config.cameras:
        probe_result = probe_camera(camera)
        event(LOGGER, "camera_probe_result", **probe_result)
        print(json.dumps(probe_result))


def _handle_intrinsics_command(
    cameras: list[CameraConfig],
    args: argparse.Namespace,
    bridge: MqttBridge | None,
) -> None:
    for camera in cameras:
        if bridge:
            bridge.publish_calibration_session(
                {"stage": "intrinsics", "camera_id": camera.id, "status": "started"}
            )
        calibration = run_intrinsic_wizard(
            camera,
            args.calibrations,
            spec=BoardSpec.for_format(args.board_format),
        )
        if calibration is None:
            raise SystemExit(f"calibrazione intrinseca annullata: {camera.id}")
        if bridge:
            bridge.publish_calibration(calibration)


def _handle_extrinsics_command(
    cameras: list[CameraConfig],
    config: AppConfig,
    args: argparse.Namespace,
    bridge: MqttBridge | None,
) -> None:
    calibrations = load_calibrations(args.calibrations)
    for camera in cameras:
        calibration = calibrations.get(camera.id)
        if calibration is None:
            event(
                LOGGER,
                "extrinsic_blocked_missing_intrinsics",
                level=logging.ERROR,
                camera_id=camera.id,
                calibration_dir=args.calibrations,
            )
            raise SystemExit(f"manca la calibrazione intrinseca di {camera.id}")
        compatibility_error = calibration_compatibility_error(camera, calibration)
        if compatibility_error is not None:
            event(
                LOGGER,
                "extrinsic_blocked_incompatible_intrinsics",
                level=logging.ERROR,
                camera_id=camera.id,
                reason=compatibility_error,
                configured_size=[camera.width, camera.height],
                calibrated_size=calibration.image_size,
                configured_zoom=camera.zoom,
                calibrated_zoom=calibration.camera_zoom,
                configured_digital_zoom=camera.digital_zoom,
                calibrated_digital_zoom=calibration.camera_digital_zoom,
            )
            raise SystemExit(
                f"intrinseche incompatibili per {camera.id}: {compatibility_error}; "
                "ripristina le impostazioni camera usate nella calibrazione oppure "
                "rifai le intrinseche"
            )
        if camera.source != calibration.source:
            LOGGER.warning(
                "%s usa source %s ma le intrinseche riportano source %s; "
                "verifica che sia la stessa camera fisica",
                camera.id,
                camera.source,
                calibration.source,
            )
        event(
            LOGGER,
            "extrinsic_camera_started",
            camera_id=camera.id,
            source=camera.source,
            calibrated_source=calibration.source,
            source_matches_calibration=camera.source == calibration.source,
            image_size=calibration.image_size,
            camera_zoom=camera.zoom,
            calibrated_zoom=calibration.camera_zoom,
            camera_digital_zoom=camera.digital_zoom,
            calibrated_digital_zoom=calibration.camera_digital_zoom,
            reference_ids=sorted(config.references_by_id()),
            allow_low_quality=args.allow_low_quality,
        )
        if bridge:
            bridge.publish_calibration_session(
                {"stage": "extrinsics", "camera_id": camera.id, "status": "started"}
            )
        updated = run_extrinsic_wizard(
            camera,
            calibration,
            config,
            args.calibrations,
            allow_low_quality=args.allow_low_quality,
        )
        if updated is None:
            event(LOGGER, "extrinsic_cancelled", camera_id=camera.id)
            raise SystemExit(f"calibrazione estrinseca annullata: {camera.id}")
        event(
            LOGGER,
            "extrinsic_camera_completed",
            camera_id=camera.id,
            median_error_px=updated.extrinsic_median_error_px,
            p95_error_px=updated.extrinsic_p95_error_px,
            quality_passed=updated.extrinsic_quality_passed,
        )
        if bridge:
            bridge.publish_calibration(updated)


def calibration_main() -> None:
    parser = argparse.ArgumentParser(description="Assistente guidato di calibrazione VisionSystem")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--config", type=Path, help="fallback locale; MQTT resta il canale primario"
    )
    parser.add_argument("--cache", type=Path, default=Path(".state/last_good_config.json"))
    parser.add_argument("--calibrations", type=Path, default=Path("calibrations"))
    parser.add_argument("--mqtt-wait", type=float, default=2.0)
    parser.add_argument("--no-mqtt", action="store_true", help="non apre connessioni MQTT")
    subparsers = parser.add_subparsers(dest="command", required=True)
    board_parser = subparsers.add_parser("board", help="genera la board ChArUco A4/A3")
    board_parser.add_argument("--output", type=Path, default=Path("calibration-assets"))
    board_parser.add_argument("--format", choices=("a4", "a3", "both"), default="a4")
    for command in ("intrinsics", "extrinsics", "all"):
        child = subparsers.add_parser(command)
        child.add_argument("--camera", default="all", help="cam_0..cam_3 oppure all")
        if command != "intrinsics":
            child.add_argument(
                "--allow-low-quality",
                action="store_true",
                help=(
                    "porta la soglia RANSAC da 3px a 30px e salva le estrinseche "
                    "anche se non superano le soglie di qualita"
                ),
            )
            child.add_argument(
                "--reference-markers",
                type=Path,
                help="JSON con i marker di riferimento; sostituisce quelli della config/MQTT",
            )
        if command != "extrinsics":
            child.add_argument("--board-format", choices=("a4", "a3"), default="a4")
    folder_parser = subparsers.add_parser(
        "from-folder", help="calcola le intrinseche da una cartella di foto ChArUco"
    )
    folder_parser.add_argument("--input", type=Path, required=True)
    folder_parser.add_argument("--camera")
    folder_parser.add_argument("--output", type=Path)
    folder_parser.add_argument("--board-format", choices=("a4", "a3"), default="a4")
    folder_parser.add_argument("--sharpness-threshold", type=float, default=80.0)
    folder_parser.add_argument("--allow-low-quality", action="store_true")
    selector_parser = subparsers.add_parser(
        "select-cameras", help="seleziona visualmente le camere e crea un file JSON"
    )
    selector_parser.add_argument("--output", type=Path, default=Path("camera-config.json"))
    selector_parser.add_argument("--base", type=Path)
    selector_parser.add_argument("--sources", type=int, nargs="+")
    selector_parser.add_argument("--max-index", type=int, default=15)
    selector_parser.add_argument("--force", action="store_true")
    subparsers.add_parser("probe", help="verifica le sorgenti video configurate")
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics(
        f"vision-calibrate:{args.command}", verbose=args.verbose
    )
    print(f"Log diagnostico: {diagnostic_path}")
    event(
        LOGGER,
        "calibration_command",
        command=args.command,
        config=args.config,
        calibration_dir=args.calibrations,
        mqtt_enabled=not args.no_mqtt,
    )
    if args.command == "board":
        _handle_board_command(args)
        return
    if args.command == "select-cameras":
        _handle_select_cameras_command(args, parser)
        return
    config, bridge = _configuration_with_mqtt(
        args.config,
        args.cache,
        args.calibrations,
        args.mqtt_wait,
        mqtt_enabled=not args.no_mqtt,
    )
    event(
        LOGGER,
        "configuration_loaded",
        revision=config.revision,
        camera_ids=[camera.id for camera in config.cameras],
        camera_sources={camera.id: camera.source for camera in config.cameras},
        reference_ids=sorted(config.references_by_id()),
        config_source=args.config or args.cache,
    )
    reference_path = getattr(args, "reference_markers", None)
    if reference_path:
        try:
            references = load_reference_markers(reference_path)
            aruco_payload = config.aruco.model_dump(mode="python")
            aruco_payload["reference_markers"] = references
            aruco = ArucoConfig.model_validate(aruco_payload)
            config = config.model_copy(update={"aruco": aruco})
            event(
                LOGGER,
                "reference_markers_overridden",
                path=reference_path,
                reference_ids=sorted(config.references_by_id()),
            )
        except (OSError, ValueError) as error:
            parser.error(f"reference marker non validi: {error}")
    try:
        cameras = _selected_cameras(config, getattr(args, "camera", None) or "all")
        if args.command == "from-folder":
            _handle_from_folder_command(args, config, bridge, parser)
            return
        if args.command == "probe":
            _handle_probe_command(config)
            return
        if args.command in {"intrinsics", "all"}:
            _handle_intrinsics_command(cameras, args, bridge)
        if args.command in {"extrinsics", "all"}:
            _handle_extrinsics_command(cameras, config, args, bridge)
        if bridge:
            bridge.publish_calibration_session({"status": "complete"})
    finally:
        if bridge:
            bridge.stop()


def runtime_main() -> None:
    parser = argparse.ArgumentParser(description="Localizzatore ArUco multi-camera")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cache", type=Path, default=Path(".state/last_good_config.json"))
    parser.add_argument("--calibrations", type=Path, default=Path("calibrations"))
    parser.add_argument("--debug", action="store_true", help="abilita mosaico e vista world locali")
    parser.add_argument("--no-mqtt", action="store_true", help="esegue senza aprire socket MQTT")
    parser.add_argument(
        "--print-poses",
        action="store_true",
        help="stampa una posa JSON per riga; richiede --no-mqtt",
    )
    parser.add_argument(
        "--allow-low-quality",
        action="store_true",
        help=(
            "porta la soglia RANSAC del controllo reference da 3px a 30px "
            "e non esclude automaticamente le camere in drift"
        ),
    )
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics("vision-localizer", verbose=args.verbose)
    print(f"Log diagnostico: {diagnostic_path}")
    if args.print_poses and not args.no_mqtt:
        parser.error("--print-poses richiede --no-mqtt")
    config = _initial_config(args.config, args.cache)
    if args.debug:
        config = config.model_copy(
            update={"debug": config.debug.model_copy(update={"mosaic": True, "world_view": True})}
        )
    runtime = VisionRuntime(
        config,
        args.calibrations,
        args.cache,
        mqtt_enabled=not args.no_mqtt,
        print_poses=args.print_poses,
        allow_low_quality=args.allow_low_quality,
    )
    signal.signal(signal.SIGINT, lambda signum, frame: runtime.stop())
    signal.signal(signal.SIGTERM, lambda signum, frame: runtime.stop())
    runtime.run()

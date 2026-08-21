"""Distributed simulation: synthetic/live nodes, MQTT broker, and fusion server."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import paho.mqtt.client as mqtt
from numpy.typing import NDArray

from ..core.config import CameraCalibration, load_config
from ..core.geometry import invert_transform, marker_object_points, pose_matrix
from ..transport.mqtt import MqttSettings
from ..transport.observations import observations_payload

BROKER_CONTAINER = "vision-sim-mqtt"
BROKER_IMAGE = "eclipse-mosquitto:2"


def reference_marker_poses(config) -> list[tuple[int, float, NDArray[np.float64]]]:
    """Static reference markers from the configuration, as world transforms."""
    return [
        (
            reference.id,
            reference.size_m,
            pose_matrix(
                np.asarray(reference.position_m, dtype=np.float64),
                np.asarray(reference.orientation_xyzw, dtype=np.float64),
            ),
        )
        for reference in config.aruco.reference_markers
    ]


def path_extent(config) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Center and radius of the simulated mobile-tag path, derived from the references."""
    references = config.aruco.reference_markers
    if not references:
        return np.array([0.55, 0.55, 0.0]), np.array([0.35, 0.35])
    positions = np.asarray(
        [list(reference.position_m) for reference in references], dtype=np.float64
    )
    center = (positions.max(axis=0) + positions.min(axis=0)) / 2
    span = (positions.max(axis=0) - positions.min(axis=0)) / 2
    radius = np.clip(span[:2], 0.35, 0.8) * 0.7
    return center, radius


def target_marker_pose(config) -> NDArray[np.float64]:
    """Static target pose at the center of the reference extent, flat on the world plane."""
    center, _radius = path_extent(config)
    return pose_matrix(center, np.array([0.0, 0.0, 0.0, 1.0]))


def default_target_size(config) -> float:
    if config.aruco.auto_mobile_markers.enabled:
        return config.aruco.auto_mobile_markers.default_size_m
    if config.aruco.mobile_markers:
        return config.aruco.mobile_markers[0].size_m
    return 0.07


def project_marker(
    tag_id: int,
    world_from_tag: NDArray[np.float64],
    size_m: float,
    camera_from_world: NDArray[np.float64],
    world_from_camera: NDArray[np.float64],
    camera_matrix: NDArray[np.float64],
    distortion: NDArray[np.float64],
    image_size: tuple[int, int],
    rng: np.random.Generator,
    noise_px: float,
) -> dict | None:
    """Project a marker into the camera; ``None`` when it is not realistically visible."""
    camera_from_tag = camera_from_world @ world_from_tag
    if float(camera_from_tag[2, 3]) <= 0.05:
        return None
    normal = world_from_tag[:3, :3] @ np.array([0.0, 0.0, 1.0])
    view = world_from_camera[:3, 3] - world_from_tag[:3, 3]
    if float(np.dot(normal, view)) <= 0.0:
        return None
    rvec, _ = cv2.Rodrigues(camera_from_tag[:3, :3])
    corners, _ = cv2.projectPoints(
        marker_object_points(size_m),
        rvec,
        camera_from_tag[:3, 3],
        camera_matrix,
        distortion,
    )
    corners = corners.reshape(4, 2)
    width, height = image_size
    if (
        corners[:, 0].min() < -20
        or corners[:, 0].max() > width + 20
        or corners[:, 1].min() < -20
        or corners[:, 1].max() > height + 20
    ):
        return None
    marker_side_px = float(np.linalg.norm(corners[1] - corners[0]))
    corners = corners + rng.normal(0.0, noise_px, size=corners.shape)
    return {
        "tag_id": tag_id,
        "size_m": size_m,
        "corners": corners.tolist(),
        "camera_from_tag": camera_from_tag[:3, :].tolist(),
        "candidates": [],
        "reprojection_error_px": round(float(abs(rng.normal(0.0, noise_px))), 3),
        "marker_side_px": round(marker_side_px, 2),
    }


def run_synthetic_node(args) -> int:
    config = load_config(args.config)
    camera_id = args.camera
    if camera_id not in {camera.id for camera in config.cameras}:
        print(f"errore: camera {camera_id} non presente nella configurazione", file=sys.stderr)
        return 1
    calibration_path = args.calibrations / f"{camera_id}.json"
    if not calibration_path.exists():
        print(f"errore: calibrazione mancante: {calibration_path}", file=sys.stderr)
        return 1
    calibration = CameraCalibration.model_validate_json(
        calibration_path.read_text(encoding="utf-8")
    )
    if calibration.world_from_camera is None:
        print(f"errore: {camera_id} non ha estrinseche (world_from_camera)", file=sys.stderr)
        return 1
    world_from_camera = np.asarray(calibration.world_from_camera, dtype=np.float64)
    camera_from_world = invert_transform(world_from_camera)
    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    distortion = np.asarray(calibration.distortion, dtype=np.float64)
    image_size = tuple(calibration.image_size)
    seed = int("".join(character for character in camera_id if character.isdigit()) or 0)
    rng = np.random.default_rng(seed=seed + 42)
    settings = MqttSettings.from_environment()
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"vision-sim-{camera_id}",
        protocol=mqtt.MQTTv311,
    )
    if settings.username:
        client.username_pw_set(settings.username, settings.password)
    if settings.tls:
        client.tls_set()
    client.connect(settings.host, settings.port, keepalive=30)
    client.loop_start()
    topic = f"{config.base_topic}/observations/{camera_id}"
    print(f"nodo {camera_id}: pubblica su {topic} a {args.hz:.0f} Hz")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda signum, frame: stop.set())
    signal.signal(signal.SIGTERM, lambda signum, frame: stop.set())
    tag_size = default_target_size(config)
    start = time.monotonic()
    next_tick = start
    published = 0
    try:
        while not stop.is_set():
            next_tick += 1.0 / args.hz
            poses = reference_marker_poses(config)
            poses.append((args.tag_id, tag_size, target_marker_pose(config)))
            items = []
            for tag_id, size_m, world_from_tag in poses:
                item = project_marker(
                    tag_id,
                    world_from_tag,
                    size_m,
                    camera_from_world,
                    world_from_camera,
                    camera_matrix,
                    distortion,
                    image_size,
                    rng,
                    args.noise_px,
                )
                if item is not None:
                    items.append(item)
            if items:
                client.publish(
                    topic,
                    json.dumps(
                        observations_payload(camera_id, time.time_ns(), items),
                        separators=(",", ":"),
                    ),
                    qos=0,
                )
                published += 1
            stop.wait(max(0.0, next_tick - time.monotonic()))
    finally:
        client.disconnect()
        client.loop_stop()
    print(f"nodo {camera_id}: terminato ({published} messaggi)")
    return 0


def broker_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_local_host(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "::1"}


def ensure_broker(host: str, port: int, start: bool) -> tuple[bool, bool]:
    """Guarantee a broker on ``host:port``; returns (reachable, started_by_us)."""
    if broker_reachable(host, port):
        print(f"broker MQTT gia attivo su {host}:{port}")
        return True, False
    docker = shutil.which("docker")
    if not start or docker is None or not _is_local_host(host):
        print(
            f"nessun broker su {host}:{port}: avvia mosquitto oppure usa --broker none",
            file=sys.stderr,
        )
        return False, False
    inspect = subprocess.run(
        [docker, "inspect", "--format", "{{.State.Running}}", BROKER_CONTAINER],
        capture_output=True,
        text=True,
    )
    if inspect.returncode == 0:
        subprocess.run([docker, "rm", "-f", BROKER_CONTAINER], check=False)
    print(f"avvio del broker {BROKER_IMAGE} in docker ({BROKER_CONTAINER})...")
    subprocess.run([docker, "pull", "-q", BROKER_IMAGE], check=True)
    result = subprocess.run(
        [
            docker,
            "run",
            "-d",
            "--rm",
            "--name",
            BROKER_CONTAINER,
            "-p",
            f"127.0.0.1:{port}:1883",
            BROKER_IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"avvio del broker fallito: {result.stderr.strip()}", file=sys.stderr)
        return False, False
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if broker_reachable(host, port):
            print(f"broker pronto su {host}:{port}")
            return True, True
        time.sleep(0.5)
    print("broker non raggiungibile dopo 60 s", file=sys.stderr)
    return False, False


def stop_broker_if_owned(started_by_us: bool, keep: bool) -> None:
    if not started_by_us or keep:
        return
    docker = shutil.which("docker")
    if docker is None:
        return
    subprocess.run([docker, "rm", "-f", BROKER_CONTAINER], check=False)
    print(f"broker docker {BROKER_CONTAINER} rimosso")


def select_cameras(config, tokens: list[str] | None = None):
    """Resolve camera selections: camera ids (``cam_0``), configured sources (``3``) or paths."""
    if not tokens:
        return list(config.cameras)
    selected = []
    for token in tokens:
        camera = None
        if token.startswith("cam_"):
            camera = next((camera for camera in config.cameras if camera.id == token), None)
        elif token.isdigit():
            camera = next(
                (
                    camera
                    for camera in config.cameras
                    if isinstance(camera.source, int) and camera.source == int(token)
                ),
                None,
            )
        else:
            camera = next(
                (camera for camera in config.cameras if str(camera.source) == token), None
            )
        if camera is None:
            available = ", ".join(
                f"{camera.id} (sorgente {camera.source})" for camera in config.cameras
            )
            raise ValueError(f"camera '{token}' non trovata; disponibili: {available}")
        selected.append(camera)
    return selected


def _spawn_synthetic_node(camera, args, env) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "vision_system.apps.simulator",
            "--role",
            "node",
            "--camera",
            camera.id,
            "--config",
            str(args.config.resolve()),
            "--calibrations",
            str(args.calibrations.resolve()),
            "--hz",
            str(args.hz),
            "--tag-id",
            str(args.tag_id),
            "--noise-px",
            str(args.noise_px),
        ],
        env=env,
    )


def _spawn_live_node(camera, args, env) -> subprocess.Popen:
    node_bin = Path(sys.executable).parent / "vision-node"
    command = [
        str(node_bin),
        "--camera",
        camera.id,
        "--config",
        str(args.config.resolve()),
        "--calibrations",
        str(args.calibrations.resolve()),
        "--cache",
        str(Path(".state").resolve() / f"sim-live-{camera.id}-last_good_config.json"),
    ]
    if args.node_debug:
        command.append("--debug")
    if args.allow_low_quality:
        command.append("--allow-low-quality")
    return subprocess.Popen(command, env=env)


def run_full_simulation(args) -> int:
    config = load_config(args.config)
    try:
        cameras = select_cameras(config, args.cameras)
    except ValueError as error:
        print(f"errore: {error}", file=sys.stderr)
        return 1
    settings = MqttSettings(host=args.mqtt_host, port=args.mqtt_port)
    reachable, started_by_us = ensure_broker(
        settings.host, settings.port, args.broker == "auto"
    )
    if not reachable:
        return 1
    env = dict(os.environ)
    env["VISION_MQTT_HOST"] = settings.host
    env["VISION_MQTT_PORT"] = str(settings.port)
    for key in ("VISION_MQTT_USERNAME", "VISION_MQTT_PASSWORD", "VISION_MQTT_TLS"):
        env.pop(key, None)
    children: list[subprocess.Popen] = []
    node_ids: list[str] = []
    for camera in cameras:
        if not (args.calibrations / f"{camera.id}.json").exists():
            print(f"avviso: {camera.id} senza calibrazione, nodo saltato")
            continue
        if args.live:
            children.append(_spawn_live_node(camera, args, env))
        else:
            children.append(_spawn_synthetic_node(camera, args, env))
        node_ids.append(camera.id)
    if not children:
        print("nessun nodo avviabile: mancano le calibrazioni", file=sys.stderr)
        stop_broker_if_owned(started_by_us, args.keep_broker)
        return 1
    server_bin = Path(sys.executable).parent / "vision-server"
    server = subprocess.Popen(
        [
            str(server_bin),
            "--debug",
            "--config",
            str(args.config.resolve()),
            "--calibrations",
            str(args.calibrations.resolve()),
            "--cache",
            str(Path(".state/sim-server-last_good_config.json")),
        ],
        env=env,
    )
    children.append(server)
    print()
    print("Simulazione distribuita avviata:")
    print(f"  broker : {settings.host}:{settings.port}")
    if args.live:
        print(
            f"  nodi   : {', '.join(node_ids)} (vision-node, webcam reali)"
            f" -> {config.base_topic}/observations/<camera>"
        )
        if args.node_debug:
            print("  nodi   : anteprima annotata aperta per ogni camera")
    else:
        print(
            f"  nodi   : {', '.join(node_ids)} (sintetici)"
            f" -> {config.base_topic}/observations/<camera>"
        )
        print(
            f"  world  : finestra 'VisionSystem - world' con il tag statico ID {args.tag_id}"
        )
    print(f"  server : fusion -> {config.base_topic}/pose/<tag_id>")
    print("  CTRL+C termina nodi, server e broker")

    def _forward(signum, frame):
        for child in children:
            if child.poll() is None:
                child.send_signal(signum)
        signal.signal(signum, signal.SIG_DFL)

    signal.signal(signal.SIGINT, _forward)
    signal.signal(signal.SIGTERM, _forward)
    exit_code = 0
    try:
        exit_code = server.wait()
    finally:
        for child in children:
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + 5.0
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
        stop_broker_if_owned(started_by_us, args.keep_broker)
    return exit_code


def simulator_main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Simula 4 nodi camera, un broker MQTT e il server di fusione sulla stessa macchina"
        )
    )
    parser.add_argument(
        "--role", choices=["full", "node"], default="full", help=argparse.SUPPRESS
    )
    parser.add_argument("--config", type=Path, default=Path("config.local.json"))
    parser.add_argument("--calibrations", type=Path, default=Path("calibrations"))
    parser.add_argument("--tag-id", type=int, default=23, help="ID del tag simulato")
    parser.add_argument(
        "--hz", type=float, default=25.0, help="frequenza di pubblicazione dei nodi"
    )
    parser.add_argument(
        "--noise-px", type=float, default=0.5, help="rumore gaussiano sugli angoli (px)"
    )
    parser.add_argument("--camera", help="camera del nodo (solo con --role node)")
    parser.add_argument("--mqtt-host", default="localhost", help="host del broker MQTT")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="porta del broker MQTT")
    parser.add_argument(
        "--broker",
        choices=["auto", "none"],
        default="auto",
        help="auto avvia il broker in docker se assente; none richiede un broker gia attivo",
    )
    parser.add_argument(
        "--keep-broker", action="store_true", help="non rimuovere il broker docker all'uscita"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="usa webcam reali (vision-node) invece dei nodi sintetici",
    )
    parser.add_argument(
        "--cameras",
        nargs="+",
        help="camere da avviare: id (cam_1) oppure sorgente (4) oppure percorso /dev/videoX",
    )
    parser.add_argument(
        "--node-debug",
        action="store_true",
        help="con --live, apre l'anteprima annotata di ogni camera",
    )
    parser.add_argument(
        "--allow-low-quality",
        action="store_true",
        help="con --live, i nodi non escludono le camere in drift",
    )
    args = parser.parse_args()
    if args.role == "node":
        if not args.camera:
            parser.error("--role node richiede --camera")
        raise SystemExit(run_synthetic_node(args))
    raise SystemExit(run_full_simulation(args))


if __name__ == "__main__":
    simulator_main()

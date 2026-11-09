"""How many cameras the deployment has, and which of them are wired to this PC.

Two documents, deliberately: ``config.local.json`` is the *shared* roster — every
node PC and the fusion server must agree on it, so it travels with the deployment
— while ``config.local.setup.json`` records what is true of *this* machine only:
whether the operator is running everything here or spreading nodes over several
PCs, which cameras are plugged into this box, and where its broker lives.

Keeping them apart is what makes the 1+2+1 arrangement expressible at all. A PC
that owns two of the four cameras still has to publish on the four-camera roster,
so "how many cameras exist" and "how many I can see" cannot be the same number.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import AppConfig, CameraConfig, load_config, save_json

MAX_CAMERAS = 4
# The documented starting point a new deployment is cut from, rather than the
# library defaults: it carries the arena's dictionary, marker sizes, fusion
# settings and the source numbers each cam_N is expected to sit on.
DEFAULT_TEMPLATE_NAME = "config.example.json"
# Placeholder sources for freshly added cameras; step 2 replaces them.
MAX_SOURCE_PLACEHOLDER = 64
STABLE_DEVICE_DIR = Path("/dev/v4l/by-id")
LOCAL_BROKER_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# The address a container reaches its own host on; compose.node.yaml maps it.
HOST_GATEWAY = "host.docker.internal"

DeploymentMode = Literal["single-pc", "distributed"]


class DeploymentSetup(BaseModel):
    """This PC's share of the deployment. Never published, never read by a node."""

    model_config = ConfigDict(extra="forbid")

    mode: DeploymentMode = "single-pc"
    # Meaningful in distributed mode only: single-pc owns the whole roster by
    # definition, and storing a copy of it here would be a second source of truth.
    local_camera_ids: list[str] = Field(default_factory=list)
    mqtt_host: str = "localhost"
    mqtt_port: int = Field(default=1883, ge=1, le=65535)

    @field_validator("mqtt_host")
    @classmethod
    def plain_hostname(cls, value: str) -> str:
        host = value.strip()
        if not host or any(char.isspace() for char in host) or "://" in host:
            raise ValueError("enter a broker hostname or IP address, without a URL scheme")
        return host

    def local_ids(self, config: AppConfig) -> list[str]:
        """The cameras this PC opens: the whole roster, or the chosen subset."""
        if self.mode == "single-pc":
            return [camera.id for camera in config.cameras]
        return [camera.id for camera in select_cameras(config, self.local_camera_ids)]

    def remote_ids(self, config: AppConfig) -> list[str]:
        """The rest of the roster: calibrated here, but opened by another PC."""
        local = set(self.local_ids(config))
        return [camera.id for camera in config.cameras if camera.id not in local]


def setup_path(config_path: Path) -> Path:
    """``config.local.json`` -> ``config.local.setup.json``, alongside it."""
    return config_path.with_suffix(".setup.json")


def load_setup(config_path: Path) -> DeploymentSetup:
    """The saved preferences, or the single-PC defaults when there are none."""
    path = setup_path(config_path)
    if not path.exists():
        return DeploymentSetup()
    return DeploymentSetup.model_validate_json(path.read_text(encoding="utf-8"))


def load_template(path: Path | None, config_path: Path | None = None) -> AppConfig | None:
    """The example configuration new cameras are cut from, or ``None``.

    A relative name is also looked up next to the configuration being edited, so
    the default ``config.example.json`` is found both when the panel runs from
    ``apps/vision`` and when it is pointed at a configuration somewhere else.
    A template that is absent or unreadable is not an error: the roster simply
    falls back to synthesised cameras.
    """
    if path is None:
        return None
    candidates = [path]
    if not path.is_absolute() and config_path is not None:
        candidates.append(config_path.parent / path.name)
    for candidate in candidates:
        try:
            return load_config(candidate)
        except (OSError, ValueError):
            continue
    return None


def select_cameras(config: AppConfig, camera_ids: Sequence[str] | None) -> list[CameraConfig]:
    """Resolve ids against the roster, in roster order. ``None`` means all of them.

    Unlike trimming the roster, this never removes a camera from the deployment:
    the ids that are not selected stay in the configuration, because the PC that
    owns them still publishes under them.
    """
    if camera_ids is None:
        return list(config.cameras)
    wanted = list(camera_ids)
    if len(set(wanted)) != len(wanted):
        raise ValueError("a camera can only be selected once")
    known = {camera.id for camera in config.cameras}
    if unknown := [camera_id for camera_id in wanted if camera_id not in known]:
        raise ValueError(f"not in the deployment roster: {', '.join(unknown)}")
    selected = set(wanted)
    return [camera for camera in config.cameras if camera.id in selected]


def parse_roster(request: Sequence[str], config: AppConfig) -> list[str]:
    """What the operator asked the roster to be: named ids, or just how many.

    ``["cam_2", "cam_3"]`` is the roster, literally — the arena has those two and
    no others. ``["3"]`` is the shorthand for "three cameras, you pick the names",
    which fills in ``cam_0..cam_2``. An empty request leaves the roster alone.

    The two spellings exist because both questions are real: an operator setting
    up a fresh arena counts webcams, while one joining an existing deployment is
    told which ids it uses, and a count can only ever produce ``cam_0`` upwards.
    """
    wanted = [part for part in request if part]
    if not wanted:
        return [camera.id for camera in config.cameras]
    if len(wanted) == 1 and wanted[0].isdigit():
        return numbered_roster(config, int(wanted[0]))
    if any(part.isdigit() for part in wanted):
        raise ValueError("give either a number of cameras or their ids, not both")
    if len(set(wanted)) != len(wanted):
        raise ValueError("a camera can only appear once in the roster")
    if len(wanted) > MAX_CAMERAS:
        raise ValueError(f"the deployment can have at most {MAX_CAMERAS} cameras")
    return wanted


def numbered_roster(config: AppConfig, count: int) -> list[str]:
    """``count`` ids: the ones already configured first, then free ``cam_N``."""
    if not 1 <= count <= MAX_CAMERAS:
        raise ValueError(f"the deployment can have 1 to {MAX_CAMERAS} cameras")
    ids = [camera.id for camera in config.cameras][:count]
    index = 0
    while len(ids) < count:
        candidate = f"cam_{index}"
        if candidate not in ids:
            ids.append(candidate)
        index += 1
    # The names were ours to choose, so hand them back in their natural order.
    return sorted(ids)


def resolve_roster(
    config: AppConfig,
    camera_ids: Sequence[str],
    template: AppConfig | None = None,
) -> AppConfig:
    """Make the roster exactly ``camera_ids``, in that order.

    A camera already configured is carried over untouched — id, source and every
    setting — so naming a roster never invalidates a calibration. One that is new
    comes from ``template``, or is synthesised when the template has nothing to
    say about it.
    """
    if not camera_ids:
        raise ValueError("the deployment needs at least one camera")
    if len(set(camera_ids)) != len(camera_ids):
        raise ValueError("a camera can only appear once in the roster")
    if len(camera_ids) > MAX_CAMERAS:
        raise ValueError(f"the deployment can have at most {MAX_CAMERAS} cameras")
    configured = {camera.id: camera for camera in config.cameras}
    from_template = {c.id: c for c in template.cameras} if template is not None else {}
    cameras: list[CameraConfig] = []
    # A new camera gets a source nobody is using yet. It is a placeholder either
    # way — step 2 assigns the real device — but two cameras sharing a source is
    # a configuration that looks deliberate and behaves badly.
    taken = {configured[i].source for i in camera_ids if i in configured}
    for camera_id in camera_ids:
        fresh = configured.get(camera_id)
        if fresh is None:
            fresh = from_template.get(camera_id)
            if fresh is None or fresh.source in taken:
                source = next(n for n in range(MAX_SOURCE_PLACEHOLDER) if n not in taken)
                fresh = (
                    CameraConfig(id=camera_id, source=source)
                    if fresh is None
                    else fresh.model_copy(update={"source": source})
                )
            taken.add(fresh.source)
        cameras.append(fresh)
    return config.model_copy(update={"cameras": cameras})


def apply_setup(
    config: AppConfig,
    setup: DeploymentSetup,
    camera_ids: Sequence[str],
    template: AppConfig | None = None,
) -> AppConfig:
    """Set the roster and check the local selection still fits it.

    Both halves are validated before either is written: a local camera that the
    new roster no longer contains has to be caught here, not after it is on disk.
    """
    resized = resolve_roster(config, camera_ids, template)
    setup.local_ids(resized)
    if resized.cameras == config.cameras:
        return config
    return resized.model_copy(update={"revision": config.revision + 1})


def save_setup(
    config_path: Path,
    setup: DeploymentSetup,
    camera_ids: Sequence[str],
    template: AppConfig | None = None,
) -> AppConfig:
    """Write both documents, or neither. Returns the roster as it now stands.

    ``camera_ids`` is the request as typed: ids, a bare count, or empty to leave
    the roster alone.

    With no configuration yet the whole document comes from ``template``, so a
    deployment set up from scratch starts on the documented example — its marker
    dictionary, sizes and fusion settings — instead of the library defaults.
    """
    if config_path.exists():
        base = load_config(config_path)
    elif template is not None:
        base = template
    else:
        # AppConfig() defaults to a four-camera roster; with nothing to cut from,
        # start at one so the count the operator typed is what they get.
        base = AppConfig(cameras=[CameraConfig(id="cam_0", source=0)])
    # Parsed here rather than by each caller, so the "3" shorthand and the id
    # list mean the same thing from the panel and from the command line.
    config = apply_setup(base, setup, parse_roster(camera_ids, base), template)
    save_json(config_path, config)
    save_json(setup_path(config_path), setup)
    return config


def broker_host_for_container(host: str) -> str:
    """A container cannot dial its own host by name; compose maps the gateway."""
    return HOST_GATEWAY if host in LOCAL_BROKER_HOSTS else host


def launch_commands(
    config_path: Path, config: AppConfig, setup: DeploymentSetup
) -> list[str]:
    """The Make commands that start this deployment, for the operator to copy.

    The panel prints them instead of running them: starting containers is the one
    step that has to happen on more than one machine, and a button here could only
    ever do the local third of it.
    """
    gui_config = shlex.quote(str(config_path))
    gui = (
        f"make gui CONFIG={gui_config} GUI_MQTT_HOST={shlex.quote(setup.mqtt_host)} "
        f"GUI_MQTT_PORT={setup.mqtt_port}"
    )
    if setup.mode == "single-pc":
        return [
            "# Everything on this PC: broker, cameras and fusion in one stack.",
            "make all",
            gui,
        ]
    broker = broker_host_for_container(setup.mqtt_host)
    lines = [
        "# config.local.json must be identical on every PC of the deployment:",
        f"#   {', '.join(camera.id for camera in config.cameras)}",
        "# On the fusion server PC (starts the broker too):",
        "make server",
        "# On this PC, one node per camera plugged in here:",
    ]
    local = setup.local_ids(config)
    lines += [
        f"make client CAMERA={shlex.quote(camera_id)} "
        f"MQTT_HOST={shlex.quote(broker)} MQTT_PORT={setup.mqtt_port}"
        for camera_id in local
    ] or ["# (no camera is assigned to this PC)"]
    if remote := setup.remote_ids(config):
        lines.append(f"# Run the same command on the PCs owning: {', '.join(remote)}")
    lines.append(gui)
    return lines


def source_index(source: int | str) -> int | None:
    """The ``/dev/videoN`` number a configured source points at, if it is one.

    The inverse of :func:`stable_camera_source`, and needed because the tools that
    enumerate devices work in indices: without it, a roster saved with by-id
    aliases would come back to the selector with nothing pre-assigned.
    """
    if isinstance(source, int):
        return source
    try:
        name = Path(source).resolve(strict=True).name
    except OSError:
        return None
    suffix = name.removeprefix("video")
    return int(suffix) if name.startswith("video") and suffix.isdigit() else None


def stable_camera_source(source: int | str) -> int | str:
    """A ``/dev/v4l/by-id`` alias for this exact device, when one exists.

    Kernel video node numbers are assigned in enumeration order, so the camera
    that was source 3 at calibration time can be source 1 after a reboot or a
    replugged hub — which is how a node ends up reporting "cannot open source 3"
    with the camera sitting right there. The by-id alias is derived from the USB
    descriptor and survives both. Resolution failures leave the source untouched:
    guessing a different device would be far worse than keeping the number.
    """
    target = Path(f"/dev/video{source}") if isinstance(source, int) else Path(source)
    try:
        resolved = target.resolve(strict=True)
        aliases = sorted(STABLE_DEVICE_DIR.iterdir())
    except OSError:
        return source
    for alias in aliases:
        try:
            if alias.resolve(strict=True) == resolved:
                return str(alias)
        except OSError:
            continue
    return source

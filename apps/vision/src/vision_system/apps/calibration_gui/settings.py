"""The panel's own settings: which files and folders every stage works against."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ...core.config import AppConfig
from ...core.setup import DEFAULT_TEMPLATE_NAME, DeploymentSetup

DEFAULT_CACHE = Path(".state/last_good_config.json")
DEFAULT_CALIBRATIONS = Path("calibrations")
DEFAULT_BOARD_OUTPUT = Path("calibration-assets")
DEFAULT_PHOTO_ROOT = Path("photo")
DEFAULT_TEMPLATE = Path(DEFAULT_TEMPLATE_NAME)
DEFAULT_SHARPNESS_THRESHOLD = 80.0


@dataclass
class GuiSettings:
    """Mutable because the operator edits these in the header and the step forms."""

    config_path: Path | None = None
    cache_path: Path = DEFAULT_CACHE
    calibrations_dir: Path = DEFAULT_CALIBRATIONS
    board_format: str = "a4"
    board_output: Path = DEFAULT_BOARD_OUTPUT
    reference_markers_path: Path | None = None
    photo_root: Path = DEFAULT_PHOTO_ROOT
    allow_low_quality: bool = False
    sharpness_threshold: float = DEFAULT_SHARPNESS_THRESHOLD
    # The panel never publishes; a stage only talks to the broker when asked to.
    mqtt_enabled: bool = False
    verbose: bool = False
    # ------------------------------------------------------------- deployment
    # Edited in step 1 and persisted next to the configuration. ``setup`` is this
    # PC's half; ``roster`` is the pending edit to the shared camera list, as the
    # operator typed it — ids, or a bare count — applied only when they save.
    setup: DeploymentSetup = field(default_factory=DeploymentSetup)
    roster: tuple[str, ...] = ()
    # The example the roster is cut from: the whole document when there is no
    # configuration yet, and each added camera's settings afterwards.
    template_path: Path | None = DEFAULT_TEMPLATE
    # Which cameras step 2 reconfigures: empty is this PC's share, ``("all",)``
    # the whole roster, or an explicit pick.
    cameras: tuple[str, ...] = ()

    def local_camera_ids(self, config: AppConfig) -> tuple[str, ...]:
        """The cameras this PC can probe, calibrate and open. Never raises.

        A stale selection — an id that a roster resize removed — degrades to the
        whole roster rather than blocking the step: the operator is mid-edit, and
        the setup step is where that gets corrected.
        """
        try:
            return tuple(self.setup.local_ids(config))
        except ValueError:
            return tuple(camera.id for camera in config.cameras)


def resolve_camera_ids(
    chosen: Sequence[str], roster: Sequence[str], local: Sequence[str] | None
) -> tuple[str, ...]:
    """Which cameras a step acts on, given what the operator typed in its form.

    The default is this PC's share, because that is what can be probed and opened
    from here. ``all`` is the escape hatch for the operator who is setting up the
    whole arena from one machine, and naming ids picks any subset of the roster.
    """
    if tuple(chosen) == ("all",):
        return tuple(roster)
    if chosen:
        return tuple(chosen)
    return tuple(roster if local is None else local)

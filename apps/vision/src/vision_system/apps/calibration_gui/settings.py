"""The panel's own settings: which files and folders every stage works against."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_CACHE = Path(".state/last_good_config.json")
DEFAULT_CALIBRATIONS = Path("calibrations")
DEFAULT_BOARD_OUTPUT = Path("calibration-assets")
DEFAULT_PHOTO_ROOT = Path("photo")
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

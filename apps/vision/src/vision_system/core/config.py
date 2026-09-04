"""Pydantic configuration models for cameras, ArUco tags, fusion, and calibrations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CameraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: int | str
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    autofocus: bool = False
    auto_exposure: bool = False
    exposure: float | None = None
    focus: float | None = None
    # UVC absolute zoom. ``None`` leaves the camera/driver default untouched;
    # larger values produce a narrower field of view on cameras that expose it.
    zoom: float | None = Field(default=None, ge=0)
    # Hardware-independent centered crop, resized back to width/height.
    digital_zoom: float = Field(default=1.0, ge=1.0, le=8.0)


class MobileMarkerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    size_m: float = Field(gt=0)
    name: str | None = None


class ReferenceMarkerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    size_m: float = Field(gt=0)
    position_m: tuple[float, float, float]
    orientation_xyzw: tuple[float, float, float, float]
    uncertainty_m: float = Field(default=0.005, ge=0)
    name: str | None = None

    @field_validator("orientation_xyzw")
    @classmethod
    def normalized_quaternion(
        cls, value: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        norm = sum(component * component for component in value) ** 0.5
        if norm < 1e-9:
            raise ValueError("orientation quaternion cannot be zero")
        return tuple(component / norm for component in value)  # type: ignore[return-value]


class AutoMobileMarkerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    default_size_m: float = Field(default=0.12, gt=0)
    ignored_ids: list[int] = Field(default_factory=list)

    @field_validator("ignored_ids")
    @classmethod
    def unique_ignored_ids(cls, value: list[int]) -> list[int]:
        if any(marker_id < 0 for marker_id in value):
            raise ValueError("ignored marker ids must be non-negative")
        if len(set(value)) != len(value):
            raise ValueError("duplicate ignored marker id")
        return value


class AnchorFrameConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin_id: int = Field(ge=0)
    x_axis_id: int = Field(ge=0)
    y_axis_id: int = Field(ge=0)
    opposite_id: int | None = Field(default=None, ge=0)
    x_distance_m: float = Field(gt=0)
    y_distance_m: float = Field(gt=0)
    plane_z_m: float = 0.0

    @model_validator(mode="after")
    def unique_roles(self) -> AnchorFrameConfig:
        ids = [self.origin_id, self.x_axis_id, self.y_axis_id]
        if self.opposite_id is not None:
            ids.append(self.opposite_id)
        if len(set(ids)) != len(ids):
            raise ValueError("anchor frame roles must use different marker ids")
        return self


class ArucoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dictionary: str = "DICT_4X4_50"
    mobile_markers: list[MobileMarkerConfig] = Field(default_factory=list)
    auto_mobile_markers: AutoMobileMarkerConfig = Field(default_factory=AutoMobileMarkerConfig)
    # Whitelist used before the reference map exists. Once mapping completes,
    # ``reference_markers`` contains the full measured poses for runtime use.
    reference_ids: list[int] = Field(default_factory=list)
    reference_markers: list[ReferenceMarkerConfig] = Field(default_factory=list)
    anchor_frame: AnchorFrameConfig | None = None
    adaptive_thresh_win_size_min: int = 3
    adaptive_thresh_win_size_max: int = 23
    corner_refinement: bool = True
    perspective_remove_pixel_per_cell: int = Field(default=4, ge=1, le=20)
    error_correction_rate: float = Field(default=0.6, ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def accept_reference_id_shorthand(cls, value: object) -> object:
        """Accept ``reference_markers: [13, ...]`` as a bootstrap shorthand."""
        if not isinstance(value, dict):
            return value
        raw_markers = value.get("reference_markers")
        if not isinstance(raw_markers, list) or not raw_markers:
            return value
        if not all(type(marker_id) is int for marker_id in raw_markers):
            return value
        if value.get("reference_ids"):
            raise ValueError(
                "use either reference_ids or the reference_markers ID shorthand, not both"
            )
        normalized = dict(value)
        normalized["reference_ids"] = list(raw_markers)
        normalized["reference_markers"] = []
        return normalized

    @model_validator(mode="after")
    def unique_marker_roles(self) -> ArucoConfig:
        mobile = [marker.id for marker in self.mobile_markers]
        declared_reference = self.reference_ids
        reference = [marker.id for marker in self.reference_markers]
        if len(set(mobile)) != len(mobile):
            raise ValueError("duplicate mobile marker id")
        if any(marker_id < 0 for marker_id in declared_reference):
            raise ValueError("reference ids must be non-negative")
        if len(set(declared_reference)) != len(declared_reference):
            raise ValueError("duplicate reference id")
        if len(set(reference)) != len(reference):
            raise ValueError("duplicate reference marker id")
        overlap = set(mobile) & (set(declared_reference) | set(reference))
        if overlap:
            raise ValueError(f"marker ids cannot be both mobile and reference: {sorted(overlap)}")
        ignored = set(self.auto_mobile_markers.ignored_ids)
        if explicit_ignored := ignored & set(mobile):
            raise ValueError(
                f"explicit mobile marker ids cannot also be ignored: {sorted(explicit_ignored)}"
            )
        if self.anchor_frame is not None:
            frame = self.anchor_frame
            roles = {frame.origin_id, frame.x_axis_id, frame.y_axis_id}
            if frame.opposite_id is not None:
                roles.add(frame.opposite_id)
            missing = roles - set(reference)
            if missing:
                raise ValueError(f"anchor frame marker ids are not references: {sorted(missing)}")
            positions = {
                frame.origin_id: (0.0, 0.0, frame.plane_z_m),
                frame.x_axis_id: (frame.x_distance_m, 0.0, frame.plane_z_m),
                frame.y_axis_id: (0.0, frame.y_distance_m, frame.plane_z_m),
            }
            if frame.opposite_id is not None:
                positions[frame.opposite_id] = (
                    frame.x_distance_m,
                    frame.y_distance_m,
                    frame.plane_z_m,
                )
            self.reference_markers = [
                marker.model_copy(
                    update={"position_m": positions.get(marker.id, marker.position_m)}
                )
                for marker in self.reference_markers
            ]
        return self


class FusionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_ms: float = Field(default=40.0, gt=0, le=250)
    publish_hz: float = Field(default=20.0, gt=0, le=100)
    max_reprojection_error_px: float = Field(default=4.0, gt=0)
    huber_scale_px: float = Field(default=1.5, gt=0)
    tracker_filter: Literal["one_euro", "alpha_beta"] = "one_euro"
    one_euro_min_cutoff_hz: float = Field(default=2.0, gt=0, le=100)
    one_euro_beta: float = Field(default=5.0, ge=0, le=100)
    one_euro_derivative_cutoff_hz: float = Field(default=1.0, gt=0, le=100)
    tracker_position_gain: float = Field(default=0.65, gt=0, le=1)
    tracker_velocity_gain: float = Field(default=0.12, ge=0, le=1)
    tracker_orientation_gain: float = Field(default=0.55, gt=0, le=1)
    tracker_max_innovation_m: float = Field(default=0.15, ge=0)
    stale_after_ms: float = Field(default=250.0, gt=0)


class DebugConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mosaic: bool = False
    world_view: bool = False
    trail_seconds: float = Field(default=3.0, ge=0, le=60)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    site: str = "default"
    system_id: str = "indoor-01"
    cameras: list[CameraConfig] = Field(
        default_factory=lambda: [
            CameraConfig(id="cam_0", source=5),
            CameraConfig(id="cam_1", source=1),
            CameraConfig(id="cam_2", source=2),
            CameraConfig(id="cam_3", source=4),
        ]
    )
    aruco: ArucoConfig = Field(default_factory=ArucoConfig)
    fusion: FusionConfig = Field(default_factory=FusionConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)

    @model_validator(mode="after")
    def at_least_one_unique_camera(self) -> AppConfig:
        if not self.cameras:
            raise ValueError("at least one camera is required")
        ids = [camera.id for camera in self.cameras]
        if len(set(ids)) != len(ids):
            raise ValueError("camera ids must be unique")
        return self

    @property
    def base_topic(self) -> str:
        return f"vision/{self.site}/{self.system_id}"

    def mobile_marker_sizes(self) -> dict[int, float]:
        return {marker.id: marker.size_m for marker in self.aruco.mobile_markers}

    def references_by_id(self) -> dict[int, ReferenceMarkerConfig]:
        return {marker.id: marker for marker in self.aruco.reference_markers}


class CameraCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    camera_id: str
    source: int | str
    image_size: tuple[int, int]
    camera_zoom: float | None = Field(default=None, ge=0)
    camera_digital_zoom: float = Field(default=1.0, ge=1.0, le=8.0)
    camera_matrix: list[list[float]]
    distortion: list[float]
    distortion_model: Literal["brown_rational"] = "brown_rational"
    world_from_camera: list[list[float]] | None = None
    intrinsic_median_error_px: float
    intrinsic_p95_error_px: float
    extrinsic_median_error_px: float | None = None
    extrinsic_p95_error_px: float | None = None
    extrinsic_quality_passed: bool | None = None
    captured_at: str
    opencv_version: str
    board_checksum: str


def load_config(path: Path) -> AppConfig:
    return AppConfig.model_validate_json(path.read_text(encoding="utf-8"))


def initial_config(config_path: Path | None, cache_path: Path) -> AppConfig:
    if config_path:
        return load_config(config_path)
    if cache_path.exists():
        return load_config(cache_path)
    return AppConfig()


def load_reference_markers(path: Path) -> list[ReferenceMarkerConfig]:
    """Load references from a list, a references document, or a full app config."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    markers: object
    if isinstance(payload, list):
        markers = payload
    elif isinstance(payload, dict) and "reference_markers" in payload:
        markers = payload["reference_markers"]
    elif isinstance(payload, dict) and isinstance(payload.get("aruco"), dict):
        markers = payload["aruco"].get("reference_markers")
    else:
        raise ValueError(
            "il file deve contenere una lista, 'reference_markers', oppure "
            "'aruco.reference_markers'"
        )
    if not isinstance(markers, list):
        raise ValueError("reference_markers deve essere una lista")
    return [ReferenceMarkerConfig.model_validate(marker) for marker in markers]


def save_json(path: Path, value: BaseModel | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)

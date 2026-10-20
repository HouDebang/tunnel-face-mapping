"""Persisted data contracts. All exported geometry is in the oriented original.

Canvas origin top left; +x right, +y down. Pixel centers are (x+.5,y+.5).
Lengths use complete unsimplified traces; areas use exact raster counts.
"""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

COORDINATE_SYSTEM = "original_image_pixels"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ImageMetadata(Contract):
    input_path: str
    input_sha256: str
    source_size: list[int]
    original_size: list[int]
    working_size: list[int]
    scale_x: float
    scale_y: float
    exif_transposed: bool
    coordinate_system: str = COORDINATE_SYSTEM
    coordinate_convention: str = "origin top-left; x right; y down; pixel centers (column+0.5,row+0.5); canvas [0,W]x[0,H]"
    working_to_original: list[list[float]]
    original_to_working: list[list[float]]
    details: dict[str, Any] = Field(default_factory=dict)


class FaceRegion(Contract):
    pixel_area: int
    area_ratio: float
    polygon: list[dict[str, Any]]
    method: str
    status: str
    verified: bool = False
    mask_path: str = "face_mask.png"
    details: dict[str, Any] = Field(default_factory=dict)


class LithologyRegion(Contract):
    region_id: str
    label_value: int
    category: Literal["unknown"] = "unknown"
    polygon: list[dict[str, Any]]
    pixel_area: int
    area_ratio: float
    area_m2: float | None = None
    segmentation_method: str
    mask_path: str
    status: str = "image_feature_candidate"


class FractureTrace(Contract):
    id: str
    points: list[list[float]]
    display_points: list[list[float]]
    start_node: dict[str, Any]
    end_node: dict[str, Any]
    is_closed: bool
    source: str
    detection_source: str
    length_px: float
    length_m: float | None = None
    endpoint_direction_deg: float | None = None
    principal_direction_deg: float | None = None
    status: str = "candidate_fracture_trace"


class GeometricStatistics(Contract):
    fracture_count: int
    lithology_region_count: int | None = None
    total_length_px: float
    observation_area_px2: int
    p21_per_px: float | None
    total_length_m: float | None = None
    observation_area_m2: float | None = None
    p21_per_m: float | None = None
    statistics_status: str
    details: dict[str, Any] = Field(default_factory=dict)


class StepStatus(Contract):
    step: str
    status: Literal["ok", "failed", "skipped"]
    input: str
    output: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0
    error: str | None = None


class QualityReport(Contract):
    status: str = "running"
    steps: list[StepStatus] = Field(default_factory=list)
    checks: dict[str, bool | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: [
        "Lithology regions are image-feature partitions with unknown geological category.",
        "Dark linear candidates can include shadows, drilling marks and texture; fracture accuracy is unvalidated.",
        "Automatic ROI has no verified semantic exclusion of equipment, people, lining or background.",
        "Image axial angles are not geological strike or dip.",
        "No labeled real-image accuracy evaluation has been performed by this pipeline.",
        "Default polygons follow exact raster cell boundaries; optional simplified display contours must not replace mask area.",
    ])


class PipelineResult(Contract):
    schema_version: str = "1.0"
    algorithm_version: str = "face_mapping_baseline_0.1.0"
    coordinate_system: str = COORDINATE_SYSTEM
    status: str
    image: ImageMetadata | None = None
    face: FaceRegion | None = None
    lithology_regions: list[LithologyRegion] | None = None
    fractures: list[FractureTrace] | None = None
    statistics: GeometricStatistics | None = None
    calibration: dict[str, Any]
    algorithms: dict[str, Any] = Field(default_factory=dict)
    topology: dict[str, Any] | None = None
    config: dict[str, Any]
    quality: QualityReport
    artifacts: list[str] = Field(default_factory=list)

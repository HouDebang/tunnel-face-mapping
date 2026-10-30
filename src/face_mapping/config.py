"""Validated centralized algorithm parameters. Unknown keys are errors."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PreprocessConfig(StrictConfig):
    max_dimension: int = Field(1280, ge=32, le=10000)
    enhance: bool = True
    clahe_clip: float = Field(2.0, gt=0, le=20)
    clahe_grid: int = Field(8, ge=2, le=64)


class FaceConfig(StrictConfig):
    algorithm: Literal["central_texture", "grabcut"] = "central_texture"
    mode: Literal["auto", "full_image"] = "auto"
    allow_full_image_fallback: bool = True
    min_area_ratio: float = Field(0.2, gt=0, le=1)
    max_area_ratio: float = Field(0.98, gt=0, le=1)
    grabcut_iterations: int = Field(5, ge=1, le=10)
    grabcut_margin: float = Field(.05, ge=.01, le=.2)

    @model_validator(mode="after")
    def valid_range(self):
        if self.min_area_ratio >= self.max_area_ratio:
            raise ValueError("face.min_area_ratio must be less than max_area_ratio")
        return self


class LithologyConfig(StrictConfig):
    algorithm: Literal["kmeans", "slic"] = "kmeans"
    clusters: int = Field(4, ge=1, le=12)
    slic_segments: int = Field(120, ge=2, le=1000)
    slic_compactness: float = Field(10., ge=.1, le=100)
    min_region_area_ratio: float = Field(.01, ge=0, le=.25)
    min_region_pixels: int = Field(80, ge=1)
    merge_color_distance: float = Field(12., ge=0)
    spatial_weight: float = Field(.15, ge=0, le=10)
    max_regions: int = Field(80, ge=1, le=10000)
    random_seed: int = Field(0, ge=0)


class FractureConfig(StrictConfig):
    algorithm: Literal["blackhat_hessian", "blackhat", "frangi"] = "blackhat_hessian"
    clahe_clip: float = Field(2., gt=0, le=20)
    blackhat_radius: int = Field(7, ge=1, le=100)
    frangi_sigma_min: float = Field(1., ge=.5, le=8)
    frangi_sigma_max: float = Field(4., ge=.5, le=12)
    frangi_scale_count: int = Field(4, ge=1, le=8)
    threshold: float = Field(.20, gt=0, le=1)
    min_component_area: int = Field(20, ge=1)
    min_elongation: float = Field(2., ge=1)
    min_line_length: float = Field(15., gt=0)
    noise_sigma: float = Field(1., ge=0, le=10)
    line_response_percentile: float = Field(85., ge=0, le=100)
    max_width_ratio: float = Field(.25, gt=0, le=1)

    @model_validator(mode="after")
    def valid_frangi_scales(self):
        if self.algorithm == "frangi" and self.frangi_sigma_min > self.frangi_sigma_max:
            raise ValueError("fracture.frangi_sigma_min must not exceed frangi_sigma_max")
        return self


class VectorizationConfig(StrictConfig):
    min_length_px: float = Field(15., gt=0)
    simplify_tolerance_px: float = Field(1., ge=0)


class CalibrationConfig(StrictConfig):
    meters_per_pixel: float | None = Field(None, gt=0)
    planar_approximation_acknowledged: bool = False
    description: str | None = None

    @model_validator(mode="after")
    def require_assumption(self):
        if self.meters_per_pixel is not None and not self.planar_approximation_acknowledged:
            raise ValueError("Scalar calibration requires planar_approximation_acknowledged=true (original oriented pixels)")
        return self


class GeometryConfig(StrictConfig):
    """Parameters for image-observable geometry submodules.

    ``image_axis_azimuth_deg`` is optional camera/face orientation metadata.  It
    lets the image-plane direction be expressed as a horizontal projection, but
    it cannot create a geological dip measurement from a single 2-D image.
    """

    image_axis_azimuth_deg: float | None = Field(None, ge=-360, le=360)
    attitude_min_trace_length_px: float = Field(0.0, ge=0)


class PipelineConfig(StrictConfig):
    preprocessing: PreprocessConfig = Field(default_factory=PreprocessConfig)
    face: FaceConfig = Field(default_factory=FaceConfig)
    lithology: LithologyConfig = Field(default_factory=LithologyConfig)
    fracture: FractureConfig = Field(default_factory=FractureConfig)
    vectorization: VectorizationConfig = Field(default_factory=VectorizationConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    geometry: GeometryConfig = Field(default_factory=GeometryConfig)


def load_config(path: str | Path | None = None) -> PipelineConfig:
    if path is None:
        return PipelineConfig()
    with Path(path).open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return PipelineConfig.model_validate(data or {})

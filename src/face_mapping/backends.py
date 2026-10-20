"""Array contracts for replaceable recognizers; no export/geometry dependency.

predict receives uint8 RGB and a same-sized bool ROI. Labels are disjoint positive
integers, 0 is background. A fracture backend returns a same-sized bool mask.
Future VLM/foundation/specialized implementations implement this protocol and
return these objects, including provenance; they are not installed model stubs.
"""
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np


@dataclass
class SegmentationResult:
    method: str
    labels: np.ndarray | None = None
    mask: np.ndarray | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class LithologySegmentationResult(SegmentationResult):
    """Lithology backend result: labels required, 0 background, positive IDs."""

    def __post_init__(self):
        if self.labels is None or self.labels.ndim != 2 or not np.issubdtype(self.labels.dtype,np.integer):
            raise ValueError("Lithology result requires a two-dimensional integer label map")


@dataclass
class FractureSegmentationResult(SegmentationResult):
    """Fracture backend result: binary candidate mask required."""

    def __post_init__(self):
        if self.mask is None or self.mask.ndim != 2 or self.mask.dtype != bool:
            raise ValueError("Fracture result requires a two-dimensional boolean mask")


@dataclass
class FaceDetectionResult:
    mask: np.ndarray
    method: str
    status: str
    verified: bool = False
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class SegmentationBackend(Protocol):
    def predict(self, image: np.ndarray, roi_mask: np.ndarray, config: Any) -> SegmentationResult:
        """Return candidates at input resolution, restricted to roi_mask."""
        ...

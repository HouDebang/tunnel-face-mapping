"""Deterministic connected color/texture partitions; geological class unknown."""
import heapq
from typing import Any

import cv2
import numpy as np
from skimage.measure import label as connected_labels
from skimage.segmentation import slic

from .backends import LithologySegmentationResult


def _merge_regions(labels: np.ndarray, lab: np.ndarray, minimum: int, color_distance: float, maximum: int) -> tuple[np.ndarray, list[str]]:
    """Merge adjacent components, preserving connectedness and exact ROI cover."""
    count = int(labels.max())
    counts = np.bincount(labels.ravel(), minlength=count + 1).astype(np.int64)
    sums = np.stack([np.bincount(labels.ravel(), weights=lab[..., c].ravel(), minlength=count + 1) for c in range(3)], axis=1)
    adjacency: dict[int, set[int]] = {index: set() for index in range(1, count + 1)}
    parent = np.arange(count + 1)
    generation = np.zeros(count + 1, dtype=np.int32)
    active = set(adjacency)
    pairs: list[np.ndarray] = []
    for a, b in ((labels[:, :-1], labels[:, 1:]), (labels[:-1], labels[1:])):
        valid = (a > 0) & (b > 0) & (a != b)
        if np.any(valid):
            pairs.append(np.sort(np.stack([a[valid], b[valid]], axis=1), axis=1))
    if pairs:
        for a, b in np.unique(np.concatenate(pairs), axis=0):
            adjacency[int(a)].add(int(b))
            adjacency[int(b)].add(int(a))

    def distance(a: int, b: int) -> float:
        return float(np.linalg.norm(sums[a] / counts[a] - sums[b] / counts[b]))

    def merge(a: int, b: int) -> None:
        # a joins adjacent b; all original a labels are resolved through parent.
        neighbors = adjacency[a] | adjacency[b]
        neighbors.discard(a)
        neighbors.discard(b)
        for other in neighbors:
            adjacency[other].discard(a)
            adjacency[other].discard(b)
            adjacency[other].add(b)
        adjacency[b] = neighbors
        adjacency.pop(a)
        parent[a] = b
        sums[b] += sums[a]
        counts[b] += counts[a]
        active.remove(a)
        generation[b] += 1

    sizes = [(int(counts[index]), index) for index in active]
    heapq.heapify(sizes)
    while sizes:
        size, index = heapq.heappop(sizes)
        if index not in active or size != counts[index]:
            continue
        if size >= minimum:
            break
        if not adjacency[index]:
            continue  # disconnected ROI islands must retain their own IDs
        target = min(adjacency[index], key=lambda other: (distance(index, other), -counts[other], other))
        merge(index, target)
        heapq.heappush(sizes, (int(counts[target]), target))

    # Adjacent sufficiently similar mean LAB regions merge even above the
    # minimum area. Generation counters reject stale heap distances.
    edges = [(distance(a, b), a, b, int(generation[a]), int(generation[b]))
             for a in active for b in adjacency[a] if a < b]
    heapq.heapify(edges)
    while edges:
        dist, a, b, ga, gb = heapq.heappop(edges)
        if a not in active or b not in active or ga != generation[a] or gb != generation[b]:
            continue
        if dist > color_distance:
            break
        if counts[a] > counts[b]:
            a, b = b, a
        merge(a, b)
        for other in adjacency[b]:
            heapq.heappush(edges, (distance(b, other), b, other, int(generation[b]), int(generation[other])))

    sizes = [(int(counts[index]), index) for index in active]
    heapq.heapify(sizes)
    while len(active) > maximum and sizes:
        size, index = heapq.heappop(sizes)
        if index not in active or size != counts[index] or not adjacency[index]:
            continue
        target = min(adjacency[index], key=lambda other: (distance(index, other), -counts[other], other))
        merge(index, target)
        heapq.heappush(sizes, (int(counts[target]), target))

    lookup = np.zeros(count + 1, dtype=np.int32)
    roots = {old: new for new, old in enumerate(sorted(active), 1)}
    for index in range(1, count + 1):
        root = index
        while parent[root] != root:
            root = int(parent[root])
        lookup[index] = roots[root]
    output = lookup[labels]
    warnings: list[str] = []
    if len(active) > maximum:
        warnings.append("Disconnected ROI islands prevent enforcing max_regions without losing connected IDs")
    small = sum(counts[index] < minimum for index in active)
    if small:
        warnings.append(f"{small} isolated ROI regions remain below the minimum region area")
    return output, warnings


def _predict_slic(image: np.ndarray, roi_mask: np.ndarray, config: Any,
                  lab: np.ndarray) -> LithologySegmentationResult:
    """Masked SLIC superpixels followed by the same connected region merging.

    SLIC's approximate requested count is capped at the ROI pixel count. Its
    connectivity pass may leave tiny isolated ROI pixels unassigned (including
    a single-pixel ROI). Such pixels become explicitly reported connected
    candidate islands; they are never silently dropped or called lithologies.
    """
    roi_pixels = int(np.count_nonzero(roi_mask))
    effective_segments = min(int(config.slic_segments), roi_pixels)
    raw_labels = slic(
        image, n_segments=effective_segments,
        compactness=float(config.slic_compactness), mask=roi_mask,
        start_label=1, channel_axis=-1, convert2lab=True,
        enforce_connectivity=True,
    )
    raw_labels = np.where(roi_mask, raw_labels, 0).astype(np.int32)
    # A label must mean one 4-connected component, even for diagonal contacts
    # or separated ROI islands. label() splits equal-valued components in one
    # pass rather than scanning the entire image once per superpixel.
    labels = connected_labels(raw_labels, background=0, connectivity=1).astype(np.int32)
    unassigned = roi_mask & (labels == 0)
    unassigned_pixels = int(np.count_nonzero(unassigned))
    warnings: list[str] = []
    if unassigned_pixels:
        islands = connected_labels(unassigned, background=0, connectivity=1)
        labels[unassigned] = islands[unassigned] + int(labels.max())
        warnings.append(
            f"SLIC left {unassigned_pixels} ROI pixels unassigned; "
            "retained their 4-connected components as unverified candidate islands"
        )
    minimum = max(int(config.min_region_pixels),
                  round(float(config.min_region_area_ratio) * roi_pixels))
    initial_count = int(labels.max())
    labels, merge_warnings = _merge_regions(
        labels, lab, minimum, float(config.merge_color_distance), int(config.max_regions)
    )
    warnings.extend(merge_warnings)
    if not np.array_equal(labels > 0, roi_mask):
        raise RuntimeError("SLIC segmentation failed exact ROI coverage invariant")
    parameters = config.model_dump() if hasattr(config, "model_dump") else dict(vars(config))
    parameters.update({
        "feature_channels": ["SLIC Lab color", "SLIC spatial coordinates"],
        "effective_slic_segments": effective_segments,
        "slic_convert2lab": True, "slic_enforce_connectivity": True,
        "slic_sigma": 0., "slic_start_label": 1,
        "merge_color_smoothing_sigma": 1.2,
        "effective_min_region_pixels": minimum, "connectivity": 4,
        "unassigned_roi_policy": "explicit_connected_candidate_islands",
    })
    return LithologySegmentationResult(
        method="slic_lab_adjacency_v1", labels=labels,
        parameters=parameters, warnings=warnings,
        details={"category": "unknown", "semantic_classification": False,
                 "implementation": "skimage.segmentation.slic",
                 "initial_region_count": initial_count,
                 "region_count": int(labels.max()),
                 "unassigned_pixel_count": unassigned_pixels,
                 "degenerate_roi_fallback": bool(unassigned_pixels)},
    )


class LithologyBaseline:
    """Selectable k-means or SLIC image partitions, then adjacency merging."""

    def predict(self, image: np.ndarray, roi_mask: np.ndarray, config: Any) -> LithologySegmentationResult:
        if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Lithology input must be uint8 RGB")
        if roi_mask.shape != image.shape[:2] or roi_mask.dtype != bool or not np.any(roi_mask):
            raise ValueError("Lithology requires a nonempty same-sized boolean ROI")
        algorithm = getattr(config, "algorithm", "kmeans")
        if algorithm not in {"kmeans", "slic"}:
            raise ValueError(f"Unknown lithology algorithm: {algorithm}")
        height, width = roi_mask.shape
        smooth = cv2.GaussianBlur(image, (0, 0), 1.2)
        lab = cv2.cvtColor(smooth.astype(np.float32) / 255., cv2.COLOR_RGB2LAB)
        if algorithm == "slic":
            return _predict_slic(image, roi_mask, config, lab)
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32)
        mean = cv2.GaussianBlur(gray, (0, 0), 3.)
        std = np.sqrt(np.maximum(cv2.GaussianBlur(gray * gray, (0, 0), 3.) - mean * mean, 0))
        yy, xx = np.nonzero(roi_mask)
        features = np.column_stack([lab[roi_mask], std[roi_mask] * .5,
                                    xx / max(width - 1, 1) * float(config.spatial_weight) * 100.,
                                    yy / max(height - 1, 1) * float(config.spatial_weight) * 100.]).astype(np.float32)
        rng = np.random.default_rng(int(config.random_seed))
        indices = rng.choice(len(features), min(20000, len(features)), replace=False)
        sample = features[indices]
        k = min(int(config.clusters), len(sample))
        cv2.setRNGSeed(int(config.random_seed) % 2147483647)
        _, _, centers = cv2.kmeans(sample, k, None, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 60, .05), 1, cv2.KMEANS_PP_CENTERS)
        clustered = np.zeros((height, width), dtype=np.uint8)
        assigned = np.empty(len(features), dtype=np.uint8)
        for start in range(0, len(features), 25000):
            block = features[start:start + 25000]
            assigned[start:start + len(block)] = np.argmin(np.sum((block[:, None, :] - centers[None, :, :]) ** 2, axis=2), axis=1) + 1
        clustered[roi_mask] = assigned
        # Median cleanup only uses nonzero cluster decisions; ROI is reapplied.
        filtered = cv2.medianBlur(clustered, 5)
        valid_filter = roi_mask & (filtered > 0)
        clustered[valid_filter] = filtered[valid_filter]
        labels = np.zeros_like(clustered, dtype=np.int32)
        next_id = 0
        for value in range(1, k + 1):
            n, components = cv2.connectedComponents((clustered == value).astype(np.uint8), connectivity=4)
            valid = components > 0
            labels[valid] = components[valid] + next_id
            next_id += n - 1
        minimum = max(int(config.min_region_pixels), round(float(config.min_region_area_ratio) * len(features)))
        labels, warnings = _merge_regions(labels, lab, minimum, float(config.merge_color_distance), int(config.max_regions))
        if not np.array_equal(labels > 0, roi_mask):
            raise RuntimeError("Lithology segmentation failed exact ROI coverage invariant")
        parameters = config.model_dump() if hasattr(config, "model_dump") else dict(vars(config))
        parameters.update({"feature_channels": ["Lab L", "Lab a", "Lab b", "local_gray_std_x0.5", "normalized_x", "normalized_y"],
                           "color_smoothing_sigma": 1.2, "texture_sigma": 3., "median_kernel": 5,
                           "kmeans_sample_limit": 20000, "effective_min_region_pixels": minimum,
                           "connectivity": 4})
        return LithologySegmentationResult(method="lab_texture_xy_kmeans_adjacency_v1", labels=labels, parameters=parameters,
                                  warnings=warnings, details={"category": "unknown", "semantic_classification": False, "region_count": int(labels.max())})

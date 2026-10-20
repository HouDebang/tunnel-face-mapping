"""Unvalidated feature-based face ROI proposal with explicit fallback."""
from typing import Any

import cv2
import numpy as np

from .backends import FaceDetectionResult


def detect_face(image: np.ndarray, config: Any) -> FaceDetectionResult:
    """Propose a central rock-like connected image region, without semantics.

    Brightness, saturation, central LAB similarity and local variation suppress
    some empty background. Equipment, lining and people are not reliably
    distinguished; every automatic ROI remains unverified.
    """
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Face detection requires a uint8 RGB image with shape (height, width, 3)")
    height, width = image.shape[:2]
    if height == 0 or width == 0:
        raise ValueError("Face detection image dimensions must be positive")
    algorithm = getattr(config, "algorithm", "central_texture")
    methods = {"central_texture": "central_lab_texture_component_v1", "grabcut": "central_seed_grabcut_v1"}
    if algorithm not in methods:
        raise ValueError(f"Unknown face algorithm: {algorithm}")
    if config.mode not in {"auto", "full_image"}:
        raise ValueError(f"Unknown face mode: {config.mode}")
    method = methods[algorithm]
    limitation = "ROI is unverified: automatic image segmentation does not semantically exclude equipment, people, lining or shadows"
    if config.mode == "full_image":
        return FaceDetectionResult(
            mask=np.ones((height, width), dtype=bool), method="full_image_fallback",
            status="full_image_fallback", warnings=["Full-image analysis requested by configuration", limitation],
            details={"reason": "configured_full_image", "attempted_automatic_detection": False},
        )

    if algorithm == "grabcut":
        mask, details = _grabcut_candidate(image, config)
    else:
        mask, details = _central_texture_candidate(image, config)
    area_ratio = float(mask.mean())
    details.update({"attempted_automatic_detection": True, "selected_algorithm": algorithm,
                    "candidate_area_ratio": area_ratio, "semantic_validation": False})
    if mask.any() and float(config.min_area_ratio) <= area_ratio <= float(config.max_area_ratio):
        return FaceDetectionResult(mask=mask, method=method, status="heuristic_candidate", warnings=[limitation], details=details)
    reason = details.get("failure_reason") or f"Automatic ROI area ratio {area_ratio:.4f} is outside the configured range or no central component exists"
    if not config.allow_full_image_fallback:
        raise ValueError(reason + "; full-image fallback is disabled")
    details["attempted_method"] = method
    details["reason"] = reason
    return FaceDetectionResult(mask=np.ones((height, width), dtype=bool), method="full_image_fallback", status="full_image_fallback", warnings=[reason, limitation], details=details)


def _central_texture_candidate(image: np.ndarray, config: Any) -> tuple[np.ndarray, dict]:
    """The original central LAB/texture baseline, with its numerical rules intact."""
    height, width = image.shape[:2]
    ratio = min(1., 640 / max(height, width))
    sw, sh = max(1, round(width * ratio)), max(1, round(height * ratio))
    small = cv2.resize(image, (sw, sh), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small.astype(np.float32) / 255., cv2.COLOR_RGB2LAB)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    patch = lab[int(sh * .25):max(int(sh * .75), int(sh * .25) + 1), int(sw * .25):max(int(sw * .75), int(sw * .25) + 1)]
    reference = np.median(patch.reshape(-1, 3), axis=0)
    distances = np.linalg.norm((lab - reference) * np.array([.55, 1., 1.], dtype=np.float32), axis=2)
    central_distances = distances[int(sh * .25):max(int(sh * .75), int(sh * .25) + 1), int(sw * .25):max(int(sw * .75), int(sw * .25) + 1)]
    color_limit = float(np.clip(np.percentile(central_distances, 92) * 1.5, 18., 65.))
    local_mean = cv2.GaussianBlur(gray, (0, 0), 3.)
    local_variance = np.maximum(cv2.GaussianBlur(gray * gray, (0, 0), 3.) - local_mean * local_mean, 0)
    local_std = np.sqrt(local_variance)
    raw = ((gray > 12) & (gray < 246) & (hsv[..., 1] < 205) & (distances < color_limit)
           & ((local_std > 2.) | (distances < color_limit * .55)))
    size = max(3, round(min(sw, sh) * .025) | 1)
    raw = cv2.morphologyEx(raw.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    raw = cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(raw, connectivity=8)
    chosen = 0
    best_score = -1.
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        cx, cy = centroids[index]
        center_distance = ((cx / sw - .5) ** 2 + (cy / sh - .5) ** 2) ** .5
        # Components must actually intersect the central reference, not just
        # happen to have a nearby centroid.
        overlap = np.count_nonzero(labels[int(sh * .35):max(int(sh * .65), int(sh * .35) + 1), int(sw * .35):max(int(sw * .65), int(sw * .35) + 1)] == index)
        if overlap and area / (sw * sh) >= float(config.min_area_ratio) * .7:
            score = area * max(.1, 1 - center_distance)
            if score > best_score:
                best_score, chosen = score, index
    mask = cv2.resize((labels == chosen).astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST_EXACT).astype(bool) if chosen else np.zeros((height, width), bool)
    details = {
        "analysis_size": [sw, sh],
        "central_lab_reference": reference.tolist(), "lab_distance_limit": color_limit,
        "gray_range": [12, 246], "max_hsv_saturation": 205,
        "local_std_threshold": 2., "morphology_kernel": size,
    }
    return mask, details


def _grabcut_candidate(image: np.ndarray, config: Any) -> tuple[np.ndarray, dict]:
    """Graph-cut foreground proposal with explicit, unvalidated central seeds.

    Outer-margin pixels are hard background and a small central rectangle is
    hard foreground. These spatial assumptions can exclude a real face touching
    the frame or include central equipment; the result is never semantic proof.
    """
    height, width = image.shape[:2]
    ratio = min(1., 640 / max(height, width))
    sw, sh = max(1, round(width * ratio)), max(1, round(height * ratio))
    iterations = int(config.grabcut_iterations)
    margin = float(config.grabcut_margin)
    if not 1 <= iterations <= 10 or not .01 <= margin <= .2:
        raise ValueError("GrabCut requires iterations in [1, 10] and margin in [0.01, 0.2]")
    details = {
        "analysis_size": [sw, sh], "grabcut_iterations": iterations,
        "grabcut_margin": margin, "random_seed": 0,
        "initialization": "GC_INIT_WITH_MASK",
        "initialization_assumption": "outer margin is background; center rectangle is foreground; interior is probable foreground",
        "limitations": ["A face touching the frame can be excluded", "Central equipment or people can become foreground"],
    }
    empty = np.zeros((height, width), dtype=bool)
    if min(sw, sh) < 8:
        details["failure_reason"] = "GrabCut image is too small to initialize foreground/background seeds (minimum analysis dimension: 8 pixels)"
        return empty, details
    small = cv2.resize(image, (sw, sh), interpolation=cv2.INTER_AREA)
    if np.all(small == small[0, 0]):
        details["failure_reason"] = "GrabCut cannot distinguish foreground/background in a constant analysis image"
        return empty, details
    mx, my = max(1, round(sw * margin)), max(1, round(sh * margin))
    seeds = np.full((sh, sw), cv2.GC_PR_FGD, dtype=np.uint8)
    seeds[:my, :] = seeds[-my:, :] = cv2.GC_BGD
    seeds[:, :mx] = seeds[:, -mx:] = cv2.GC_BGD
    x0, x1 = int(sw * .45), max(int(sw * .55), int(sw * .45) + 1)
    y0, y1 = int(sh * .45), max(int(sh * .55), int(sh * .45) + 1)
    seeds[y0:y1, x0:x1] = cv2.GC_FGD
    details["background_margin_pixels"] = [mx, my]
    details["foreground_seed_bounds"] = [x0, y0, x1, y1]
    try:
        # GrabCut's GMM initialization uses OpenCV RNG; seed each invocation so
        # repeat runs with identical parameters yield identical candidates.
        cv2.setRNGSeed(0)
        cv2.grabCut(small, seeds, None, np.zeros((1, 65), np.float64),
                    np.zeros((1, 65), np.float64), iterations, cv2.GC_INIT_WITH_MASK)
    except cv2.error as error:
        details["failure_reason"] = "GrabCut could not initialize or optimize the seeded image"
        details["opencv_error"] = str(error)
        return empty, details
    raw = ((seeds == cv2.GC_FGD) | (seeds == cv2.GC_PR_FGD)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(raw, connectivity=8)
    candidates = [index for index in range(1, count) if np.any(labels[y0:y1, x0:x1] == index)]
    chosen = max(candidates, key=lambda index: int(stats[index, cv2.CC_STAT_AREA])) if candidates else 0
    mask = cv2.resize((labels == chosen).astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST_EXACT).astype(bool) if chosen else empty
    return mask, details

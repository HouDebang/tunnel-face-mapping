"""Local dark-line candidates with explicit morphology and shape filtering."""
from typing import Any

import cv2
import numpy as np
from skimage.filters import frangi
from skimage.morphology import skeletonize

from .backends import FractureSegmentationResult


class FractureBaseline:
    """Selectable local dark-ridge candidates; no geological semantic claim.

    All responses use the same connected-component shape filters. Frangi uses
    its own multiscale Hessian response, independent of Blackhat morphology.
    """

    def predict(self, image: np.ndarray, roi_mask: np.ndarray, config: Any) -> FractureSegmentationResult:
        if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or not image.size:
            raise ValueError("Fracture input must be uint8 RGB")
        if roi_mask.shape != image.shape[:2] or roi_mask.dtype != bool or not roi_mask.any():
            raise ValueError("Fracture detector requires a nonempty same-sized boolean ROI")
        algorithm = getattr(config, "algorithm", "blackhat_hessian")
        methods = {"blackhat_hessian": "clahe_blackhat_hessian_pca_v1",
                   "blackhat": "clahe_blackhat_pca_v1", "frangi": "clahe_frangi_pca_v1"}
        if algorithm not in methods:
            raise ValueError(f"Unsupported fracture algorithm: {algorithm}")
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        constant_input = bool(np.ptp(gray) == 0)
        enhanced = cv2.createCLAHE(clipLimit=float(config.clahe_clip), tileGridSize=(8, 8)).apply(gray)
        sigma = float(config.noise_sigma)
        smooth = cv2.GaussianBlur(enhanced, (0, 0), sigma) if sigma > 0 else enhanced
        response_parameters: dict[str, Any] = {}
        if algorithm == "frangi":
            sigmas = np.linspace(float(config.frangi_sigma_min), float(config.frangi_sigma_max),
                                 int(config.frangi_scale_count)).tolist()
            # A constant image has no ridge evidence. Do not normalize tiny
            # floating-point Hessian residuals into full-strength detections.
            response = (np.zeros(gray.shape, dtype=np.float32) if constant_input else
                        frangi(smooth.astype(np.float32) / 255., sigmas=sigmas,
                               alpha=.5, beta=.5, gamma=None, black_ridges=True, mode="reflect"))
            response = np.nan_to_num(response, nan=0., posinf=0., neginf=0.)
            scale = max(1.e-6, float(np.percentile(response[roi_mask], 99.5)))
            support = response > 1.e-8
            response_parameters.update({"frangi_sigmas": sigmas, "frangi_alpha": .5, "frangi_beta": .5,
                                        "frangi_gamma": None, "black_ridges": True, "boundary_mode": "reflect",
                                        "response_source": "skimage.filters.frangi", "minimum_raw_response": 1.e-8,
                                        "normalization_floor": 1.e-6})
        else:
            radius = int(config.blackhat_radius)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
            blackhat = cv2.morphologyEx(smooth, cv2.MORPH_BLACKHAT, kernel).astype(np.float32)
            response = blackhat
            if algorithm == "blackhat_hessian":
                # A dark ridge has positive second derivative across the ridge
                # and a smaller derivative along it. The floor keeps junctions.
                field = cv2.GaussianBlur(smooth.astype(np.float32), (0, 0), 1.)
                dxx = cv2.Sobel(field, cv2.CV_32F, 2, 0, ksize=3)
                dyy = cv2.Sobel(field, cv2.CV_32F, 0, 2, ksize=3)
                dxy = cv2.Sobel(field, cv2.CV_32F, 1, 1, ksize=3)
                discriminant = np.sqrt(np.maximum((dxx - dyy) ** 2 + 4 * dxy ** 2, 0))
                high = .5 * (dxx + dyy + discriminant)
                low = .5 * (dxx + dyy - discriminant)
                coherence = np.clip(1 - np.abs(low) / (np.abs(high) + 1.e-6), 0, 1)
                ridge_weight = np.where(high > 0, .35 + .65 * coherence, .15)
                response = blackhat * ridge_weight
                response_parameters.update({"hessian_sigma": 1., "hessian_weight_floor": .35})
            if constant_input:
                response = np.zeros(gray.shape, dtype=np.float32)
            scale = max(10., float(np.percentile(response[roi_mask], 99.5)))
            support = blackhat >= 3
            response_parameters.update({"minimum_blackhat_contrast": 3, "normalization_floor": 10.,
                                        "response_source": "opencv_blackhat" + ("_hessian" if algorithm == "blackhat_hessian" else "")})
        response = np.clip(response / scale, 0, 1)
        response[~roi_mask] = 0
        positive = response[roi_mask & support]
        effective_threshold = float(config.threshold)
        if positive.size:
            effective_threshold = max(effective_threshold, float(np.percentile(positive, float(config.line_response_percentile))))
        candidate = (response >= effective_threshold) & support & roi_mask
        # Do not join nearby disjoint structures: close can bridge real gaps
        # and create topology not directly supported by observed dark pixels.
        count, components, stats, _ = cv2.connectedComponentsWithStats(candidate.astype(np.uint8), connectivity=8)
        output = np.zeros(roi_mask.shape, dtype=bool)
        rejected = {"small_component": 0, "short_component": 0, "not_elongated": 0, "too_wide": 0}
        kept = 0
        for index in range(1, count):
            area = int(stats[index, cv2.CC_STAT_AREA])
            if area < int(config.min_component_area):
                rejected["small_component"] += 1
                continue
            left, top, box_width, box_height = (int(stats[index, entry]) for entry in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
            crop = components[top:top + box_height, left:left + box_width] == index
            yy, xx = np.nonzero(crop)
            points = np.column_stack([xx, yy]).astype(np.float64)
            centered = points - points.mean(axis=0)
            covariance = centered.T @ centered / max(area, 1)
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
            major = eigenvectors[:, -1]
            projected = centered @ major
            extent = float(np.ptp(projected) + 1.)
            elongation = float(np.sqrt((eigenvalues[-1] + 1. / 12.) / (eigenvalues[0] + 1. / 12.)))
            width_ratio = (area / extent) / extent
            # Crossed/joining narrow lines can have isotropic PCA despite being
            # a line network. Compare skeleton extent with its actual width as
            # an additional shape test; a solid blob has a short thick skeleton.
            skeleton = skeletonize(crop)
            skeleton_pixels = int(skeleton.sum())
            distances = cv2.distanceTransform(np.pad(crop.astype(np.uint8), 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
            median_width = max(1., float(np.median(distances[skeleton]) * 2)) if skeleton_pixels else float("inf")
            network_elongation = skeleton_pixels / median_width
            if extent < float(config.min_line_length):
                rejected["short_component"] += 1
            elif max(elongation, network_elongation) < float(config.min_elongation):
                rejected["not_elongated"] += 1
            elif width_ratio > float(config.max_width_ratio):
                rejected["too_wide"] += 1
            else:
                output[top:top + box_height, left:left + box_width] |= crop
                kept += 1
        parameters = config.model_dump() if hasattr(config, "model_dump") else dict(vars(config))
        parameters.update({"algorithm": algorithm, "effective_response_threshold": effective_threshold,
                           "response_scale_p995": scale, "clahe_grid": 8, "connectivity": 8,
                           "parameter_units": "working_image_pixels", **response_parameters})
        parameters["elongation_rule"] = "max(PCA axis ratio, skeleton pixel count / median skeleton width)"
        warnings = []
        if not output.any():
            warnings.append("No dark-line components passed the configured candidate filters; this does not prove absence of fractures")
        return FractureSegmentationResult(method=methods[algorithm], mask=output, parameters=parameters, warnings=warnings,
                                  details={"candidate_components_before_filter": count - 1, "kept_components": kept,
                                           "rejected_components": rejected, "semantic_validation": False,
                                           "result_kind": "candidate_fracture_mask", "constant_input": constant_input,
                                           "response_nonzero_pixels_in_roi": int(np.count_nonzero(response[roi_mask])),
                                           "interference": "Shadows, drilling marks, surface texture and equipment may remain; broad or curved fractures may be missed"})

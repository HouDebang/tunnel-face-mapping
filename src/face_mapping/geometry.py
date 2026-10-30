"""Geometry in the EXIF-oriented original image canvas.

Coordinates use x right and y down; pixel centers are (column+.5,row+.5).
The mask, rather than a simplified polygon, is authoritative for area.
"""
from __future__ import annotations

import math
from collections import defaultdict

import cv2
import numpy as np


def _points_array(points) -> np.ndarray:
    array = np.asarray(points, dtype=np.float64)
    if array.size == 0:
        return np.empty((0, 2), dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 2 or not np.isfinite(array).all():
        raise ValueError("Polyline points must be a finite N x 2 coordinate array")
    return array


def polyline_length(points) -> float:
    """Sum consecutive Euclidean distances; include a cycle's closing point."""
    array = _points_array(points)
    return float(np.linalg.norm(np.diff(array, axis=0), axis=1).sum())


def axial_direction_deg(points) -> float | None:
    """Endpoint axial angle in [0,180), clockwise from the image +x axis."""
    array = _points_array(points)
    if len(array) < 2:
        return None
    delta = array[-1] - array[0]
    if np.linalg.norm(delta) <= 1e-12:
        return None
    return float(math.degrees(math.atan2(delta[1], delta[0])) % 180.0)


def principal_direction_deg(points) -> float | None:
    """PCA axial direction; return null when no unique principal axis exists."""
    array = _points_array(points)
    if len(array) > 1 and np.array_equal(array[0], array[-1]):
        array = array[:-1]
    if len(array) < 2:
        return None
    centered = array - array.mean(axis=0)
    eigenvalues, eigenvectors = np.linalg.eigh(centered.T @ centered)
    if eigenvalues[-1] <= 1e-12 or np.isclose(
        eigenvalues[-1], eigenvalues[0], rtol=1e-8, atol=1e-12
    ):
        return None
    axis = eigenvectors[:, -1]
    return float(math.degrees(math.atan2(axis[1], axis[0])) % 180.0)


def _trace_points(points) -> np.ndarray:
    """Return a validated trace array without changing the source points."""
    array = _points_array(points)
    if len(array) > 1 and np.array_equal(array[0], array[-1]):
        # A repeated closing point must not create a spurious turn at the seam.
        array = array[:-1]
    return array


def calculate_trace_roughness(points, image=None, *, meters_per_pixel: float | None = None,
                              sample_step_px: float = 1.0) -> dict:
    """Estimate image-observable roughness for one fracture trace.

    The shape term is deliberately dimensionless: path tortuosity (path/chord - 1)
    and the RMS change in segment direction.  When an RGB/gray image is supplied,
    a texture term is reported from the intensity gradient sampled along the trace.
    These are candidate image metrics; they are not a JRC measurement unless a
    field calibration is supplied externally.
    """
    array = _trace_points(points)
    if not math.isfinite(sample_step_px) or sample_step_px <= 0:
        raise ValueError("sample_step_px must be a finite positive number")
    if meters_per_pixel is not None and (
        not math.isfinite(meters_per_pixel) or meters_per_pixel <= 0
    ):
        raise ValueError("meters_per_pixel must be a finite positive number")
    if len(array) < 2:
        return {
            "status": "insufficient_trace",
            "roughness_index": None,
            "tortuosity": None,
            "angular_variability_deg": None,
            "texture_gradient_rms": None,
            "length_px": polyline_length(points),
            "length_m": None if meters_per_pixel is None else polyline_length(points) * meters_per_pixel,
            "method": "trace_tortuosity_texture_v1",
        }
    deltas = np.diff(array, axis=0)
    segment_lengths = np.linalg.norm(deltas, axis=1)
    valid = segment_lengths > 1e-12
    deltas, segment_lengths = deltas[valid], segment_lengths[valid]
    length = float(segment_lengths.sum())
    chord = float(np.linalg.norm(array[-1] - array[0]))
    tortuosity = None if chord <= 1e-12 else max(0.0, length / chord - 1.0)
    angles = np.unwrap(np.arctan2(deltas[:, 1], deltas[:, 0]))
    turns = np.diff(angles)
    # Axial traces have no preferred direction; wrap each turn to [-pi/2, pi/2].
    turns = (turns + math.pi / 2) % math.pi - math.pi / 2
    angular = float(np.sqrt(np.average(turns ** 2, weights=segment_lengths[1:]))) if len(turns) else 0.0
    image_gradient = None
    if image is not None:
        pixels = np.asarray(image)
        if pixels.ndim == 3:
            if pixels.shape[2] not in (3, 4):
                raise ValueError("image must be grayscale or RGB(A)")
            pixels = pixels[..., :3].astype(np.float64).mean(axis=2)
        elif pixels.ndim == 2:
            pixels = pixels.astype(np.float64)
        else:
            raise ValueError("image must be a two-dimensional grayscale or RGB(A) array")
        if pixels.size and not np.isfinite(pixels).all():
            raise ValueError("image must contain finite values")
        # Resample the intensity along the trace so texture statistics do not
        # depend on how densely the skeleton happened to emit vertices.
        samples = [array[0]]
        segment_starts = array[:-1][valid]
        segment_ends = array[1:][valid]
        for start, end, segment_length in zip(segment_starts, segment_ends, segment_lengths):
            count = max(1, int(math.ceil(segment_length / sample_step_px)))
            samples.extend(start + (end - start) * (index / count)
                           for index in range(1, count + 1))
        sampled = np.asarray(samples, dtype=np.float64)
        yy = np.clip(np.rint(sampled[:, 1] - .5).astype(int), 0, pixels.shape[0] - 1)
        xx = np.clip(np.rint(sampled[:, 0] - .5).astype(int), 0, pixels.shape[1] - 1)
        values = pixels[yy, xx]
        if len(values) > 1:
            image_gradient = float(np.sqrt(np.mean(np.diff(values) ** 2)))
    # Shape and texture are kept separate so a downstream calibration can replace
    # this simple composite without losing the auditable primitive metrics.
    shape_term = float(tortuosity or 0.0) + angular / math.pi
    roughness = shape_term if image_gradient is None else shape_term + image_gradient / 255.0
    return {
        "status": "candidate_image_metric",
        "roughness_index": roughness,
        "tortuosity": tortuosity,
        "angular_variability_deg": math.degrees(angular),
        "texture_gradient_rms": image_gradient,
        "length_px": length,
        "length_m": None if meters_per_pixel is None else length * meters_per_pixel,
        "method": "trace_tortuosity_texture_v1",
        "calibration": "unvalidated_shape_plus_normalized_texture" if image is not None else "unvalidated_shape_only",
    }


def calculate_roughness(traces: list[dict], image=None, *, meters_per_pixel: float | None = None) -> dict:
    """Calculate per-trace and length-weighted roughness candidates."""
    records = []
    for index, trace in enumerate(traces):
        record = calculate_trace_roughness(trace["points"], image,
                                           meters_per_pixel=meters_per_pixel)
        record["trace_id"] = trace.get("id", str(index))
        records.append(record)
    valid = [record for record in records
             if record["roughness_index"] is not None and record["length_px"] > 0]
    weights = np.asarray([record["length_px"] for record in valid], dtype=float)
    values = np.asarray([record["roughness_index"] for record in valid], dtype=float)
    mean = None if not len(values) or not weights.sum() else float(np.average(values, weights=weights))
    return {
        "status": "candidate_image_metric" if records else "no_traces",
        "method": "trace_tortuosity_texture_v1",
        "trace_count": len(records),
        "mean_roughness_index": mean,
        "per_trace": records,
        "units": {"roughness_index": "dimensionless", "angular_variability_deg": "degree",
                  "texture_gradient_rms": "intensity_level", "length_px": "px"},
    }


def calculate_trace_aperture(points, fracture_mask=None, *, meters_per_pixel: float | None = None) -> dict:
    """Estimate apparent opening from a binary fracture mask.

    The mask is treated as the visible fracture band.  Twice the Euclidean distance
    from each trace centreline pixel to the mask boundary gives a local full width.
    This is a pixel-resolution apparent opening, not a physical crack gauge result.
    """
    if meters_per_pixel is not None and (
        not math.isfinite(meters_per_pixel) or meters_per_pixel <= 0
    ):
        raise ValueError("meters_per_pixel must be a finite positive number")
    if fracture_mask is None:
        return {"status": "unavailable", "aperture_mean_px": None, "aperture_max_px": None,
                "aperture_mean_m": None, "sample_count": 0,
                "method": "distance_transform_visible_band_v1"}
    mask = np.asarray(fracture_mask, dtype=bool)
    if mask.ndim != 2:
        raise ValueError("fracture_mask must be two-dimensional")
    if not mask.any():
        return {"status": "empty_mask", "aperture_mean_px": None, "aperture_max_px": None,
                "aperture_mean_m": None, "sample_count": 0,
                "method": "distance_transform_visible_band_v1"}
    distances = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    array = _points_array(points)
    if len(array) == 0:
        return {"status": "insufficient_trace", "aperture_mean_px": None, "aperture_max_px": None,
                "aperture_mean_m": None, "sample_count": 0,
                "method": "distance_transform_visible_band_v1"}
    yy = np.rint(array[:, 1] - .5).astype(int)
    xx = np.rint(array[:, 0] - .5).astype(int)
    inside = (yy >= 0) & (yy < mask.shape[0]) & (xx >= 0) & (xx < mask.shape[1])
    values = distances[yy[inside], xx[inside]] * 2.0
    values = values[values > 0]
    if not len(values):
        return {"status": "no_mask_overlap", "aperture_mean_px": None, "aperture_max_px": None,
                "aperture_mean_m": None, "sample_count": 0,
                "method": "distance_transform_visible_band_v1"}
    mean = float(values.mean())
    maximum = float(values.max())
    return {
        "status": "candidate_apparent_opening",
        "aperture_mean_px": mean,
        "aperture_max_px": maximum,
        "aperture_mean_m": None if meters_per_pixel is None else mean * meters_per_pixel,
        "aperture_max_m": None if meters_per_pixel is None else maximum * meters_per_pixel,
        "sample_count": int(len(values)),
        "method": "distance_transform_visible_band_v1",
        "calibration": "pixel_band_width_not_field_aperture",
    }


def calculate_aperture(traces: list[dict], fracture_mask=None, *, meters_per_pixel: float | None = None) -> dict:
    """Calculate apparent opening for every trace and summarize it."""
    records = []
    for index, trace in enumerate(traces):
        record = calculate_trace_aperture(trace["points"], fracture_mask,
                                          meters_per_pixel=meters_per_pixel)
        record["trace_id"] = trace.get("id", str(index))
        records.append(record)
    valid = [record for record in records if record["aperture_mean_px"] is not None]
    widths = np.asarray([record["aperture_mean_px"] for record in valid], dtype=float)
    return {
        "status": "candidate_apparent_opening" if valid else ("unavailable" if fracture_mask is None else "no_valid_samples"),
        "method": "distance_transform_visible_band_v1",
        "trace_count": len(records),
        "mean_aperture_px": None if not len(widths) else float(widths.mean()),
        "max_aperture_px": None if not len(widths) else float(widths.max()),
        "mean_aperture_m": None if not len(widths) or meters_per_pixel is None else float(widths.mean() * meters_per_pixel),
        "per_trace": records,
        "units": {"aperture_mean_px": "px", "aperture_max_px": "px", "aperture_mean_m": "m"},
    }


def calculate_layer_attitude(traces: list[dict], *, image_axis_azimuth_deg: float | None = None,
                             min_trace_length_px: float = 0.0) -> dict:
    """Estimate a dominant axial trace direction as a layer-attitude candidate.

    A single face image does not determine geological strike and dip.  When the
    camera/face azimuth is known, ``strike_azimuth_deg`` is provided as a horizontal
    projection; ``dip_deg`` remains null unless an external orientation measurement
    is supplied.  The result therefore stays explicitly a candidate observation.
    """
    if image_axis_azimuth_deg is not None and not math.isfinite(image_axis_azimuth_deg):
        raise ValueError("image_axis_azimuth_deg must be finite")
    if not math.isfinite(min_trace_length_px) or min_trace_length_px < 0:
        raise ValueError("min_trace_length_px must be finite and nonnegative")
    angles, weights = [], []
    for trace in traces:
        length = float(trace.get("length_px") or polyline_length(trace["points"]))
        if length < min_trace_length_px:
            continue
        angle = trace.get("principal_direction_deg")
        if angle is None:
            angle = principal_direction_deg(trace["points"])
        if angle is None:
            angle = axial_direction_deg(trace["points"])
        if angle is not None and length > 0:
            angles.append(math.radians(float(angle)))
            weights.append(length)
    if not angles:
        return {"status": "insufficient_orientation_evidence", "image_strike_deg": None,
                "strike_azimuth_deg": None, "dip_deg": None, "confidence": None,
                "dispersion_deg": None, "trace_count": 0,
                "evidence_source": "candidate_trace_orientation; bedding confirmation required",
                "method": "weighted_axial_orientation_v1"}
    doubled = 2.0 * np.asarray(angles)
    weights_array = np.asarray(weights, dtype=float)
    resultant = np.sum(weights_array * np.exp(1j * doubled)) / np.sum(weights_array)
    mean = (math.degrees(.5 * math.atan2(resultant.imag, resultant.real)) % 180.0)
    confidence = float(abs(resultant))
    dispersion = float(math.degrees(.5 * math.sqrt(max(0.0, -2.0 * math.log(max(confidence, 1e-12))))))
    strike = None if image_axis_azimuth_deg is None else (image_axis_azimuth_deg + mean) % 180.0
    return {
        "status": "candidate_image_plane_orientation" if image_axis_azimuth_deg is None else "candidate_horizontal_projection",
        "evidence_source": "candidate_trace_orientation; bedding confirmation required",
        "image_strike_deg": mean,
        "strike_azimuth_deg": strike,
        "dip_deg": None,
        "confidence": confidence,
        "dispersion_deg": dispersion,
        "trace_count": len(angles),
        "method": "weighted_axial_orientation_v1",
        "convention": "image +x clockwise; axial orientation in [0,180); dip requires external 3D orientation",
    }


def _signed_area(ring: list[tuple[int, int]]) -> float:
    points = np.asarray(ring, dtype=np.float64)
    return float(
        (points[:-1, 0] * points[1:, 1] - points[1:, 0] * points[:-1, 1]).sum()
        / 2.0
    )


def _compress_collinear(ring: list[tuple[int, int]]) -> list[list[float]]:
    points = ring[:-1]
    compressed = []
    for index, point in enumerate(points):
        previous, following = points[index - 1], points[(index + 1) % len(points)]
        a = (point[0] - previous[0], point[1] - previous[1])
        b = (following[0] - point[0], following[1] - point[1])
        if a[0] * b[1] - a[1] * b[0] != 0:
            compressed.append([float(point[0]), float(point[1])])
    return compressed + [compressed[0].copy()]


def mask_to_polygons(mask, simplify_tolerance: float = 0) -> list[dict]:
    """Polygonize exact pixel-cell edges, preserving holes and multipart masks.

    Default rings follow cell boundaries in [0,W] x [0,H] with repeated closing
    coordinates. Diagonally touching foreground cells remain separate polygons.
    A positive display tolerance simplifies rings; areas must still use masks.
    """
    binary = np.asarray(mask, dtype=bool)
    if binary.ndim != 2:
        raise ValueError("Polygon mask must be two-dimensional")
    if not math.isfinite(simplify_tolerance) or simplify_tolerance < 0:
        raise ValueError("Polygon simplification tolerance must be finite and nonnegative")
    if not binary.any():
        return []
    padded = np.pad(binary, 1)
    # Directed edges have foreground on the right. In y-down coordinates,
    # exterior rings then have positive signed area and holes negative area.
    exposed = (
        binary & ~padded[:-2, 1:-1],
        binary & ~padded[1:-1, 2:],
        binary & ~padded[2:, 1:-1],
        binary & ~padded[1:-1, :-2],
    )
    successors: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for side, boundary in enumerate(exposed):
        for y, x in zip(*np.nonzero(boundary)):
            x, y = int(x), int(y)
            if side == 0:
                start, end = (x, y), (x + 1, y)
            elif side == 1:
                start, end = (x + 1, y), (x + 1, y + 1)
            elif side == 2:
                start, end = (x + 1, y + 1), (x, y + 1)
            else:
                start, end = (x, y + 1), (x, y)
            successors[start].append(end)
    directions = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
    turn_priority = {1: 0, 0: 1, 3: 2, 2: 3}
    rings = []
    while successors:
        start = next(iter(successors))
        current = start
        previous = None
        ring = [start]
        while True:
            options = successors.get(current)
            if not options:
                raise RuntimeError("Raster polygon boundary is unexpectedly open")
            if previous is None or len(options) == 1:
                following = options[0]
            else:
                incoming = directions[(current[0] - previous[0], current[1] - previous[1])]
                following = min(options, key=lambda point: turn_priority[(
                    directions[(point[0] - current[0], point[1] - current[1])] - incoming
                ) % 4])
            options.remove(following)
            if not options:
                del successors[current]
            ring.append(following)
            previous, current = current, following
            if current == start:
                break
        rings.append((ring, _signed_area(ring)))
    exteriors = [(ring, area) for ring, area in rings if area > 0]
    polygons = [{"exterior": _compress_collinear(ring), "holes": []} for ring, _ in exteriors]
    for ring, area in rings:
        if area >= 0:
            continue
        # Move the probe into the hole (left of its first directed edge).
        first, second = np.asarray(ring[0], float), np.asarray(ring[1], float)
        delta = second - first
        probe = (first + second) / 2 + .25 * np.array([delta[1], -delta[0]])
        containing = [index for index, (outer, _) in enumerate(exteriors) if cv2.pointPolygonTest(
            np.asarray(outer, dtype=np.float32), tuple(probe), False
        ) >= 0]
        if not containing:
            raise RuntimeError("Raster polygon hole has no enclosing exterior")
        owner = min(containing, key=lambda index: exteriors[index][1])
        polygons[owner]["holes"].append(_compress_collinear(ring))
    if simplify_tolerance:
        for polygon in polygons:
            for key in ("exterior", "holes"):
                targets = [polygon[key]] if key == "exterior" else polygon[key]
                for index, ring in enumerate(targets):
                    approximate = cv2.approxPolyDP(
                        np.asarray(ring[:-1], np.float32), simplify_tolerance, True
                    ).reshape(-1, 2).astype(float).tolist()
                    if len(approximate) >= 3:
                        targets[index] = approximate + [approximate[0].copy()]
                if key == "exterior":
                    polygon[key] = targets[0]
    return polygons


def calculate_statistics(
    traces: list[dict], face_mask, region_labels, meters_per_pixel: float | None = None,
    face_verified: bool = False, image=None, fracture_mask=None,
    image_axis_azimuth_deg: float | None = None,
    attitude_min_trace_length_px: float = 0.0,
) -> dict:
    """Annotate full traces and calculate candidate totals on exact ROI pixels.

    ``verified_roi`` qualifies only the observation boundary. It never asserts
    that image detections are verified fractures or identified lithologies.
    """
    roi = np.asarray(face_mask, dtype=bool)
    if roi.ndim != 2:
        raise ValueError("Observation mask must be two-dimensional")
    if meters_per_pixel is not None and (
        not math.isfinite(meters_per_pixel) or meters_per_pixel <= 0
    ):
        raise ValueError("meters_per_pixel must be a finite positive number")
    if region_labels is None:
        region_count = 0
    else:
        labels = np.asarray(region_labels)
        if labels.shape != roi.shape:
            raise ValueError("Region labels and observation mask must have the same shape")
        if np.any(labels[~roi] != 0):
            raise ValueError("Region labels extend outside the observation mask")
        region_count = int(np.count_nonzero(np.unique(labels[roi]) > 0))
    lengths = []
    for trace in traces:
        length = polyline_length(trace["points"])
        trace["length_px"] = length
        trace["length_m"] = None if meters_per_pixel is None else length * meters_per_pixel
        trace["endpoint_direction_deg"] = axial_direction_deg(trace["points"])
        trace["principal_direction_deg"] = principal_direction_deg(trace["points"])
        lengths.append(length)
    total = float(math.fsum(lengths))
    area = int(np.count_nonzero(roi))
    physical_area = None if meters_per_pixel is None else area * meters_per_pixel**2
    physical_length = None if meters_per_pixel is None else total * meters_per_pixel
    submodules = {
        "roughness": calculate_roughness(traces, image, meters_per_pixel=meters_per_pixel),
        "aperture": calculate_aperture(traces, fracture_mask, meters_per_pixel=meters_per_pixel),
        "layer_attitude": calculate_layer_attitude(
            traces, image_axis_azimuth_deg=image_axis_azimuth_deg,
            min_trace_length_px=attitude_min_trace_length_px,
        ),
    }
    # Keep the per-trace metrics beside the existing trace geometry so they are
    # available to JSON consumers and the object table without reparsing the
    # nested module summaries.
    for index, trace in enumerate(traces):
        rough = submodules["roughness"]["per_trace"][index]
        opening = submodules["aperture"]["per_trace"][index]
        trace.update(
            roughness_index=rough.get("roughness_index"),
            roughness_status=rough.get("status"),
            tortuosity=rough.get("tortuosity"),
            angular_variability_deg=rough.get("angular_variability_deg"),
            texture_gradient_rms=rough.get("texture_gradient_rms"),
            aperture_mean_px=opening.get("aperture_mean_px"),
            aperture_max_px=opening.get("aperture_max_px"),
            aperture_mean_m=opening.get("aperture_mean_m"),
            aperture_max_m=opening.get("aperture_max_m"),
            aperture_sample_count=opening.get("sample_count"),
            aperture_status=opening.get("status"),
        )
    attitude = submodules["layer_attitude"]
    roughness = submodules["roughness"]
    aperture = submodules["aperture"]
    return {
        "fracture_count": len(traces),
        "lithology_region_count": region_count,
        "total_length_px": total,
        "observation_area_px2": area,
        "p21_per_px": total / area if area else None,
        "total_length_m": physical_length,
        "observation_area_m2": physical_area,
        "p21_per_m": physical_length / physical_area if physical_area else None,
        "mean_roughness_index": roughness.get("mean_roughness_index"),
        "mean_aperture_px": aperture.get("mean_aperture_px"),
        "max_aperture_px": aperture.get("max_aperture_px"),
        "mean_aperture_m": aperture.get("mean_aperture_m"),
        "max_aperture_m": aperture.get("max_aperture_m"),
        "image_strike_deg": attitude.get("image_strike_deg"),
        "strike_azimuth_deg": attitude.get("strike_azimuth_deg"),
        "dip_deg": attitude.get("dip_deg"),
        "attitude_confidence": attitude.get("confidence"),
        "statistics_status": "verified_roi" if face_verified else "candidate",
        "details": {
            "detection_status": "candidate_fracture_traces",
            "roi_verified": bool(face_verified),
            "area_basis": "exact_count_of_original_mask_pixels",
            "length_basis": "complete_unsimplified_pixel_center_polylines",
            "direction_convention": "axial degrees [0,180); clockwise from image +x; not geological strike or dip",
            "units": {"length": "px", "area": "px^2", "density": "px^-1"},
            "submodules": submodules,
        },
    }

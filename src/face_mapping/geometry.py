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
    face_verified: bool = False,
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
    return {
        "fracture_count": len(traces),
        "lithology_region_count": region_count,
        "total_length_px": total,
        "observation_area_px2": area,
        "p21_per_px": total / area if area else None,
        "total_length_m": physical_length,
        "observation_area_m2": physical_area,
        "p21_per_m": physical_length / physical_area if physical_area else None,
        "statistics_status": "verified_roi" if face_verified else "candidate",
        "details": {
            "detection_status": "candidate_fracture_traces",
            "roi_verified": bool(face_verified),
            "area_basis": "exact_count_of_original_mask_pixels",
            "length_basis": "complete_unsimplified_pixel_center_polylines",
            "direction_convention": "axial degrees [0,180); clockwise from image +x; not geological strike or dip",
            "units": {"length": "px", "area": "px^2", "density": "px^-1"},
        },
    }

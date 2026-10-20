"""Skeleton pixel graph tracing with exhaustive undirected edge accounting."""
from __future__ import annotations

import math

import cv2
import numpy as np
from skimage.morphology import skeletonize

from .geometry import polyline_length

SOURCE = "skimage_skeleton_pixel_graph_v1"
BRANCH_RULE = (
    "Split maximal paths at every graph pixel of degree != 2; retain pure cycles with "
    "a repeated closing point. Use 8-neighbor edges, omitting a diagonal when either "
    "orthogonal intermediate pixel exists. Each undirected edge is visited once; "
    "paths shorter than min_length are discarded after tracing. Adjacent junction "
    "pixels are separate nodes; no unmeasured junction-cluster contraction."
)


def vectorize_fractures(mask, min_length: float = 15.0, simplify_tolerance: float = 1.0) -> dict:
    """Return skeleton, raw/simplified traces, and graph accounting in original pixels.

    Trace length uses raw pixel-center coordinates, never the display geometry.
    Branch segments represent graph paths, rather than inferred geological joints.
    """
    binary = np.asarray(mask, dtype=bool)
    if binary.ndim != 2:
        raise ValueError("Fracture mask must be two-dimensional")
    if not math.isfinite(min_length) or min_length < 0:
        raise ValueError("Minimum trace length must be finite and nonnegative")
    if not math.isfinite(simplify_tolerance) or simplify_tolerance < 0:
        raise ValueError("Trace simplification tolerance must be finite and nonnegative")
    skeleton = skeletonize(binary)
    height, width = skeleton.shape
    pixels = [(int(y), int(x)) for y, x in zip(*np.nonzero(skeleton))]
    adjacent = {pixel: set() for pixel in pixels}
    for y, x in pixels:
        for dy, dx in ((0, 1), (1, -1), (1, 0), (1, 1)):
            other = (y + dy, x + dx)
            if other not in adjacent:
                continue
            if dx and dy and (skeleton[y, x + dx] or skeleton[y + dy, x]):
                continue
            adjacent[(y, x)].add(other)
            adjacent[other].add((y, x))
    visited = set()
    traces = []
    retained_edges = 0
    discarded_edges = 0
    path_count = 0

    def edge(a, b):
        return (a, b) if a < b else (b, a)

    def node(pixel, cycle=False):
        degree = len(adjacent[pixel])
        kind = "cycle" if cycle else "isolated" if degree == 0 else "endpoint" if degree == 1 else "junction"
        return {"kind": kind, "point": [pixel[1] + .5, pixel[0] + .5], "degree": degree}

    def record(path, pure_cycle=False):
        nonlocal retained_edges, discarded_edges, path_count
        path_count += 1
        points = [[float(x) + .5, float(y) + .5] for y, x in path]
        edge_total = len(path) - 1
        if polyline_length(points) < min_length:
            discarded_edges += edge_total
            return
        retained_edges += edge_total
        closed = len(path) > 1 and path[0] == path[-1]
        display = points
        if simplify_tolerance and len(points) > 2:
            approximate = cv2.approxPolyDP(
                np.asarray(points, np.float32), simplify_tolerance, closed
            ).reshape(-1, 2).astype(float).tolist()
            if closed and approximate and approximate[-1] != approximate[0]:
                approximate.append(approximate[0].copy())
            display = approximate
        traces.append({
            "id": f"F{len(traces) + 1:03d}", "points": points,
            "display_points": display, "start_node": node(path[0], pure_cycle),
            "end_node": node(path[-1], pure_cycle), "is_closed": closed, "source": SOURCE,
        })

    def walk(start, following):
        path = [start, following]
        visited.add(edge(start, following))
        previous, current = start, following
        while len(adjacent[current]) == 2 and current != start:
            candidate = next(pixel for pixel in adjacent[current] if pixel != previous)
            next_edge = edge(current, candidate)
            if next_edge in visited:
                break
            visited.add(next_edge)
            path.append(candidate)
            previous, current = current, candidate
        return path

    # Anchors include every branch/end pixel. Deterministic ordering fixes IDs.
    for pixel in pixels:
        if len(adjacent[pixel]) == 0:
            record([pixel])
        elif len(adjacent[pixel]) != 2:
            for other in sorted(adjacent[pixel]):
                if edge(pixel, other) not in visited:
                    record(walk(pixel, other))
    # Any remaining component consists solely of degree-two cycle pixels.
    for pixel in pixels:
        for other in sorted(adjacent[pixel]):
            if edge(pixel, other) not in visited:
                record(walk(pixel, other), pure_cycle=True)
    edge_count = sum(map(len, adjacent.values())) // 2
    if len(visited) != edge_count or retained_edges + discarded_edges != edge_count:
        raise RuntimeError("Skeleton graph edge accounting failed")
    return {
        "skeleton": skeleton,
        "traces": traces,
        "graph": {
            "edge_count": edge_count, "visited_edge_count": len(visited),
            "retained_edge_count": retained_edges, "discarded_edge_count": discarded_edges,
            "node_count": len(pixels),
            "endpoint_count": sum(len(value) == 1 for value in adjacent.values()),
            "junction_pixel_count": sum(len(value) > 2 for value in adjacent.values()),
            "isolated_pixel_count": sum(len(value) == 0 for value in adjacent.values()),
            "unfiltered_path_count": path_count,
            "min_length_px": float(min_length), "display_simplification_tolerance_px": float(simplify_tolerance),
            "coordinate_convention": "pixel centers (column+0.5,row+0.5); x right; y down",
            "branch_rule": BRANCH_RULE,
        },
    }

"""Known geometry and graph accounting checks, independent of image accuracy."""
import math

import numpy as np
import pytest

from face_mapping.geometry import (
    axial_direction_deg,
    calculate_statistics,
    mask_to_polygons,
    polyline_length,
    principal_direction_deg,
)
from face_mapping.vectorization import vectorize_fractures


@pytest.mark.parametrize("points,length,direction", [
    ([[.5, .5], [10.5, .5]], 10, 0),
    ([[.5, .5], [.5, 10.5]], 10, 90),
    ([[.5, .5], [10.5, 10.5]], math.sqrt(200), 45),
    ([[0, 0], [3, 0], [3, 4]], 7, math.degrees(math.atan2(4, 3))),
])
def test_known_polylines(points, length, direction):
    assert polyline_length(points) == pytest.approx(length)
    assert axial_direction_deg(points) == pytest.approx(direction)
    assert axial_direction_deg(points[::-1]) == pytest.approx(direction)


def test_direction_degeneracy_and_pca():
    assert axial_direction_deg([[1, 1], [2, 3], [1, 1]]) is None
    assert principal_direction_deg([[1, 1], [2, 2], [3, 3]]) == pytest.approx(45)
    assert principal_direction_deg([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]) is None
    assert polyline_length([]) == 0
    assert polyline_length([[1, 2]]) == 0
    with pytest.raises(ValueError, match="finite"):
        polyline_length([[0, float("nan")]])


def _edge_set(traces):
    edges = []
    for trace in traces:
        points = [tuple(point) for point in trace["points"]]
        edges.extend(tuple(sorted((a, b))) for a, b in zip(points, points[1:]))
    return edges


def _assert_exhaustive(result):
    graph = result["graph"]
    edges = _edge_set(result["traces"])
    assert len(edges) == len(set(edges))
    assert graph["edge_count"] == graph["visited_edge_count"]
    assert graph["edge_count"] == graph["retained_edge_count"] + graph["discarded_edge_count"]
    assert len(edges) == graph["retained_edge_count"]


@pytest.mark.parametrize("mode,direction,expected", [
    ("horizontal", 0, 8), ("vertical", 90, 8), ("diagonal", 45, 8 * math.sqrt(2)),
])
def test_skeleton_line_coordinates_and_lengths(mode, direction, expected):
    mask = np.zeros((11, 11), bool)
    if mode == "horizontal":
        mask[5, 1:10] = True
    elif mode == "vertical":
        mask[1:10, 5] = True
    else:
        mask[np.arange(1, 10), np.arange(1, 10)] = True
    result = vectorize_fractures(mask, min_length=0)
    assert len(result["traces"]) == 1
    trace = result["traces"][0]
    assert polyline_length(trace["points"]) == pytest.approx(expected)
    assert axial_direction_deg(trace["points"]) == pytest.approx(direction)
    assert trace["start_node"]["kind"] == "endpoint"
    assert trace["end_node"]["kind"] == "endpoint"
    assert all(float(coordinate) % 1 == .5 for point in trace["points"] for coordinate in point)
    _assert_exhaustive(result)


def test_branch_splits_without_duplicate_edges_or_diagonal_shortcuts():
    mask = np.zeros((13, 13), bool)
    mask[2:11, 6] = True
    mask[6, 6:11] = True
    result = vectorize_fractures(mask, min_length=0)
    assert len(result["traces"]) == 3
    assert result["graph"]["junction_pixel_count"] == 1
    assert result["graph"]["edge_count"] == 12
    assert sum(polyline_length(trace["points"]) for trace in result["traces"]) == 12
    assert all("junction" in {trace["start_node"]["kind"], trace["end_node"]["kind"]}
               for trace in result["traces"])
    _assert_exhaustive(result)


def test_cycle_is_closed_and_has_no_endpoint_direction():
    mask = np.zeros((9, 9), bool)
    mask[2, 2:7] = mask[6, 2:7] = True
    mask[2:7, 2] = mask[2:7, 6] = True
    result = vectorize_fractures(mask, min_length=0)
    assert len(result["traces"]) == 1
    trace = result["traces"][0]
    assert trace["is_closed"]
    assert trace["points"][0] == trace["points"][-1]
    assert trace["start_node"]["kind"] == "cycle"
    assert axial_direction_deg(trace["points"]) is None
    _assert_exhaustive(result)


def test_disconnected_components_and_length_filter_are_accounted():
    mask = np.zeros((12, 15), bool)
    mask[2, 1:12] = True
    mask[8, 2:5] = True
    mask[10, 13] = True
    result = vectorize_fractures(mask, min_length=5)
    assert len(result["traces"]) == 1
    assert result["graph"]["discarded_edge_count"] == 2
    assert result["graph"]["retained_edge_count"] == 10
    assert result["graph"]["isolated_pixel_count"] == 1
    _assert_exhaustive(result)


def test_length_uses_raw_path_and_not_display_simplification():
    mask = np.zeros((20, 20), bool)
    mask[3, 2:8] = True
    mask[3:15, 7] = True
    detailed = vectorize_fractures(mask, min_length=0, simplify_tolerance=0)
    simplified = vectorize_fractures(mask, min_length=0, simplify_tolerance=100)
    assert [trace["points"] for trace in detailed["traces"]] == [trace["points"] for trace in simplified["traces"]]
    assert any(len(trace["display_points"]) < len(trace["points"]) for trace in simplified["traces"])
    _assert_exhaustive(simplified)


def _ring_area(ring):
    p = np.asarray(ring)
    return abs(np.sum(p[:-1, 0] * p[1:, 1] - p[1:, 0] * p[:-1, 1])) / 2


def _polygon_area(polygons):
    return sum(_ring_area(polygon["exterior"]) - sum(_ring_area(hole) for hole in polygon["holes"])
               for polygon in polygons)


def test_known_rectangle_polygon_and_exact_area():
    mask = np.zeros((12, 17), bool)
    mask[2:8, 3:14] = True
    polygons = mask_to_polygons(mask)
    assert len(polygons) == 1
    assert polygons[0]["holes"] == []
    assert set(map(tuple, polygons[0]["exterior"])) == {(3., 2.), (14., 2.), (14., 8.), (3., 8.)}
    assert polygons[0]["exterior"][0] == polygons[0]["exterior"][-1]
    assert _polygon_area(polygons) == np.count_nonzero(mask) == 66


def test_polygon_holes_and_multipart_and_diagonal_touch():
    mask = np.zeros((15, 20), bool)
    mask[1:12, 1:12] = True
    mask[3:10, 3:10] = False
    mask[5:7, 5:7] = True  # island inside a hole
    mask[2, 15] = mask[3, 16] = True  # point contact should stay multipart
    polygons = mask_to_polygons(mask)
    assert len(polygons) == 4
    assert sum(len(polygon["holes"]) for polygon in polygons) == 1
    assert _polygon_area(polygons) == np.count_nonzero(mask)


def test_random_polygonization_preserves_raster_area():
    random = np.random.default_rng(193)
    for _ in range(20):
        mask = random.random((12, 17)) > .45
        assert _polygon_area(mask_to_polygons(mask)) == np.count_nonzero(mask)


def test_area_density_and_physical_calibration():
    mask = np.ones((10, 20), bool)
    labels = np.ones(mask.shape, np.uint16)
    labels[:, 10:] = 2
    traces = [{"points": [[1, 1], [4, 5]]}, {"points": [[1, 3], [11, 3]]}]
    stats = calculate_statistics(traces, mask, labels, meters_per_pixel=.1, face_verified=True)
    assert stats["fracture_count"] == 2
    assert stats["lithology_region_count"] == 2
    assert stats["total_length_px"] == 15
    assert stats["observation_area_px2"] == 200
    assert stats["p21_per_px"] == pytest.approx(.075)
    assert stats["total_length_m"] == pytest.approx(1.5)
    assert stats["observation_area_m2"] == pytest.approx(2)
    assert stats["p21_per_m"] == pytest.approx(.75)
    assert stats["statistics_status"] == "verified_roi"
    assert stats["details"]["detection_status"] == "candidate_fracture_traces"
    assert traces[0]["length_px"] == 5
    assert traces[0]["length_m"] == .5


def test_missing_scale_and_empty_area_are_null_not_fabricated():
    stats = calculate_statistics([], np.zeros((3, 4), bool), np.zeros((3, 4), int))
    assert stats["total_length_m"] is None
    assert stats["observation_area_m2"] is None
    assert stats["p21_per_m"] is None
    assert stats["p21_per_px"] is None
    assert stats["statistics_status"] == "candidate"


def test_geometry_input_validation():
    with pytest.raises(ValueError, match="positive"):
        calculate_statistics([], np.ones((2, 2), bool), np.ones((2, 2)), meters_per_pixel=0)
    with pytest.raises(ValueError, match="outside"):
        calculate_statistics([], np.zeros((2, 2), bool), np.ones((2, 2)))
    with pytest.raises(ValueError, match="two-dimensional"):
        vectorize_fractures(np.ones((2, 2, 3)))
    with pytest.raises(ValueError, match="nonnegative"):
        vectorize_fractures(np.ones((2, 2)), min_length=-1)


def test_empty_masks_are_valid_empty_results():
    result = vectorize_fractures(np.zeros((10, 10), bool))
    assert result["traces"] == []
    assert result["graph"]["node_count"] == 0
    assert mask_to_polygons(np.zeros((10, 10), bool)) == []
    _assert_exhaustive(result)

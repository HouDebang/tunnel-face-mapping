"""Real selectable image partitions; these tests do not validate lithology."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from face_mapping.config import LithologyConfig
from face_mapping.lithology import LithologyBaseline
import face_mapping.lithology as module


def _two_colors(height=96, width=128):
    image = np.empty((height, width, 3), dtype=np.uint8)
    image[:, :width // 2] = (150, 110, 70)
    image[:, width // 2:] = (70, 100, 170)
    return image


def _assert_partition(result, roi):
    labels = result.labels
    assert labels.shape == roi.shape
    assert np.array_equal(labels > 0, roi)
    values = np.unique(labels[roi])
    assert np.array_equal(values, np.arange(1, len(values) + 1))
    assert sum(np.count_nonzero(labels == value) for value in values) == np.count_nonzero(roi)
    for value in values:
        count, _ = cv2.connectedComponents((labels == value).astype(np.uint8), connectivity=4)
        assert count == 2, f"region {value} must be one 4-connected component"
    assert result.details["category"] == "unknown"
    assert result.details["semantic_classification"] is False


def test_slic_really_executes_with_configured_arguments_and_no_global_kmeans(monkeypatch):
    image = _two_colors()
    roi = np.ones(image.shape[:2], dtype=bool)
    real_slic = module.slic
    calls = []

    def observed_slic(*args, **kwargs):
        calls.append(kwargs)
        return real_slic(*args, **kwargs)

    def forbidden_kmeans(*args, **kwargs):
        raise AssertionError("SLIC must execute its own implementation")

    monkeypatch.setattr(module, "slic", observed_slic)
    monkeypatch.setattr(module.cv2, "kmeans", forbidden_kmeans)
    config = LithologyConfig(algorithm="slic", slic_segments=40, slic_compactness=7.)
    result = LithologyBaseline().predict(image, roi, config)
    _assert_partition(result, roi)
    assert len(calls) == 1
    assert calls[0]["mask"] is roi
    assert calls[0]["n_segments"] == 40
    assert calls[0]["compactness"] == 7.
    assert calls[0]["convert2lab"] is calls[0]["enforce_connectivity"] is True
    assert calls[0]["start_label"] == 1 and calls[0]["channel_axis"] == -1
    assert result.method == "slic_lab_adjacency_v1"
    assert result.parameters["algorithm"] == "slic"
    assert result.details["implementation"] == "skimage.segmentation.slic"


def test_slic_separates_known_color_boundary_and_merges_superpixels():
    image = _two_colors()
    roi = np.ones(image.shape[:2], dtype=bool)
    config = LithologyConfig(algorithm="slic", slic_segments=40,
                            min_region_pixels=8, min_region_area_ratio=0,
                            merge_color_distance=8, max_regions=80)
    result = LithologyBaseline().predict(image, roi, config)
    _assert_partition(result, roi)
    assert result.details["initial_region_count"] > 2
    assert result.details["region_count"] == 2
    assert np.unique(result.labels[:, :64]).size == 1
    assert np.unique(result.labels[:, 64:]).size == 1
    assert result.labels[0, 0] != result.labels[0, -1]
    assert result.details["unassigned_pixel_count"] == 0


@pytest.mark.parametrize("algorithm", ["kmeans", "slic"])
def test_both_algorithms_preserve_irregular_roi_with_holes_and_disconnected_islands(algorithm):
    image = _two_colors()
    roi = np.zeros(image.shape[:2], dtype=bool)
    roi[8:85, 7:60] = True
    roi[20:40, 20:40] = False
    roi[14:80, 75:120] = True
    roi[0, 0] = roi[1, 1] = True  # diagonal contact remains two IDs
    result = LithologyBaseline().predict(image, roi, LithologyConfig(algorithm=algorithm))
    _assert_partition(result, roi)
    assert result.labels[0, 0] != result.labels[1, 1]


@pytest.mark.parametrize("shape,coordinates", [
    ((20, 20), [(3, 3)]),
    ((1, 7), [(0, 1), (0, 2), (0, 4), (0, 6)]),
    ((2, 2), [(0, 0), (1, 1)]),
])
def test_tiny_slic_rois_preserve_every_pixel_and_report_unassigned_fallback(shape, coordinates):
    image = np.full((*shape, 3), 100, dtype=np.uint8)
    roi = np.zeros(shape, dtype=bool)
    for coordinate in coordinates:
        roi[coordinate] = True
    result = LithologyBaseline().predict(image, roi, LithologyConfig(algorithm="slic"))
    _assert_partition(result, roi)
    assert result.parameters["effective_slic_segments"] == len(coordinates)
    if len(coordinates) == 1:
        assert result.details["degenerate_roi_fallback"] is True
        assert result.details["unassigned_pixel_count"] == 1
        assert any("SLIC left 1 ROI pixels unassigned" in warning for warning in result.warnings)


@pytest.mark.parametrize("algorithm", ["kmeans", "slic"])
def test_algorithms_are_repeatable_and_have_distinct_provenance(algorithm):
    rng = np.random.default_rng(8)
    image = np.clip(_two_colors().astype(np.int16) + rng.integers(-10, 11, (96, 128, 3)), 0, 255).astype(np.uint8)
    roi = np.ones(image.shape[:2], dtype=bool)
    config = LithologyConfig(algorithm=algorithm)
    first = LithologyBaseline().predict(image, roi, config)
    second = LithologyBaseline().predict(image, roi, config)
    assert np.array_equal(first.labels, second.labels)
    assert first.parameters == second.parameters and first.details == second.details
    expected = "slic_lab_adjacency_v1" if algorithm == "slic" else "lab_texture_xy_kmeans_adjacency_v1"
    assert first.method == expected


def test_kmeans_is_still_the_default():
    image = _two_colors(48, 64)
    roi = np.ones(image.shape[:2], dtype=bool)
    default = LithologyBaseline().predict(image, roi, LithologyConfig())
    explicit = LithologyBaseline().predict(image, roi, LithologyConfig(algorithm="kmeans"))
    assert np.array_equal(default.labels, explicit.labels)
    assert default.method == explicit.method == "lab_texture_xy_kmeans_adjacency_v1"


def test_slic_count_and_shared_region_limit_change_real_partition():
    yy, xx = np.indices((80, 100))
    image = np.stack([xx * 2, yy * 3, xx + yy], axis=-1).astype(np.uint8)
    roi = np.ones(image.shape[:2], dtype=bool)
    common = {"algorithm": "slic", "min_region_pixels": 1,
              "min_region_area_ratio": 0, "merge_color_distance": 0}
    coarse = LithologyBaseline().predict(image, roi, LithologyConfig(**common, slic_segments=20, max_regions=100))
    fine = LithologyBaseline().predict(image, roi, LithologyConfig(**common, slic_segments=80, max_regions=100))
    limited = LithologyBaseline().predict(image, roi, LithologyConfig(**common, slic_segments=80, max_regions=4))
    assert fine.details["initial_region_count"] > coarse.details["initial_region_count"]
    assert not np.array_equal(coarse.labels, fine.labels)
    assert limited.details["region_count"] <= 4
    _assert_partition(limited, roi)


def test_unknown_backend_algorithm_is_rejected():
    image = _two_colors(16, 16)
    with pytest.raises(ValueError, match="Unknown lithology algorithm"):
        LithologyBaseline().predict(image, np.ones(image.shape[:2], bool), SimpleNamespace(algorithm="missing"))

"""Actual dark-line execution and provenance for the selectable detectors."""
import cv2
import numpy as np
import pytest

from face_mapping.config import FractureConfig
from face_mapping.fracture_detection import FractureBaseline


ALGORITHMS = {
    "blackhat_hessian": "clahe_blackhat_hessian_pca_v1",
    "blackhat": "clahe_blackhat_pca_v1",
    "frangi": "clahe_frangi_pca_v1",
}


@pytest.fixture
def dark_lines():
    image = np.full((140, 180, 3), 190, dtype=np.uint8)
    cv2.line(image, (20, 40), (155, 40), (35, 35, 35), 3)
    cv2.line(image, (30, 85), (150, 110), (55, 55, 55), 5)
    roi = np.zeros(image.shape[:2], dtype=bool)
    roi[10:130, 10:170] = True
    return image, roi


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_real_detectors_find_dark_lines_and_record_provenance(algorithm, dark_lines):
    image, roi = dark_lines
    result = FractureBaseline().predict(image, roi, FractureConfig(algorithm=algorithm))
    assert result.method == ALGORITHMS[algorithm]
    assert result.mask.dtype == bool
    assert result.mask.shape == roi.shape
    assert result.mask.any()
    assert not result.mask[~roi].any()
    # Keep actual line evidence, rather than merely detecting an unrelated blob.
    assert result.mask[37:44, 30:145].any()
    assert result.details["response_nonzero_pixels_in_roi"] > 0
    assert result.details["semantic_validation"] is False
    assert result.details["result_kind"] == "candidate_fracture_mask"
    assert result.parameters["algorithm"] == algorithm
    assert .2 <= result.parameters["effective_response_threshold"] <= 1


def test_algorithms_produce_different_computed_masks(dark_lines):
    image, roi = dark_lines
    results = [FractureBaseline().predict(image, roi, FractureConfig(algorithm=a))
               for a in ALGORITHMS]
    assert all(not np.array_equal(first.mask, second.mask)
               for index, first in enumerate(results) for second in results[index + 1:])
    assert "hessian_sigma" in results[0].parameters
    assert "hessian_sigma" not in results[1].parameters
    assert results[2].parameters["response_source"] == "skimage.filters.frangi"
    assert "minimum_blackhat_contrast" not in results[2].parameters


def test_frangi_does_not_require_blackhat_evidence(dark_lines, monkeypatch):
    image, roi = dark_lines

    def unexpected_morphology(*args, **kwargs):
        raise AssertionError("Frangi must not call the Blackhat operation")

    monkeypatch.setattr(cv2, "morphologyEx", unexpected_morphology)
    result = FractureBaseline().predict(image, roi, FractureConfig(algorithm="frangi"))
    assert result.mask.any()
    assert result.parameters["black_ridges"] is True
    assert result.parameters["frangi_sigmas"] == [1., 2., 3., 4.]


def test_frangi_scale_configuration_changes_computation(dark_lines):
    image, roi = dark_lines
    small = FractureBaseline().predict(image, roi, FractureConfig(
        algorithm="frangi", frangi_sigma_min=.5, frangi_sigma_max=1., frangi_scale_count=2))
    large = FractureBaseline().predict(image, roi, FractureConfig(
        algorithm="frangi", frangi_sigma_min=3., frangi_sigma_max=5., frangi_scale_count=3))
    assert small.parameters["frangi_sigmas"] == [.5, 1.]
    assert large.parameters["frangi_sigmas"] == [3., 4., 5.]
    assert not np.array_equal(small.mask, large.mask)


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("value", [0, 127, 255])
def test_constant_images_have_no_invented_ridges(algorithm, value):
    image = np.full((48, 64, 3), value, dtype=np.uint8)
    roi = np.ones(image.shape[:2], dtype=bool)
    result = FractureBaseline().predict(image, roi, FractureConfig(algorithm=algorithm))
    assert not result.mask.any()
    assert result.details["constant_input"]
    assert result.details["response_nonzero_pixels_in_roi"] == 0
    assert result.warnings


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_roi_and_common_length_filter_are_applied(algorithm, dark_lines):
    image, roi = dark_lines
    roi[70:, :] = False
    result = FractureBaseline().predict(image, roi, FractureConfig(algorithm=algorithm))
    assert result.mask.any()
    assert not result.mask[~roi].any()
    too_short = FractureBaseline().predict(image, roi, FractureConfig(
        algorithm=algorithm, min_line_length=300.))
    assert not too_short.mask.any()
    assert too_short.details["rejected_components"]["short_component"] > 0


@pytest.mark.parametrize("invalid", [
    np.zeros((30, 30), dtype=np.uint8),
    np.zeros((30, 30, 3), dtype=np.float32),
    np.zeros((0, 30, 3), dtype=np.uint8),
])
def test_invalid_images_raise_explicit_error(invalid):
    with pytest.raises(ValueError, match="uint8 RGB"):
        FractureBaseline().predict(invalid, np.ones(invalid.shape[:2], bool), FractureConfig())


@pytest.mark.parametrize("roi", [np.ones((10, 10), bool), np.zeros((30, 30), bool),
                                 np.ones((30, 30), np.uint8)])
def test_invalid_rois_raise_explicit_error(roi):
    with pytest.raises(ValueError, match="boolean ROI"):
        FractureBaseline().predict(np.zeros((30, 30, 3), np.uint8), roi, FractureConfig())


def test_unknown_algorithm_is_never_silently_substituted(dark_lines):
    image, roi = dark_lines
    config = FractureConfig().model_copy(update={"algorithm": "missing_backend"})
    with pytest.raises(ValueError, match="Unsupported fracture algorithm"):
        FractureBaseline().predict(image, roi, config)

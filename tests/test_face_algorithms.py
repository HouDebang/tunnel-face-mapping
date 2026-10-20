"""Executable ROI choices, provenance, determinism and explicit failure policy."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from face_mapping.config import FaceConfig
from face_mapping.face_detection import detect_face


def rock_image(height=150, width=210):
    rng = np.random.default_rng(12)
    image = np.full((height, width, 3), [22, 30, 45], dtype=np.uint8)
    yy, xx = np.indices((height, width))
    center = ((xx - width / 2) / (width * .4)) ** 2 + ((yy - height / 2) / (height * .43)) ** 2 < 1
    noise = rng.integers(-18, 19, size=(center.sum(), 1))
    image[center] = np.clip(np.array([125, 112, 99]) + noise, 0, 255)
    return image


def test_grabcut_executes_real_graph_cut(monkeypatch):
    calls = []
    actual = cv2.grabCut

    def spy(image, mask, rectangle, bg, fg, iterations, mode):
        calls.append((image.shape, iterations, mode))
        return actual(image, mask, rectangle, bg, fg, iterations, mode)

    monkeypatch.setattr(cv2, "grabCut", spy)
    image = rock_image(700, 900)
    result = detect_face(image, FaceConfig(algorithm="grabcut", grabcut_iterations=2))
    assert calls == [((498, 640, 3), 2, cv2.GC_INIT_WITH_MASK)]
    assert result.method == "central_seed_grabcut_v1"
    assert result.status == "heuristic_candidate" and not result.verified
    assert result.mask.dtype == bool and result.mask.shape == image.shape[:2]
    assert .35 < result.mask.mean() < .7
    assert result.details["semantic_validation"] is False
    assert "background" in result.details["initialization_assumption"]
    assert result.details["grabcut_margin"] == .05


def test_grabcut_is_reproducible():
    image = rock_image()
    config = FaceConfig(algorithm="grabcut", grabcut_iterations=3)
    first = detect_face(image, config)
    cv2.setRNGSeed(972)
    second = detect_face(image, config)
    np.testing.assert_array_equal(first.mask, second.mask)
    assert first.details == second.details


def test_area_failure_falls_back_with_selected_method():
    result = detect_face(rock_image(), FaceConfig(algorithm="grabcut", min_area_ratio=.8, max_area_ratio=.95))
    assert result.method == result.status == "full_image_fallback"
    assert result.mask.all() and not result.verified
    assert result.details["attempted_method"] == "central_seed_grabcut_v1"
    assert result.details["selected_algorithm"] == "grabcut"
    assert result.details["candidate_area_ratio"] < .8
    with pytest.raises(ValueError, match="fallback is disabled"):
        detect_face(rock_image(), FaceConfig(algorithm="grabcut", min_area_ratio=.8, max_area_ratio=.95, allow_full_image_fallback=False))


@pytest.mark.parametrize("image", [np.full((30, 30, 3), 90, dtype=np.uint8), np.tile(np.array([90, 60, 30], dtype=np.uint8), (30, 30, 1)), np.zeros((2, 3, 3), dtype=np.uint8)])
def test_grabcut_constant_or_tiny_images_are_explicit_fallback(image):
    result = detect_face(image, FaceConfig(algorithm="grabcut"))
    assert result.status == "full_image_fallback" and result.mask.all()
    assert result.details["attempted_method"] == "central_seed_grabcut_v1"
    assert "failure_reason" in result.details
    with pytest.raises(ValueError, match="fallback is disabled"):
        detect_face(image, FaceConfig(algorithm="grabcut", allow_full_image_fallback=False))


def test_full_image_mode_does_not_run_grabcut(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Configured full-image mode must not invoke graph cut")
    monkeypatch.setattr(cv2, "grabCut", forbidden)
    result = detect_face(rock_image(), FaceConfig(algorithm="grabcut", mode="full_image"))
    assert result.mask.all() and result.status == "full_image_fallback"
    assert result.details["attempted_automatic_detection"] is False
    assert result.details["reason"] == "configured_full_image"


def test_opencv_error_keeps_attempted_method(monkeypatch):
    def fail(*args, **kwargs):
        raise cv2.error("test optimizer failure")
    monkeypatch.setattr(cv2, "grabCut", fail)
    result = detect_face(rock_image(), FaceConfig(algorithm="grabcut"))
    assert result.status == "full_image_fallback"
    assert result.details["attempted_method"] == "central_seed_grabcut_v1"
    assert "test optimizer failure" in result.details["opencv_error"]


def test_unknown_face_algorithm_is_rejected():
    with pytest.raises(ValidationError):
        FaceConfig(algorithm="not_installed")
    with pytest.raises(ValueError, match="Unknown face algorithm"):
        detect_face(rock_image(), SimpleNamespace(algorithm="not_installed", mode="full_image"))


@pytest.mark.parametrize("image", [None, np.zeros((20, 20), np.uint8), np.zeros((20, 20, 4), np.uint8), np.zeros((20, 20, 3), np.float32), np.zeros((0, 20, 3), np.uint8)])
def test_invalid_images_are_rejected_before_segmentation(image):
    with pytest.raises(ValueError, match="uint8 RGB|dimensions must be positive"):
        detect_face(image, FaceConfig(algorithm="grabcut"))


def test_default_method_remains_the_original_baseline():
    result = detect_face(rock_image(), FaceConfig())
    assert result.method == "central_lab_texture_component_v1"
    assert result.details["selected_algorithm"] == "central_texture"
    assert "central_lab_reference" in result.details

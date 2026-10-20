"""Mask resampling and continuous pixel-center transforms."""
import cv2
import numpy as np


def resize_mask(mask: np.ndarray, original_size: list[int]) -> np.ndarray:
    w, h = original_size
    return cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST_EXACT).astype(bool)


def resize_labels(labels: np.ndarray, original_size: list[int]) -> np.ndarray:
    w, h = original_size
    return cv2.resize(labels.astype(np.float64), (w, h), interpolation=cv2.INTER_NEAREST_EXACT).astype(np.int32)


def transform_points(points, scale_x: float, scale_y: float, inverse: bool = False):
    """Transform continuous canvas coordinates, not integer pixel indices."""
    scales = np.array([scale_x, scale_y], dtype=float)
    if np.any(scales <= 0):
        raise ValueError("Scales must be positive")
    values = np.asarray(points, dtype=float)
    return (values / scales if inverse else values * scales).tolist()

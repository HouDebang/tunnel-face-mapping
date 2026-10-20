"""Image loading, orientation and enhancement with explicit resize metadata."""
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


def load_image(path: str | Path, config: Any) -> tuple[np.ndarray, np.ndarray, dict[str, Any], list[str]]:
    """Read a genuine image; returned original coordinates are EXIF-oriented.

    The working image is never cropped. Scale factors use the actual rounded
    output dimensions, so anisotropy from resize rounding is accounted for.
    """
    path = Path(path)
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("Input must be a JPG, JPEG or PNG image")
    if not path.is_file():
        raise FileNotFoundError(f"Image does not exist: {path}")
    try:
        with Image.open(path) as source:
            source_format = source.format
            frame_count = getattr(source, "n_frames", 1)
            source_size = list(source.size)
            orientation = int(source.getexif().get(274, 1))
            source.load()  # PIL defaults reject truncated/corrupt image data.
            oriented = ImageOps.exif_transpose(source)
            original = np.asarray(oriented.convert("RGB"), dtype=np.uint8).copy()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(f"Unable to decode image {path.name}: {exc}") from exc

    height, width = original.shape[:2]
    if min(width, height) < 16:
        raise ValueError("Image dimensions must each be at least 16 pixels")
    channel_std = original.reshape(-1, 3).astype(np.float32).std(axis=0)
    lo, hi = np.percentile(original, [0.5, 99.5])
    if hi <= 2 or lo >= 253 or float(channel_std.max()) < .5:
        raise ValueError("Image is effectively black, white or spatially constant")

    warnings: list[str] = []
    if frame_count > 1:
        warnings.append(f"Multi-picture {source_format} container: only primary frame 0 is analyzed; {frame_count} embedded frames are not a 3D reconstruction.")
    gray = cv2.cvtColor(original, cv2.COLOR_RGB2GRAY)
    dark_fraction = float(np.mean(gray < 12))
    bright_fraction = float(np.mean(gray > 245))
    if dark_fraction > .35:
        warnings.append(f"Large underexposed area: {dark_fraction:.1%} of pixels below gray 12")
    if bright_fraction > .35:
        warnings.append(f"Large overexposed area: {bright_fraction:.1%} of pixels above gray 245")
    if float(np.percentile(gray, 95) - np.percentile(gray, 5)) < 12:
        warnings.append("Low input contrast may prevent reliable line detection")

    ratio = min(1., float(config.max_dimension) / max(width, height))
    work_width, work_height = max(1, round(width * ratio)), max(1, round(height * ratio))
    working = original.copy() if ratio == 1 else cv2.resize(original, (work_width, work_height), interpolation=cv2.INTER_AREA)
    if config.enhance:
        lab = cv2.cvtColor(working, cv2.COLOR_RGB2LAB)
        clahe = cv2.createCLAHE(clipLimit=float(config.clahe_clip), tileGridSize=(int(config.clahe_grid), int(config.clahe_grid)))
        lab[..., 0] = clahe.apply(lab[..., 0])
        working = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    metadata = {
        "source_size": source_size,
        "original_size": [width, height],
        "working_size": [work_width, work_height],
        "scale_x": work_width / width,
        "scale_y": work_height / height,
        "exif_transposed": orientation in range(2, 9),
        "details": {
            "exif_orientation": orientation,
            "source_format": source_format,
            "embedded_frame_count": frame_count,
            "selected_frame": 0,
            "original_definition": "decoded RGB after EXIF orientation; no crop",
            "enhancement": "LAB L-channel CLAHE" if config.enhance else "none",
            "clahe_clip": float(config.clahe_clip),
            "clahe_grid": int(config.clahe_grid),
            "underexposed_fraction": dark_fraction,
            "overexposed_fraction": bright_fraction,
        },
    }
    return original, working, metadata, warnings

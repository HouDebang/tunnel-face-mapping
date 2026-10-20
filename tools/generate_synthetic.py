"""Generate a deterministic software test image, never accuracy evidence.

The image contains procedural texture, broad colour regions, dark polylines,
uneven illumination and a simple foreground occluder. It is deliberately not
a model of geological formation and supplies no masks to the detector.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def generate_synthetic(
    path: Path, width: int = 640, height: int = 480, seed: int = 7
) -> Path:
    """Write a reproducible synthetic rock-like RGB image and provenance JSON."""
    path = Path(path)
    if width < 64 or height < 64:
        raise ValueError("Synthetic image dimensions must each be at least 64 pixels.")
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    nx, ny = xx / width, yy / height

    # Multiscale texture uses only test-generation dependencies, not the pipeline.
    texture = np.zeros((height, width), dtype=np.float64)
    for size, strength in ((8, 9.0), (32, 5.0), (128, 2.0)):
        noise = rng.integers(0, 256, (size, size), dtype=np.uint8)
        enlarged = Image.fromarray(noise).resize((width, height), Image.Resampling.BICUBIC)
        texture += (np.asarray(enlarged, dtype=float) - 127.5) * (strength / 64.0)
    texture += rng.normal(0, 2.0, (height, width))
    illumination = 0.78 + 0.22 * nx + 0.09 * np.sin(2 * np.pi * ny)

    face = ((nx - 0.49) / 0.47) ** 2 + ((ny - 0.52) / 0.49) ** 2 < 1
    image = np.empty((height, width, 3), dtype=float)
    image[:] = (41, 43, 44)
    for channel, level in enumerate((159, 151, 135)):
        image[:, :, channel] = np.where(face, (level + texture) * illumination, image[:, :, channel])

    # Broad visual partitions do not carry a lithology label or geological meaning.
    warm_region = face & (nx < 0.34 + 0.10 * np.sin(5 * ny))
    cool_region = face & (nx > 0.62 + 0.07 * np.cos(7 * ny))
    image[warm_region] += (22, 5, -13)
    image[cool_region] += (-18, -4, 9)
    image = np.clip(image, 0, 255).astype(np.uint8)
    canvas = Image.fromarray(image, "RGB")
    draw = ImageDraw.Draw(canvas)

    # Hand-defined strokes are only image content. The pipeline receives RGB alone.
    cracks = [
        [(0.16, 0.23), (0.27, 0.35), (0.35, 0.47), (0.44, 0.59), (0.53, 0.74)],
        [(0.35, 0.47), (0.24, 0.54), (0.15, 0.65)],
        [(0.24, 0.74), (0.39, 0.67), (0.55, 0.62), (0.70, 0.55), (0.84, 0.49)],
        [(0.66, 0.17), (0.62, 0.29), (0.63, 0.41), (0.60, 0.53)],
        [(0.43, 0.15), (0.45, 0.25), (0.50, 0.36)],
    ]
    stroke_width = max(2, round(width / 210))
    for crack in cracks:
        points = [(round(x * width), round(y * height)) for x, y in crack]
        draw.line(points, fill=(51, 47, 42), width=stroke_width + 2, joint="curve")
        draw.line(points, fill=(22, 23, 23), width=stroke_width, joint="curve")
    # A bright rectangular object tests the limitations of image-only heuristics.
    draw.polygon(
        [(int(width * 0.77), int(height * 0.80)),
         (int(width * 0.93), int(height * 0.78)),
         (int(width * 0.98), height - 1),
         (int(width * 0.76), height - 1)],
        fill=(194, 148, 37),
    )
    canvas = canvas.filter(ImageFilter.GaussianBlur(radius=0.35))
    path.parent.mkdir(parents=True, exist_ok=True)
    save_options = {"quality": 95, "subsampling": 0} if path.suffix.lower() in {".jpg", ".jpeg"} else {}
    canvas.save(path, **save_options)
    metadata = {
        "synthetic": True,
        "generator": "procedural_rock_texture_v1",
        "seed": seed,
        "width": width,
        "height": height,
        "purpose": "software smoke test and reproducibility; not real-image recognition accuracy validation",
        "ground_truth_available": False,
        "supplied_to_detector": "RGB image only; no generator masks or line coordinates",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    path.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("examples/synthetic_face.png"))
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    print(generate_synthetic(args.output, args.width, args.height, args.seed))


if __name__ == "__main__":
    main()

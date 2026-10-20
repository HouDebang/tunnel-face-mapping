"""Rendering independent of recognition. All layers share original canvas."""
from pathlib import Path
import colorsys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage.segmentation import find_boundaries


def region_color(label: int) -> tuple[int, int, int]:
    return tuple(round(v * 255) for v in colorsys.hsv_to_rgb((label * .61803398875) % 1, .62, .95))


def save_rgb(path: Path, array: np.ndarray):
    Image.fromarray(np.asarray(array, dtype=np.uint8)).save(path)


def lithology_overlay(image: np.ndarray, labels: np.ndarray) -> np.ndarray:
    result = image.astype(float).copy()
    for label in np.unique(labels):
        if label:
            selected = labels == label
            result[selected] = .76 * result[selected] + .24 * np.array(region_color(int(label)))
    boundary = find_boundaries(labels, mode="inner") & (labels > 0)
    result[boundary] = (255, 220, 80)
    return result.astype(np.uint8)


def fracture_overlay(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    result = image.astype(float).copy()
    result[mask] = .35 * result[mask] + .65 * np.array([255, 30, 40])
    return result.astype(np.uint8)


def _font(size):
    for path in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def render_sketch(image, face_mask, labels, traces, meters_per_pixel, face_status):
    h, w = image.shape[:2]
    canvas = Image.fromarray(lithology_overlay(image, labels) if labels is not None else image.copy()).convert("RGB")
    draw = ImageDraw.Draw(canvas)
    font_size = max(10, min(24, w // 70))
    font = _font(font_size)
    if face_mask is not None:
        # Pad so a full-image fallback has an explicit visible border.
        padded = np.pad(face_mask, 1)
        border = find_boundaries(padded, mode="inner")[1:-1, 1:-1] & face_mask
        array = np.asarray(canvas).copy()
        array[border] = [30, 255, 100]
        canvas = Image.fromarray(array)
        draw = ImageDraw.Draw(canvas)
    labeled = {t.id for t in sorted(traces or [],key=lambda t:t.length_px,reverse=True)[:80]}
    for trace in traces or []:
        points = [tuple(p) for p in trace.display_points]
        if len(points) > 1:
            draw.line(points, fill=(255, 25, 35), width=max(1, w // 650))
        if points and trace.id in labeled:
            x, y = points[len(points) // 2]
            x = max(0, min(w - 6 * font_size, x + 2))
            y = max(0, min(h - font_size - 2, y))
            draw.text((x, y), trace.id, fill="white", font=font, stroke_width=1, stroke_fill="black")
    if labels is not None:
        for label in np.unique(labels):
            if label == 0:
                continue
            yy, xx = np.nonzero(labels==label)
            # Select an actual region pixel near the centroid, including rings.
            distances = (xx-xx.mean())**2+(yy-yy.mean())**2
            index = int(np.argmin(distances))
            x, y = int(xx[index]), int(yy[index])
            draw.text((min(x,max(0,w-6*font_size)),min(y,h-font_size-2)),f"R{label:03d}",fill=region_color(int(label)),font=font,stroke_width=1,stroke_fill="black")
    legend = ["CANDIDATE geological sketch", f"Green: analysis ROI ({face_status})", "Red: candidate fracture traces; angles in image plane"]
    ids = [int(n) for n in np.unique(labels) if n > 0] if labels is not None else []
    legend.append("Regions: " + (", ".join(f"R{n:03d}" for n in ids[:8]) or "unavailable") + (" ..." if len(ids) > 8 else "") + " (unknown)")
    legend.append(f"F labels: longest {len(labeled)}/{len(traces or [])}; complete IDs in JSON")
    legend.append("Scale: pixels only" if meters_per_pixel is None else f"Scale: {meters_per_pixel:g} m/original px (planar approximation)")
    panel = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    line_height = font_size + 4
    box_h = min(h, line_height * len(legend) + 10)
    pd.rectangle((0, 0, w, box_h), fill=(0, 0, 0, 155))
    for i, text in enumerate(legend):
        pd.text((6, 4 + i * line_height), text, fill="white", font=font)
    return np.asarray(Image.alpha_composite(canvas.convert("RGBA"), panel).convert("RGB"))

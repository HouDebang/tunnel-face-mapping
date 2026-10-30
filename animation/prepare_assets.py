"""Prepare browser-friendly assets from the real RK4+970 results.

The animation deliberately uses the measured face photograph, the generated
geological sketch, and the candidate trace/region geometry from this project.
No synthetic geology is substituted for the measured sample.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "animation" / "assets"
DATA = ROOT / "animation" / "data"
OUT.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)

RUN = ROOT / "outputs" / "real_RK4_970"


def save_jpeg(src: Path, dst: Path, size=(1600, 1200), quality=92):
    im = Image.open(src).convert("RGB")
    im = im.resize(size, Image.LANCZOS)
    im.save(dst, quality=quality, subsampling=0, optimize=True)


def save_png(src: Path, dst: Path, size=(1600, 1200)):
    im = Image.open(src).convert("RGB")
    im = im.resize(size, Image.LANCZOS)
    im.save(dst, optimize=True)


def make_thumbnail(src: Path, dst: Path, crop, size=(520, 360), quality=90):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    box = (int(crop[0] * w), int(crop[1] * h), int(crop[2] * w), int(crop[3] * h))
    im = im.crop(box)
    # cover-crop to requested aspect
    tw, th = size
    scale = max(tw / im.width, th / im.height)
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    left = (im.width - tw) // 2
    top = (im.height - th) // 2
    im = im.crop((left, top, left + tw, top + th))
    im.save(dst, quality=quality, subsampling=0, optimize=True)


def simplify(points, tolerance):
    arr = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    approx = cv2.approxPolyDP(arr, tolerance, False).reshape(-1, 2)
    return approx.tolist()


def main():
    face = ROOT / "examples" / "real_face_RK4_970.jpg"
    sketch = RUN / "geological_sketch.png"
    lith = RUN / "lithology_overlay.png"
    frac = RUN / "fracture_overlay.png"

    save_jpeg(face, OUT / "face.jpg")
    save_jpeg(sketch, OUT / "sketch.jpg", quality=94)
    save_jpeg(lith, OUT / "lithology_overlay.jpg", quality=90)
    save_jpeg(frac, OUT / "fracture_overlay.jpg", quality=90)

    # Capture thumbnails: three deliberately different, plausible viewpoints of
    # the same measured face. They are crops, not fabricated rock surfaces.
    make_thumbnail(face, OUT / "capture_1.jpg", (0.03, 0.05, 0.63, 0.72))
    make_thumbnail(face, OUT / "capture_2.jpg", (0.22, 0.12, 0.82, 0.79))
    make_thumbnail(face, OUT / "capture_3.jpg", (0.36, 0.03, 0.96, 0.70))

    # A soft luminance mask and a detail crop for the "orthophoto" card.
    make_thumbnail(sketch, OUT / "sketch_thumb.jpg", (0.02, 0.02, 0.98, 0.98), size=(720, 540), quality=92)

    regions_doc = json.loads((RUN / "lithology_regions.json").read_text(encoding="utf-8"))
    regions = []
    palette = ["#37d6c4", "#5ba7ff", "#f0b45a", "#9a8cff", "#72d38a",
               "#ff8e72", "#52c7e8", "#d6d06a", "#c38bff", "#6fe0b0"]
    for i, r in enumerate(regions_doc["regions"]):
        ext = r["polygon"][0]["exterior"]
        # tolerance in source pixels; 4096-wide image
        simp = simplify(ext, 7.0)
        if len(simp) < 3:
            continue
        pts = [[round(x / 4096.0, 5), round(y / 3072.0, 5)] for x, y in simp]
        area_ratio = float(r.get("area_ratio") or 0)
        regions.append({
            "id": r["region_id"],
            "label": r["region_id"],
            "color": palette[i % len(palette)],
            "area": area_ratio,
            "points": pts,
        })

    traces_doc = json.loads((RUN / "fracture_traces.json").read_text(encoding="utf-8"))
    traces = []
    for f in traces_doc["fractures"]:
        pts = f.get("display_points") or f.get("points") or []
        if len(pts) < 2:
            continue
        # normalized source coordinates
        arr = np.asarray(pts, dtype=np.float32)
        length = float(f.get("length_px") or 0)
        traces.append((length, f["id"], arr))
    traces.sort(key=lambda x: x[0], reverse=True)

    chosen = []
    for length, fid, arr in traces[:110]:
        # Keep the long, legible traces; simplify to a compact SVG polyline.
        arr2 = arr.reshape(-1, 1, 2)
        approx = cv2.approxPolyDP(arr2, 2.2, False).reshape(-1, 2)
        if len(approx) < 2:
            continue
        pts = [[round(float(x) / 4096.0, 5), round(float(y) / 3072.0, 5)] for x, y in approx]
        chosen.append({"id": fid, "length_px": round(length, 1), "points": pts})

    data = {
        "source": "outputs/real_RK4_970",
        "image_size": [4096, 3072],
        "regions": regions,
        "traces": chosen,
        "trace_total": len(traces_doc["fractures"]),
        "region_total": len(regions_doc["regions"]),
    }
    (DATA / "geometry.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (DATA / "geometry.js").write_text(
        "window.GEOMETRY = " + json.dumps(data, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    # Small manifest for the animation credits.
    manifest = {
        "face_source": str(face.relative_to(ROOT)).replace("\\", "/"),
        "result_source": str(RUN.relative_to(ROOT)).replace("\\", "/"),
        "trace_total": len(traces_doc["fractures"]),
        "region_total": len(regions_doc["regions"]),
        "display_traces": len(chosen),
    }
    (DATA / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("assets", len(list(OUT.glob("*"))), "files")


if __name__ == "__main__":
    main()

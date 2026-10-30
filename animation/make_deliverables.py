"""Create poster, storyboard contact sheet, and copy subtitles/readme."""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FRAMES = ROOT / "frames"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

FONT_CN = "C:/Windows/Fonts/msyh.ttc"
FONT_CN_BD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_MONO = "C:/Windows/Fonts/consola.ttf"


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def frame(t: float) -> Image.Image:
    idx = int(round(t * 30))
    idx = max(0, min(1799, idx))
    p = FRAMES / f"f_{idx:05d}.jpg"
    if not p.exists():
        raise FileNotFoundError(p)
    return Image.open(p).convert("RGB")


def make_poster():
    im = frame(57.0)
    im.save(OUT / "poster.jpg", quality=94, subsampling=0, optimize=True)
    print("poster", OUT / "poster.jpg")


def make_storyboard():
    W, H = 1920, 1200
    canvas = Image.new("RGB", (W, H), (4, 9, 12))
    d = ImageDraw.Draw(canvas)
    # subtle grid
    for x in range(0, W, 48):
        d.line([(x, 0), (x, H)], fill=(9, 20, 24))
    for y in range(0, H, 48):
        d.line([(0, y), (W, y)], fill=(9, 20, 24))

    title_f = font(FONT_CN_BD, 38)
    sub_f = font(FONT_CN, 16)
    label_f = font(FONT_CN_BD, 18)
    small_f = font(FONT_MONO, 12)
    d.text((42, 36), "隧道智能地质素描机器人", font=title_f, fill=(235, 251, 253))
    d.text((44, 86), "60 秒产品概念演示片  /  STORYBOARD  /  RK4+970", font=sub_f, fill=(122, 170, 178))
    d.line([(42, 116), (W - 42, 116)], fill=(43, 90, 98), width=1)

    shots = [
        (5.0, "01  开机与自主建图", "设备启动 · 实时空间建图", "00:05"),
        (12.0, "02  路径规划", "规划路径 · 目标点高亮", "00:12"),
        (18.0, "03  自主行进", "履带平台 · 沿隧道推进", "00:18"),
        (31.0, "04  摄影测量采集", "相机装置 · 多视角采集", "00:31"),
        (45.0, "05  自动化素描", "候选分区 · 结构面迹线", "00:45"),
        (57.0, "06  成果输出", "空间地图 · 重建 · 素描表", "00:57"),
    ]
    cell_w, cell_h = 590, 332
    xs = [42, 665, 1288]
    ys = [150, 610]
    for i, (t, name, desc, tc) in enumerate(shots):
        col, row = i % 3, i // 3
        x, y = xs[col], ys[row]
        im = frame(t).resize((cell_w, cell_h), Image.LANCZOS)
        canvas.paste(im, (x, y))
        d.rectangle([x, y, x + cell_w - 1, y + cell_h - 1], outline=(57, 118, 127), width=1)
        # corner ticks
        for cx, cy, dx, dy in [(x, y, 1, 1), (x + cell_w - 1, y, -1, 1), (x, y + cell_h - 1, 1, -1), (x + cell_w - 1, y + cell_h - 1, -1, -1)]:
            d.line([(cx, cy), (cx + dx * 22, cy)], fill=(76, 225, 232), width=2)
            d.line([(cx, cy), (cx, cy + dy * 22)], fill=(76, 225, 232), width=2)
        d.text((x, y + cell_h + 14), name, font=label_f, fill=(231, 250, 252))
        d.text((x, y + cell_h + 42), desc, font=sub_f, fill=(130, 170, 177))
        d.text((x + cell_w - 44, y + cell_h + 16), tc, font=small_f, fill=(255, 180, 92))

    d.line([(42, 1120), (W - 42, 1120)], fill=(43, 90, 98), width=1)
    d.text((42, 1140), "真实掌子面数据：RK4+970 实测样例  /  机器人与隧道为概念视觉示意  /  成果待地质复核",
           font=sub_f, fill=(113, 151, 159))
    d.text((W - 260, 1140), "GEO-SKETCH  /  2026", font=small_f, fill=(113, 151, 159))
    canvas.save(OUT / "storyboard.jpg", quality=92, subsampling=0, optimize=True)
    print("storyboard", OUT / "storyboard.jpg")


def copy_extras():
    for src, dst in [
        (ROOT / "audio" / "subtitles.srt", OUT / "subtitles.srt"),
        (ROOT / "audio" / "narration.json", OUT / "narration.json"),
        (ROOT / "README.md", OUT / "README.md"),
    ]:
        if src.exists():
            shutil.copy2(src, dst)
            print("copy", dst)


def main():
    make_poster()
    make_storyboard()
    copy_extras()


if __name__ == "__main__":
    main()

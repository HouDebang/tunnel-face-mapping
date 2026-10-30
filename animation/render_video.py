"""Deterministically render the 60 s animation and encode the final MP4."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
FRAMES = ROOT / "frames"
OUT = ROOT / "output"
FPS = 30
DUR = 60.0
W, H = 1920, 1080


def render(start: float, end: float, fps: int, clean: bool):
    FRAMES.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    if clean:
        for p in FRAMES.glob("f_*.jpg"):
            p.unlink()

    url = (ROOT / "index.html").as_uri()
    total = int(round((end - start) * fps))
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=True,
            args=[
                "--disable-gpu",
                "--force-device-scale-factor=1",
                "--hide-scrollbars",
                "--force-color-profile=srgb",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
            ],
        )
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url, wait_until="load")
        page.wait_for_function("window.ready === true", timeout=60000)

        t0 = time.time()
        for i in range(total):
            t = start + i / fps
            page.evaluate("(t) => window.seek(t)", t)
            page.screenshot(
                path=str(FRAMES / f"f_{i:05d}.jpg"),
                type="jpeg",
                quality=95,
                animations="disabled",
            )
            if (i + 1) % 90 == 0 or i == total - 1:
                elapsed = time.time() - t0
                rate = (i + 1) / max(elapsed, 1e-9)
                print(f"frames {i + 1}/{total}  {rate:.1f} fps  t={t:.2f}s", flush=True)
        browser.close()
        if errors:
            print("PAGE ERRORS:", errors, file=sys.stderr)


def encode(fps: int, out_name: str):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    audio = ROOT / "audio" / "soundtrack.wav"
    if not audio.exists():
        raise SystemExit("missing audio/soundtrack.wav; run make_audio.py first")
    out = OUT / out_name
    cmd = [
        ff, "-y", "-hide_banner",
        "-framerate", str(fps),
        "-start_number", "0",
        "-i", str(FRAMES / "f_%05d.jpg"),
        "-i", str(audio),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-vf", "scale=in_range=full:out_range=tv,format=yuv420p",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", "4.1",
        "-color_range", "tv",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        "-metadata", "title=隧道智能地质素描机器人 · 60s Concept Film",
        str(out),
    ]
    print("encoding:", out)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        print(p.stdout.decode("utf-8", "ignore")[-4000:], file=sys.stderr)
        raise SystemExit(p.returncode)
    print("wrote", out, f"{out.stat().st_size / 1e6:.1f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=DUR)
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--out", default="tunnel_geo_sketch_60s.mp4")
    ap.add_argument("--no-clean", action="store_true")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--encode-only", action="store_true")
    args = ap.parse_args()

    if not args.encode_only:
        render(args.start, args.end, args.fps, clean=not args.no_clean)
    if not args.render_only:
        encode(args.fps, args.out)


if __name__ == "__main__":
    main()

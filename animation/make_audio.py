"""Build the 60 s soundtrack and Chinese narration.

Narration uses the Microsoft Edge neural voice (Yunyang) and is placed on the
same timeline as the picture. The music/SFX bed is synthesized locally so the
film can be rebuilt offline after the voice files exist.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from scipy import signal
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "audio"
OUT.mkdir(exist_ok=True)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SR = 48000
DUR = 60.0

LINES = [
    (4.8,  "设备自主启动，实时构建隧道三维地图。"),
    (10.6, "系统规划路径，机器人自主前往掌子面测量点。"),
    (15.5, "履带式移动平台沿隧道行进，精准抵达目标位置。"),
    (27.9, "测量位置已到达，摄影测量装置开始多视角采集。"),
    (33.7, "现场影像重建为正射影像与三维纹理模型。"),
    (40.8, "自动提取岩性分区与结构面迹线。"),
    (47.9, "生成标准化掌子面素描图与成果表。"),
    (53.8, "从自主采集到智能素描，形成完整闭环。"),
]

VOICE = "zh-CN-YunyangNeural"
RATE = "-4%"
PITCH = "-2Hz"


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8", "ignore"), p.stderr.decode("utf-8", "ignore")


async def tts(text: str, path: Path):
    import edge_tts
    com = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH)
    await com.save(str(path))


def mp3_to_wav(mp3: Path, wav: Path):
    rc, _, err = run([FFMPEG, "-y", "-i", str(mp3), "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(wav)])
    if rc != 0:
        raise RuntimeError(err[-800:])


def duration_of(path: Path) -> float:
    rc, _, err = run([FFMPEG, "-i", str(path)])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", err)
    if not m:
        raise RuntimeError("cannot read duration: " + err[-300:])
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def env_ar(n, attack, release, sr=SR):
    a = max(1, int(attack * sr))
    r = max(1, int(release * sr))
    e = np.ones(n, dtype=np.float64)
    if a < n:
        e[:a] = np.linspace(0, 1, a) ** .6
    if r < n:
        e[-r:] *= np.linspace(1, 0, r) ** 1.4
    return e


def lowpass(x, cutoff, order=2):
    b, a = signal.butter(order, cutoff / (SR / 2), btype="low")
    return signal.lfilter(b, a, x)


def bandpass(x, lo, hi, order=2):
    b, a = signal.butter(order, [lo / (SR / 2), hi / (SR / 2)], btype="band")
    return signal.lfilter(b, a, x)


def make_bed():
    n = int(SR * DUR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(20261027)

    # very low tunnel room tone
    room = rng.normal(0, 1, n)
    room = lowpass(room, 120, 2) * .05
    room += lowpass(rng.normal(0, 1, n), 420, 2) * .012

    # slow evolving pad: D minor add9, very restrained
    pad = np.zeros(n)
    notes = [73.42, 110.0, 146.83, 220.0, 293.66]
    for i, f in enumerate(notes):
        lfo = .55 + .45 * np.sin(2 * np.pi * (.017 + i * .004) * t + i * 1.7)
        pad += np.sin(2 * np.pi * f * t + i * .9) * lfo * (.018 / (1 + i * .35))
    pad *= env_ar(n, 3.0, 4.0)

    # slow sub pulse, like a survey instrument
    pulse = np.zeros(n)
    for k in range(0, 60, 2):
        s = int(k * SR)
        ln = int(.9 * SR)
        if s + ln > n: break
        tt = np.arange(ln) / SR
        b = np.sin(2 * np.pi * 48 * tt) * np.exp(-tt * 5.2) * .055
        pulse[s:s + ln] += b

    # mechanical track rumble 14-23.5 s
    mech = np.zeros(n)
    s0, s1 = int(14 * SR), int(23.6 * SR)
    seg = rng.normal(0, 1, s1 - s0)
    seg = lowpass(seg, 95, 2) * .12
    tt = np.arange(s1 - s0) / SR
    trem = .55 + .45 * np.sin(2 * np.pi * 13.5 * tt)
    mech[s0:s1] = seg * trem
    mech[s0:s1] *= env_ar(s1 - s0, .8, 1.6)
    # track link clatter
    for k in np.arange(14.2, 23.4, .24):
        s = int(k * SR)
        ln = int(.09 * SR)
        if s + ln >= n: break
        tt = np.arange(ln) / SR
        mech[s:s + ln] += rng.normal(0, 1, ln) * np.exp(-tt * 55) * .035

    # servo whine on arrival 23.3-26.8
    servo = np.zeros(n)
    s0, s1 = int(23.3 * SR), int(26.9 * SR)
    tt = np.arange(s1 - s0) / SR
    f = 260 + 240 * np.clip(tt / 2.5, 0, 1)
    servo[s0:s1] = np.sin(2 * np.pi * np.cumsum(f) / SR) * .018 * env_ar(s1 - s0, .5, 1.3)
    servo[s0:s1] += np.sin(2 * np.pi * np.cumsum(f * 2.01) / SR) * .007 * env_ar(s1 - s0, .5, 1.3)

    # shutter clicks during photogrammetry
    shutter = np.zeros(n)
    for k in [28.7, 29.35, 30.0, 30.65, 31.3, 31.95, 32.6]:
        s = int(k * SR)
        ln = int(.22 * SR)
        if s + ln >= n: break
        tt = np.arange(ln) / SR
        click = rng.normal(0, 1, ln) * np.exp(-tt * 120)
        click += np.sin(2 * np.pi * 2100 * tt) * np.exp(-tt * 80) * .25
        shutter[s:s + ln] += click * .10

    # UI confirms
    beep = np.zeros(n)
    for k, f in [(10.2, 880), (12.5, 1320), (23.8, 990), (40.1, 760), (47.7, 1140), (53.1, 640)]:
        s = int(k * SR)
        ln = int(.55 * SR)
        if s + ln >= n: break
        tt = np.arange(ln) / SR
        b = (np.sin(2 * np.pi * f * tt) + .35 * np.sin(2 * np.pi * f * 2 * tt)) * np.exp(-tt * 8.5) * .035
        beep[s:s + ln] += b

    # final warm chord
    final = np.zeros(n)
    s0 = int(53.2 * SR)
    ln = n - s0
    tt = np.arange(ln) / SR
    for i, f in enumerate([65.41, 98.0, 130.81, 196.0, 261.63]):
        final[s0:] += np.sin(2 * np.pi * f * tt + i * .5) * (.022 / (1 + i * .4))
    final[s0:] *= env_ar(ln, 1.8, 4.5)

    # scene-transition air
    air = np.zeros(n)
    for k in [10.0, 27.0, 40.0, 53.0]:
        s = int((k - .35) * SR)
        ln = int(1.1 * SR)
        if s < 0 or s + ln >= n: continue
        tt = np.arange(ln) / SR
        air[s:s + ln] += bandpass(rng.normal(0, 1, ln), 200, 1800) * np.exp(-((tt - .35) / .25) ** 2) * .035

    bed = room + pad + pulse + mech + servo + shutter + beep + final + air
    # gentle bus compression / limiting
    bed = np.tanh(bed * 1.35) * .72
    bed *= env_ar(n, 1.2, 2.8)
    return bed


def add_reverb(x, sr=SR, mix=.10):
    rng = np.random.default_rng(7)
    ln = int(.55 * sr)
    tt = np.arange(ln) / sr
    ir = rng.normal(0, 1, ln) * np.exp(-tt * 9.0)
    ir = lowpass(ir, 4200, 1)
    ir[0] = 1.0
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    wet = signal.fftconvolve(x, ir, mode="full")[:len(x)]
    return x * (1 - mix) + wet * mix * 1.2


def main():
    lines = []
    for i, (start, text) in enumerate(LINES, 1):
        mp3 = OUT / f"vo_{i:02d}.mp3"
        wav = OUT / f"vo_{i:02d}.wav"
        if not mp3.exists():
            asyncio.run(tts(text, mp3))
        if not wav.exists() or wav.stat().st_mtime < mp3.stat().st_mtime:
            mp3_to_wav(mp3, wav)
        dur = duration_of(wav)
        lines.append({"index": i, "start": start, "duration": round(dur, 3), "text": text, "wav": wav.name})
        print(f"VO {i:02d}  start={start:5.1f}s  dur={dur:5.2f}s  end={start+dur:5.2f}s  {text}")

    n = int(SR * DUR)
    bed = make_bed()
    voice_bus = np.zeros(n)
    for item in lines:
        wav_path = OUT / item["wav"]
        sr, data = wavfile.read(wav_path)
        if data.ndim > 1:
            data = data.mean(axis=1)
        data = data.astype(np.float64) / 32768.0
        if sr != SR:
            # simple resample by linear interpolation
            x = np.linspace(0, 1, len(data), endpoint=False)
            y = np.linspace(0, 1, int(len(data) * SR / sr), endpoint=False)
            data = np.interp(y, x, data)
        data = add_reverb(data, mix=.07)
        # de-ess / soften hiss a little
        data = lowpass(data, 11500, 1)
        data *= env_ar(len(data), .04, .12)
        s = int(item["start"] * SR)
        e = min(n, s + len(data))
        voice_bus[s:e] += data[:e - s] * .88

    # duck the bed slightly under speech
    env = np.ones(n)
    for item in lines:
        s = int(item["start"] * SR)
        e = min(n, int((item["start"] + item["duration"] + .25) * SR))
        env[s:e] = np.minimum(env[s:e], .72)
    # smooth ducking envelope
    k = int(.18 * SR)
    env = signal.lfilter(np.ones(k) / k, [1], env)
    mix = bed * env + voice_bus
    mix = np.tanh(mix * 1.1) * .92
    mix *= env_ar(n, .6, 2.5)

    # stereo: small width on bed, centered voice
    left = mix.copy()
    right = mix.copy()
    # Haas-ish width only for the bed
    delay = int(.011 * SR)
    bed_st = np.stack([bed, np.concatenate([np.zeros(delay), bed[:-delay]])], axis=1)
    voice_st = np.stack([voice_bus, voice_bus], axis=1)
    out = bed_st * env[:, None] + voice_st
    out = np.tanh(out * 1.05) * .9
    out *= env_ar(n, .6, 2.5)[:, None]
    peak = float(np.max(np.abs(out))) + 1e-9
    out = out / peak * .92
    out16 = np.clip(out * 32767, -32768, 32767).astype(np.int16)
    wavfile.write(OUT / "soundtrack.wav", SR, out16)
    # stems for level checks / later remixing
    for name, stem in [("bed.wav", bed_st * env[:, None]), ("voice.wav", voice_st)]:
        st = np.tanh(stem * 1.05) * .9
        wavfile.write(OUT / name, SR, np.clip(st * 32767, -32768, 32767).astype(np.int16))

    # SRT from the same subtitle timings as the picture
    subs = [
        (4.7, 9.6, "设备自主启动，实时构建隧道三维地图。"),
        (10.4, 13.7, "根据地图规划路径，锁定掌子面测量点。"),
        (14.3, 22.9, "履带式移动平台自主行进，精准抵达目标位置。"),
        (23.6, 26.7, "测量位置已到达，摄影测量装置开始工作。"),
        (27.8, 32.9, "多视角采集掌子面影像与几何信息。"),
        (33.5, 39.3, "现场数据重建为正射影像与三维纹理模型。"),
        (40.5, 46.9, "自动提取岩性分区与结构面迹线。"),
        (47.6, 52.7, "生成标准化掌子面素描图与成果表。"),
        (53.5, 58.9, "从自主采集到智能素描，形成完整闭环。"),
    ]
    def ts(x):
        h = int(x // 3600); m = int((x % 3600) // 60); s = int(x % 60); ms = int((x - int(x)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    with (OUT / "subtitles.srt").open("w", encoding="utf-8") as f:
        for i, (a, b, text) in enumerate(subs, 1):
            f.write(f"{i}\n{ts(a)} --> {ts(b)}\n{text}\n\n")

    (OUT / "narration.json").write_text(json.dumps({
        "voice": VOICE, "rate": RATE, "pitch": PITCH,
        "duration": DUR, "lines": lines,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT / "soundtrack.wav")


if __name__ == "__main__":
    main()

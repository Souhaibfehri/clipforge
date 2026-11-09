"""Non-speech signals so Claude can find moments in sports, anime, movies and comedy:
audio peaks (crowd roar, laughter, explosions) and scene-cut density (action)."""
import re
import statistics
import subprocess
from pathlib import Path


def audio_levels(audio: Path) -> list[tuple[float, float]]:
    """RMS level in dB for every second of a 16 kHz mono wav."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(audio), "-af",
         "asetnsamples=n=16000,astats=metadata=1:reset=1,"
         "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    out, t = [], None
    for line in r.stdout.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"RMS_level=(-?[\d.]+|-inf)", line)
        if m and t is not None:
            v = m.group(1)
            out.append((t, -120.0 if v == "-inf" else float(v)))
    return out


def audio_peaks(levels, top=40, min_gap=12.0) -> list[dict]:
    if len(levels) < 10:
        return []
    vals = [v for _, v in levels if v > -100]
    if not vals:
        return []
    med = statistics.median(vals)
    # 3-second rolling mean to ignore single clicks
    smooth = []
    for i, (t, _) in enumerate(levels):
        win = [v for _, v in levels[max(0, i - 1): i + 2]]
        smooth.append((t, sum(win) / len(win)))
    cands = sorted(smooth, key=lambda x: -x[1])
    peaks = []
    for t, v in cands:
        if v - med < 6:
            break
        if all(abs(t - p["t"]) >= min_gap for p in peaks):
            peaks.append({"t": round(t, 1), "above_db": round(v - med, 1)})
        if len(peaks) >= top:
            break
    return sorted(peaks, key=lambda p: p["t"])


def scene_cuts(src: Path, max_seconds: float = 3 * 3600) -> list[float]:
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-t", str(max_seconds), "-i", str(src), "-an",
         "-vf", "fps=4,scale=256:-2,select='gt(scene,0.35)',showinfo", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [float(m.group(1)) for m in re.finditer(r"pts_time:([\d.]+)", r.stderr)]


def action_windows(cuts: list[float], total: float, window=30.0, top=15) -> list[dict]:
    if not cuts or total <= 0:
        return []
    n = int(total // window) + 1
    counts = [0] * n
    for c in cuts:
        counts[min(int(c // window), n - 1)] += 1
    med = statistics.median(counts) if counts else 0
    wins = [{"start": i * window, "end": min(total, (i + 1) * window), "cuts": c}
            for i, c in enumerate(counts) if c >= max(4, med * 1.8)]
    return sorted(wins, key=lambda w: -w["cuts"])[:top]


def describe(peaks, windows) -> str:
    lines = []
    if peaks:
        lines.append("Audio peaks (loud moments: crowd, laughter, impacts), time in seconds and dB above normal:")
        lines.append(", ".join(f"{p['t']}s(+{p['above_db']})" for p in peaks))
    if windows:
        lines.append("Fast-cutting action windows (scene cuts per 30s):")
        lines.append(", ".join(f"{w['start']:.0f}-{w['end']:.0f}s({w['cuts']} cuts)"
                               for w in sorted(windows, key=lambda w: w["start"])))
    return "\n".join(lines)

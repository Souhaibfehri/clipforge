"""ffmpeg / OpenCV work: download, layouts (face-track, split, fit), jump cuts, zoom, captions, voiceover."""
import json
import statistics
import subprocess
from pathlib import Path

import cv2

from . import config

OW, OH = config.OUT_W, config.OUT_H


class CmdError(RuntimeError):
    pass


def run(cmd: list[str], cwd: Path | None = None, timeout: int | None = None) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CmdError(f"{cmd[0]} timed out")
    if r.returncode != 0:
        tail = (r.stderr or r.stdout)[-1500:]
        raise CmdError(f"{cmd[0]} failed: {tail}")
    return r.stdout


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    w, h = (int(v["width"]), int(v["height"])) if v else (0, 0)
    rot = 0
    if v:
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = abs(int(sd["rotation"]))
        rot = rot or abs(int(v.get("tags", {}).get("rotate", 0) or 0))
    if rot in (90, 270):
        w, h = h, w
    return {"duration": float(data["format"].get("duration", 0) or 0), "width": w, "height": h,
            "has_audio": a is not None, "has_video": v is not None}


# ================================================================== yt-dlp

def _ytdlp_base():
    cmd = ["yt-dlp", "--no-warnings"]
    if config.YTDLP_COOKIES and Path(config.YTDLP_COOKIES).exists():
        cmd += ["--cookies", config.YTDLP_COOKIES]
    return cmd


def download(url: str, dest_dir: Path) -> tuple[Path, str]:
    cmd = _ytdlp_base() + [
        "--no-playlist", "--no-progress", "--restrict-filenames",
        "-f", f"bv*[height<={config.MAX_HEIGHT}]+ba/b[height<={config.MAX_HEIGHT}]/b",
        "--merge-output-format", "mp4", "-o", str(dest_dir / "source.%(ext)s"),
        "--print", "after_move:%(title)s", url,
    ]
    out = run(cmd, timeout=3 * 3600)
    title = out.strip().splitlines()[-1] if out.strip() else url
    files = [p for p in dest_dir.glob("source.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise CmdError("yt-dlp finished but no video file was found")
    return files[0], title


def latest_videos(source_url: str, n: int = 5) -> list[dict]:
    """Newest videos of a channel/playlist: [{"id", "url", "title"}]."""
    url = source_url.rstrip("/")
    if "youtube.com/@" in url and not url.endswith(("/videos", "/streams", "/shorts")):
        url += "/videos"
    out = run(_ytdlp_base() + ["--flat-playlist", "--playlist-end", str(n), "-J", url], timeout=300)
    data = json.loads(out)
    vids = []
    for e in data.get("entries") or []:
        if not e or not e.get("id"):
            continue
        vurl = e.get("url") or e.get("webpage_url") or ""
        if not vurl.startswith("http"):
            vurl = f"https://www.youtube.com/watch?v={e['id']}"
        vids.append({"id": e["id"], "url": vurl, "title": e.get("title") or e["id"]})
    return vids


def post_stats(post_url: str) -> dict:
    """Views/likes/comments of a published TikTok / Instagram / YouTube post."""
    out = run(_ytdlp_base() + ["--skip-download", "-J", post_url], timeout=120)
    d = json.loads(out)
    return {"views": int(d.get("view_count") or 0), "likes": int(d.get("like_count") or 0),
            "comments": int(d.get("comment_count") or 0), "shares": int(d.get("repost_count") or 0)}


def extract_audio(src: Path, out: Path) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(out)])


# ================================================================== face tracking

_MODELS = Path(__file__).parent / "models"
_yunet = None
_cascade = None


def _detect(small) -> list[tuple[int, int, int, int]]:
    """Faces in a 640px-wide BGR frame as (x, y, w, h), largest first. YuNet (neural), Haar as fallback."""
    global _yunet, _cascade
    h, w = small.shape[:2]
    if _yunet is None:
        try:
            _yunet = cv2.FaceDetectorYN.create(str(_MODELS / "face_detection_yunet_2023mar.onnx"), "", (w, h), 0.6)
        except Exception as e:
            print(f"YuNet unavailable, using Haar: {e}")
            _yunet = False
    if _yunet:
        _yunet.setInputSize((w, h))
        _, faces = _yunet.detect(small)
        boxes = [] if faces is None else [tuple(int(v) for v in f[:4]) for f in faces]
    else:
        if _cascade is None:
            _cascade = cv2.CascadeClassifier(str(_MODELS / "haarcascade_frontalface_default.xml"))
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        boxes = [tuple(int(v) for v in f) for f in _cascade.detectMultiScale(gray, 1.1, 6, minSize=(32, 32))]
    return sorted(boxes, key=lambda b: -b[2] * b[3])


def analyse_faces(src: Path, start: float, end: float, info: dict) -> dict:
    """Samples frames every 0.5s. Returns
    {"face_ratio": share of samples with a face, "track": [(t_rel, cx)], "pair": (left_cx, right_cx) | None}"""
    empty = {"face_ratio": 0.0, "track": [], "pair": None}
    try:
        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            return empty
        W = info["width"]
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        every = max(1, int(round(fps * 0.5)))
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        track, pairs, n, frame_i, t = [], [], 0, 0, start
        while t < end:
            if frame_i % every == 0:
                ok, frame = cap.read()
                if not ok:
                    break
                pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                t = pos if pos > 0 else t
                n += 1
                fh, fw = frame.shape[:2]
                scale = 640.0 / fw
                small = cv2.resize(frame, (640, int(fh * scale)))
                faces = _detect(small)
                to_src = W / fw / scale
                if len(faces):
                    x, y, w, h = faces[0]
                    track.append((t - start, (x + w / 2) * to_src))
                    if len(faces) > 1:
                        x2, y2, w2, h2 = faces[1]
                        if w2 * h2 >= 0.35 * w * h:
                            fa = ((x + w / 2) * to_src, (y + h / 2) * to_src, w * to_src)
                            fb = ((x2 + w2 / 2) * to_src, (y2 + h2 / 2) * to_src, w2 * to_src)
                            fa, fb = sorted([fa, fb])
                            if fb[0] - fa[0] > 0.3 * W:
                                pairs.append((*fa, *fb))
                else:
                    track.append((t - start, None))
            else:
                if not cap.grab():
                    break
                t += 1.0 / fps
            frame_i += 1
        cap.release()
        known = [c for _, c in track if c is not None]
        ratio = len(known) / max(1, n)
        pair = None
        if n and len(pairs) / n >= 0.5:
            pair = tuple(statistics.median(p[i] for p in pairs) for i in range(6))  # ax, ay, aw, bx, by, bw
        return {"face_ratio": ratio, "track": track, "pair": pair}
    except Exception as e:
        print(f"face analysis failed: {e}")
        return empty


def camera_path(track, W, crop_w) -> list[tuple[float, int]]:
    """Piecewise-constant crop x positions: only 'cut' the camera when the subject really moves."""
    center_x = (W - crop_w) // 2
    known = [c for _, c in track if c is not None]
    if len(known) < 2:
        return [(0.0, center_x)]
    filled, last = [], known[0]
    for tt, cx in track:
        last = cx if cx is not None else last
        filled.append((tt, last))
    smooth = [(tt, statistics.median(c for _, c in filled[max(0, i - 3): i + 4])) for i, (tt, _) in enumerate(filled)]
    threshold, hold = 0.10 * W, 3
    segs = [(0.0, smooth[0][1])]
    i = 0
    while i < len(smooth):
        tt, cx = smooth[i]
        if abs(cx - segs[-1][1]) > threshold:
            window = smooth[i: i + hold]
            if len(window) == hold and all(abs(c - segs[-1][1]) > threshold for _, c in window):
                segs.append((tt, statistics.median(c for _, c in window)))
                i += hold
                continue
        i += 1
    clamp = lambda cx: int(min(max(cx - crop_w / 2, 0), W - crop_w))
    return [(round(tt, 2), clamp(cx)) for tt, cx in segs[:40]]


def _piecewise(points, var="t") -> str:
    expr = str(points[-1][1])
    for k in range(len(points) - 1, 0, -1):
        expr = f"if(lt({var},{points[k][0]}),{points[k - 1][1]},{expr})"
    return expr


def resolve_layout(wanted: str, info: dict, faces: dict) -> str:
    W, H = info["width"], info["height"]
    aspect = W / max(H, 1)
    if aspect <= 0.62:
        return "vertical"
    if aspect < 1.2:
        return "fit"
    if wanted == "fit":
        return "fit"
    if wanted in ("split", "auto") and faces.get("pair"):
        return "split"
    if wanted in ("face", "split"):
        return "face"
    return "face" if faces.get("face_ratio", 0) >= 0.2 else "fit"


# ================================================================== render

def _layout_filter(layout: str, info: dict, faces: dict) -> str:
    """Input [0:v] -> output [base] at 1080x1920, 30 fps."""
    W, H = info["width"], info["height"]
    H2 = H // 2 * 2
    if layout == "face":
        crop_w = int(H * 9 / 16) // 2 * 2
        x = _piecewise(camera_path(faces.get("track", []), W, crop_w))
        return f"[0:v]fps=30,crop=w={crop_w}:h={H2}:x='{x}':y=0,scale={OW}:{OH}:flags=lanczos,setsar=1[base]"
    if layout == "split":
        ph = OH // 2
        ax, ay, aw, bx, by, bw = faces["pair"]
        # each panel frames one face at ~4.5x its width (head and shoulders), never wider than half the frame
        crop_w = int(min(W * 0.5, H * OW / ph, max(aw, bw) * 4.5)) // 2 * 2
        crop_w = max(crop_w, int(W * 0.25) // 2 * 2)
        crop_h = int(crop_w * ph / OW) // 2 * 2
        if crop_h > H:
            crop_h = H2
            crop_w = int(crop_h * OW / ph) // 2 * 2

        def box(cx, cy):
            x = int(min(max(cx - crop_w / 2, 0), W - crop_w))
            y = int(min(max(cy - crop_h * 0.42, 0), H - crop_h))
            return x, y

        (xa, ya), (xb, yb) = box(ax, ay), box(bx, by)
        return (f"[0:v]fps=30,split[sa][sb];"
                f"[sa]crop={crop_w}:{crop_h}:{xa}:{ya},scale={OW}:{ph}:flags=lanczos[ta];"
                f"[sb]crop={crop_w}:{crop_h}:{xb}:{yb},scale={OW}:{ph}:flags=lanczos[tb];"
                f"[ta][tb]vstack,setsar=1[base]")
    if layout == "vertical":
        return (f"[0:v]fps=30,scale={OW}:{OH}:force_original_aspect_ratio=increase,"
                f"crop={OW}:{OH},setsar=1[base]")
    # fit: full picture on a blurred, darkened copy of itself
    return (f"[0:v]fps=30,split[fa][fb];"
            f"[fa]scale={OW // 4}:{OH // 4}:force_original_aspect_ratio=increase,crop={OW // 4}:{OH // 4},"
            f"boxblur=10:2,scale={OW}:{OH},eq=brightness=-0.12[bg];"
            f"[fb]scale={OW}:-2:flags=lanczos[fg];"
            f"[bg][fg]overlay=0:(H-h)/2-140,setsar=1[base]")


def render_piece(src: Path, info: dict, layout: str, faces: dict, win_start: float, win_dur: float,
                 keep: list, zooms: list, ass_file: Path | None, out: Path, progress: bool, final: bool) -> None:
    """Render one continuous piece: window of the source -> layout -> zoom -> jump cuts -> captions."""
    chain = [_layout_filter(layout, info, faces)]
    cur = "base"
    if zooms:
        z = "+".join(f"between(it,{a},{b})" for a, b in zooms)
        chain.append(f"[{cur}]zoompan=z='if({z},1.12,1)':x='iw/2-(iw/zoom/2)':y='ih*0.4-(ih/zoom*0.4)'"
                     f":d=1:s={OW}x{OH}:fps=30[zm]")
        cur = "zm"
    single = len(keep) == 1 and keep[0][0] <= 0.01
    if not single:
        sel = "+".join(f"between(t,{a},{b})" for a, b in keep)
        chain.append(f"[{cur}]select='{sel}',setpts=N/FRAME_RATE/TB[cut]")
        cur = "cut"
    if ass_file is not None:
        chain.append(f"[{cur}]ass={ass_file.name}[subs]")
        cur = "subs"
    out_dur = sum(b - a for a, b in keep)
    inputs = ["-ss", f"{win_start:.3f}", "-t", f"{win_dur:.3f}", "-i", str(src.resolve())]
    if progress:
        inputs += ["-f", "lavfi", "-i", "color=c=0xFFD23F:s=1080x14:r=30"]
        chain.append(f"[{cur}][1:v]overlay=x='-W+W*t/{out_dur:.3f}':y=H-h:shortest=1[pb]")
        cur = "pb"
    if info["has_audio"]:
        if single:
            chain.append(f"[0:a]aresample=48000,atrim=0:{out_dur:.3f}[aout]")
        else:
            asel = "+".join(f"between(t,{a},{b})" for a, b in keep)
            chain.append(f"[0:a]aresample=48000,aselect='{asel}',asetpts=N/SR/TB[aout]")
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chain), "-map", f"[{cur}]"]
    if info["has_audio"]:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    crf = config.X264_CRF if final else "16"
    cmd += ["-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", crf, "-pix_fmt", "yuv420p",
            "-r", "30", "-t", f"{out_dur:.3f}", "-movflags", "+faststart", out.name]
    run(cmd, cwd=out.parent, timeout=3600)


def join_pieces(pieces: list[Path], out: Path, progress: bool, has_audio: bool) -> None:
    """Concatenate cold open + body, adding the progress bar over the whole thing."""
    total = sum(probe(p)["duration"] for p in pieces)
    n = len(pieces)
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for p in pieces:
        cmd += ["-i", str(p)]
    streams = "".join(f"[{i}:v][{i}:a]" if has_audio else f"[{i}:v]" for i in range(n))
    fc = f"{streams}concat=n={n}:v=1:a={1 if has_audio else 0}[v]" + ("[a]" if has_audio else "")
    cur = "v"
    if progress:
        cmd += ["-f", "lavfi", "-i", "color=c=0xFFD23F:s=1080x14:r=30"]
        fc += f";[v][{n}:v]overlay=x='-W+W*t/{total:.3f}':y=H-h:shortest=1[pb]"
        cur = "pb"
    cmd += ["-filter_complex", fc, "-map", f"[{cur}]"]
    if has_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", config.X264_CRF,
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    run(cmd, timeout=3600)


def thumbnail(video: Path, out: Path, at: float) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=360:-2", "-q:v", "4", str(out)])


# ================================================================== voiceover

def mix_voiceover(clip: Path, voice: Path, out: Path, offset: float = 0.0,
                  voice_gain_db: float = 2.0, duck: float = 0.85) -> None:
    """Mix the creator's voiceover over a clip; original audio is ducked under the voice.
    If the voiceover runs past the end of the clip, the last frame is held."""
    c = probe(clip)
    v = probe(voice)
    if not v["has_audio"]:
        raise CmdError("The voiceover file has no audio track")
    extra = max(0.0, offset + v["duration"] - c["duration"] + 0.3)
    ratio = 1 + duck * 19
    delay_ms = int(offset * 1000)
    voice_chain = (f"[1:a]aformat=channel_layouts=stereo,aresample=48000,highpass=f=80,"
                   f"loudnorm=I=-16:TP=-1.5:LRA=11,volume={voice_gain_db}dB,"
                   f"adelay={delay_ms}|{delay_ms},apad[vo];[vo]asplit[vo1][vo2];")
    if c["has_audio"]:
        audio = (voice_chain +
                 f"[0:a]aformat=channel_layouts=stereo,aresample=48000,apad[orig];"
                 f"[orig][vo1]sidechaincompress=threshold=0.015:ratio={ratio:.1f}:attack=15:release=450:makeup=1[ducked];"
                 f"[ducked][vo2]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
    else:
        audio = voice_chain + "[vo1]anull[a];[vo2]anullsink"
    total = c["duration"] + extra
    if extra > 0:
        fc = f"[0:v]tpad=stop_mode=clone:stop_duration={extra:.2f}[v];" + audio
        vmap = ["-map", "[v]", "-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", config.X264_CRF,
                "-pix_fmt", "yuv420p"]
    else:
        fc = audio
        vmap = ["-map", "0:v", "-c:v", "copy"]
    run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-i", str(voice), "-filter_complex", fc,
         *vmap, "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
         "-movflags", "+faststart", str(out)])

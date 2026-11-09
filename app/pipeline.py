"""Full job: source -> transcript + signals -> Claude picks moments -> pro-edited vertical clips."""
import json
import traceback
from pathlib import Path

from . import captions, config, db, edit, llm, presets, signals, transcribe, video


def channel_settings(job: dict) -> tuple[dict, str, str]:
    ch = db.get_channel(job.get("channel_id"))
    niche = ch["niche"] if ch else "podcast"
    settings = presets.settings_for(niche, ch["settings"] if ch else None)
    for k in ("num_clips", "angle", "commentary_language"):
        if job["options"].get(k):
            settings[k] = job["options"][k]
    name = ch["name"] if ch else "Default"
    return settings, niche, name


def _status(job_id, status, message):
    db.update_job(job_id, status=status, message=message)


def _words_abs(transcript, start, end):
    out = []
    for s in transcript.get("segments", []):
        if s["end"] < start or s["start"] > end:
            continue
        out.extend(w for w in s.get("words", []) if w["end"] > start and w["start"] < end)
    return out


def _snap(transcript, start, end, total):
    words = [w for s in transcript.get("segments", []) for w in s.get("words", [])]
    near = [w for w in words if abs(w["start"] - start) < 3 or abs(w["end"] - end) < 3]
    if near:
        first = min(words, key=lambda w: abs(w["start"] - start))
        last = min(words, key=lambda w: abs(w["end"] - end))
        if abs(first["start"] - start) < 3:
            start = first["start"]
        if abs(last["end"] - end) < 3:
            end = last["end"]
    return round(max(0.0, start - 0.15), 3), round(min(total, end + 0.35), 3)


def clean_candidates(raw, transcript, total, n, min_s, max_s) -> list[dict]:
    picked = []
    for c in sorted(raw, key=lambda c: -float(c.get("score", 0) or 0)):
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e <= s or s >= total:
            continue
        s, e = _snap(transcript, s, e, total)
        if e - s < min_s * 0.6:
            continue
        if e - s > max_s * 1.25:
            e = s + max_s
        if any(s < p["end"] and e > p["start"] for p in picked):
            continue
        co = c.get("cold_open")
        cold = None
        if isinstance(co, dict):
            try:
                cs, ce = float(co["start"]), float(co["end"])
                if s + 1.0 <= cs < ce <= e and 1.0 <= ce - cs <= 5.0:
                    cold = {"start": round(cs, 3), "end": round(ce, 3)}
            except (KeyError, TypeError, ValueError):
                pass
        tags = c.get("hashtags") or []
        if isinstance(tags, str):
            tags = tags.split()
        picked.append({
            "n": len(picked) + 1, "start": s, "end": e, "duration": round(e - s, 2), "cold_open": cold,
            "hook": str(c.get("hook", "")).strip(), "title": str(c.get("title", "")).strip(),
            "description": str(c.get("description", "")).strip(),
            "hashtags": [t if t.startswith("#") else f"#{t}" for t in map(str, tags)][:8],
            "commentary_script": str(c.get("commentary_script", "")).strip(),
            "score": int(float(c.get("score", 0) or 0)), "reason": str(c.get("reason", "")).strip(),
            "status": "pending", "file": None, "thumb": None, "final": None,
        })
        if len(picked) >= n:
            break
    return picked


def _shift_faces(faces, offset, dur):
    track = [(t - offset, c) for t, c in faces.get("track", []) if offset <= t <= offset + dur]
    return {**faces, "track": track}


def render_clip(job_id: str, clip: dict, src: Path, info: dict, transcript: dict, settings: dict) -> dict:
    d = db.job_dir(job_id) / "clips"
    d.mkdir(exist_ok=True)
    tag = f"c{clip['n']:02d}"
    start, end = clip["start"], clip["end"]
    words = _words_abs(transcript, start, end)

    wanted = settings.get("layout", "auto")
    faces = {"face_ratio": 0, "track": [], "pair": None}
    if wanted != "fit" and info["width"] / max(info["height"], 1) >= 1.2:
        faces = video.analyse_faces(src, start, end, info)
    layout = video.resolve_layout(wanted, info, faces)

    keep = edit.keep_segments(words, start, end, bool(settings.get("remove_silences")))
    zooms = edit.zoom_ranges(words, start, keep) if settings.get("punch_zoom") and layout in ("face", "vertical") else []
    out_words = edit.map_words(words, start, keep)
    body_dur = edit.out_duration(keep)
    style = settings.get("caption_style", "bold")
    progress = bool(settings.get("progress_bar", True))
    cta = settings.get("cta", "")
    out = d / f"{tag}.mp4"

    cold = clip.get("cold_open") if settings.get("cold_open", True) else None
    if cold:
        cs, ce = cold["start"], cold["end"]
        co_words = [dict(w, start=w["start"] - cs, end=w["end"] - cs) for w in _words_abs(transcript, cs, ce)]
        co_ass = d / f"{tag}_cold.ass"
        co_ass.write_text(captions.build_ass(co_words, ce - cs, clip.get("hook", ""), style, layout), encoding="utf-8")
        body_ass = d / f"{tag}_body.ass"
        body_ass.write_text(captions.build_ass(out_words, body_dur, "", style, layout, cta), encoding="utf-8")
        p1, p2 = d / f"{tag}_p1.mp4", d / f"{tag}_p2.mp4"
        video.render_piece(src, info, layout, _shift_faces(faces, cs - start, ce - cs), cs, ce - cs,
                           [(0.0, round(ce - cs, 3))], [], co_ass, p1, progress=False, final=False)
        video.render_piece(src, info, layout, faces, start, end - start, keep, zooms, body_ass, p2,
                           progress=False, final=False)
        video.join_pieces([p1, p2], out, progress, info["has_audio"])
        for p in (p1, p2):
            p.unlink(missing_ok=True)
    else:
        ass = d / f"{tag}.ass"
        ass.write_text(captions.build_ass(out_words, body_dur, clip.get("hook", ""), style, layout, cta), encoding="utf-8")
        video.render_piece(src, info, layout, faces, start, end - start, keep, zooms, ass, out,
                           progress=progress, final=True)

    out_info = video.probe(out)
    thumb = d / f"{tag}.jpg"
    video.thumbnail(out, thumb, min(4.0, out_info["duration"] / 2))
    return {"status": "ready", "file": f"clips/{tag}.mp4", "thumb": f"clips/{tag}.jpg", "final": None,
            "layout": layout, "out_duration": round(out_info["duration"], 2), "error": None,
            "jump_cuts": len(keep) - 1, "caption_style": style}


def find_source(job_id: str) -> Path | None:
    files = [p for p in db.job_dir(job_id).glob("source.*") if p.suffix not in (".part", ".ytdl")]
    return files[0] if files else None


def winners_for(channel_id) -> list[str]:
    if not channel_id:
        return []
    ins = db.latest_insight(channel_id)
    hooks = list((ins or {}).get("data", {}).get("winning_hooks", []))
    rows = db.q("SELECT c.data, MAX(p.views) AS v FROM posts p JOIN clips c ON c.id = p.clip_id "
                "WHERE p.channel_id=? AND p.views > 0 GROUP BY c.id ORDER BY v DESC LIMIT 5", (channel_id,))
    for r in rows:
        h = json.loads(r["data"]).get("hook")
        if h:
            hooks.append(f"{h} ({r['v']} views)")
    return list(dict.fromkeys(hooks))


def load_transcript(job_id):
    p = db.job_dir(job_id) / "transcript.json"
    return json.loads(p.read_text()) if p.exists() else {"segments": []}


def process(job_id: str) -> None:
    job = db.get_job(job_id)
    d = db.job_dir(job_id)
    try:
        settings, niche, _ = channel_settings(job)
        src = find_source(job_id)
        if src is None:
            if job["source"]["type"] != "url":
                raise RuntimeError("Uploaded file is missing")
            _status(job_id, "downloading", "Downloading the video")
            src, title = video.download(job["source"]["url"], d)
            db.update_job(job_id, title=title)
        info = video.probe(src)
        if not info["has_video"]:
            raise RuntimeError("This file has no video track")
        db.update_job(job_id, duration=info["duration"])

        tpath, spath = d / "transcript.json", d / "signals.json"
        audio = d / "audio.wav"
        if not tpath.exists() or not spath.exists():
            if info["has_audio"]:
                video.extract_audio(src, audio)
            if not tpath.exists():
                if info["has_audio"]:
                    _status(job_id, "transcribing", "Transcribing speech (slowest step)")
                    transcribe.transcribe(audio, tpath, job["options"].get("language") or None)
                else:
                    tpath.write_text(json.dumps({"segments": []}))
            if not spath.exists():
                _status(job_id, "analysing", "Finding loud moments and action")
                peaks = signals.audio_peaks(signals.audio_levels(audio)) if audio.exists() else []
                transcript_tmp = json.loads(tpath.read_text())
                need_scenes = settings.get("layout") == "fit" or not transcript_tmp.get("segments")
                windows = signals.action_windows(signals.scene_cuts(src), info["duration"]) if need_scenes else []
                spath.write_text(json.dumps({"peaks": peaks, "windows": windows}))
            audio.unlink(missing_ok=True)
        transcript = json.loads(tpath.read_text())
        sig = json.loads(spath.read_text())
        sig_text = signals.describe(sig["peaks"], sig["windows"])
        if not transcript.get("segments") and not sig_text:
            raise RuntimeError("No speech and no usable signals found in this video")

        if not db.job_clips(job_id):
            _status(job_id, "selecting", "Claude is picking the best moments")
            n = int(settings.get("num_clips") or config.DEFAULT_NUM_CLIPS)
            raw = llm.select_clips(transcript, info["duration"], n, niche, presets.brief_for(niche), settings,
                                   sig_text, winners_for(job.get("channel_id")))
            clips = clean_candidates(raw, transcript, info["duration"], n,
                                     settings.get("min_s", 20), settings.get("max_s", 60))
            if not clips:
                raise RuntimeError("Claude did not return any usable moment")
            for c in clips:
                db.add_clip(job_id, job.get("channel_id"), c)

        todo = [c for c in db.job_clips(job_id) if c["status"] != "ready"]
        for i, clip in enumerate(todo, 1):
            _status(job_id, "rendering", f"Editing clip {i} of {len(todo)}")
            db.update_clip(clip["id"], status="rendering")
            try:
                db.update_clip(clip["id"], **render_clip(job_id, clip, src, info, transcript, settings))
            except Exception as e:
                traceback.print_exc()
                db.update_clip(clip["id"], status="error", error=str(e)[-600:])
        clips = db.job_clips(job_id)
        ok = sum(1 for c in clips if c["status"] == "ready")
        if ok == 0:
            raise RuntimeError("No clip could be rendered: " + next((c.get("error") or "" for c in clips), ""))
        _status(job_id, "done", f"{ok} of {len(clips)} clips ready")

        from . import poster  # auto-schedule into the channel's posting slots
        poster.auto_schedule(job_id)
    except Exception as e:
        traceback.print_exc()
        db.update_job(job_id, status="error", error=str(e)[-1500:], message="Failed")

import base64
import datetime as dt
import secrets
import shutil
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import config, db, pipeline, poster, presets, tracker, video, watcher, worker

STATIC = Path(__file__).parent / "static"
ALLOWED_VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
ALLOWED_AUDIO = {".webm", ".m4a", ".mp4", ".mp3", ".wav", ".ogg", ".aac", ".caf", ".opus"}
RUNNING = ("queued", "downloading", "transcribing", "analysing", "selecting", "rendering")


@asynccontextmanager
async def lifespan(app):
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    db.conn()
    worker.start()
    yield


app = FastAPI(title="ClipForge", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not config.APP_PASSWORD or request.url.path == "/api/health":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
            if secrets.compare_digest(user, config.APP_USER) and secrets.compare_digest(pwd, config.APP_PASSWORD):
                return await call_next(request)
        except Exception:
            pass
    return Response("Login required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="ClipForge"'})


@app.exception_handler(ValueError)
async def bad_value(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


def _bad(msg, code=400):
    raise HTTPException(code, msg)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def get_config():
    return {
        "claude_ready": bool(config.ANTHROPIC_API_KEY),
        "posting_ready": poster.enabled(),
        "transcriber": config.TRANSCRIBE_PROVIDER,
        "queue": worker.queue_size(),
        "presets": {k: {"label": v["label"], "defaults": presets.settings_for(k)} for k, v in presets.PRESETS.items()},
        "caption_styles": ["bold", "karaoke", "boxed", "minimal", "none"],
        "layouts": ["auto", "face", "split", "fit"],
    }


@app.get("/api/overview")
def overview():
    return tracker.overview()


# ================================================================== channels

def _channel_or_404(cid):
    ch = db.get_channel(cid)
    if not ch:
        _bad("Channel not found", 404)
    return ch


def _clean_settings(niche, incoming: dict) -> dict:
    s = presets.settings_for(niche, incoming)
    for k in ("num_clips", "min_s", "max_s"):
        s[k] = int(s[k])
    s["num_clips"] = max(1, min(s["num_clips"], 20))
    s["min_s"] = max(5, min(s["min_s"], 170))
    s["max_s"] = max(s["min_s"] + 5, min(s["max_s"], 180))
    s["cpm"] = float(s.get("cpm") or 0)
    s["budget"] = float(s.get("budget") or 0)
    if isinstance(s.get("sources"), str):
        s["sources"] = [x.strip() for x in s["sources"].splitlines() if x.strip()]
    if isinstance(s.get("slots"), str):
        s["slots"] = [x.strip() for x in s["slots"].replace(",", " ").split() if x.strip()]
    if s.get("layout") not in ("auto", "face", "split", "fit"):
        s["layout"] = "auto"
    return s


@app.get("/api/channels")
def list_channels():
    return [dict(ch, summary=tracker.channel_summary(ch)) for ch in db.list_channels()]


@app.post("/api/channels")
def create_channel(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    niche = payload.get("niche") or "podcast"
    if not name:
        _bad("Give the channel a name")
    if niche not in presets.PRESETS:
        _bad("Unknown niche")
    ch = db.create_channel(name, niche, _clean_settings(niche, payload.get("settings") or {}))
    return ch


@app.get("/api/channels/{cid}")
def get_channel(cid: int):
    ch = _channel_or_404(cid)
    return {"channel": ch, "stats": tracker.channel_stats(ch), "jobs": db.list_jobs(cid, limit=50)}


@app.put("/api/channels/{cid}")
def update_channel(cid: int, payload: dict = Body(...)):
    ch = _channel_or_404(cid)
    niche = payload.get("niche") or ch["niche"]
    merged = {**ch["settings"], **(payload.get("settings") or {})}
    s = _clean_settings(niche, merged)
    s["sources_initialized"] = ch["settings"].get("sources_initialized", [])
    if "backfill" in merged:
        s["backfill"] = int(merged["backfill"] or 0)
    return db.update_channel(cid, (payload.get("name") or ch["name"]).strip(), niche, s)


@app.delete("/api/channels/{cid}")
def delete_channel(cid: int):
    _channel_or_404(cid)
    db.ex("UPDATE jobs SET channel_id=NULL WHERE channel_id=?", (cid,))
    db.ex("UPDATE clips SET channel_id=NULL WHERE channel_id=?", (cid,))
    db.ex("DELETE FROM posts WHERE channel_id=? AND status='queued'", (cid,))
    db.delete_channel(cid)
    return {"ok": True}


@app.post("/api/channels/{cid}/connect")
def connect_accounts(cid: int):
    try:
        return poster.connect_link(_channel_or_404(cid))
    except RuntimeError as e:
        _bad(str(e))


@app.get("/api/channels/{cid}/accounts")
def channel_accounts(cid: int):
    ch = _channel_or_404(cid)
    try:
        return {"user": ch["settings"].get("uploadpost_user"), "accounts": poster.connected_accounts(ch["settings"].get("uploadpost_user"))}
    except Exception as e:
        return {"user": ch["settings"].get("uploadpost_user"), "accounts": {}, "error": str(e)}


@app.post("/api/channels/{cid}/watch-now")
def watch_now(cid: int):
    created = watcher.check_channel(_channel_or_404(cid), worker.enqueue)
    return {"created": created}


@app.post("/api/channels/{cid}/insights")
async def channel_insights(cid: int):
    ch = _channel_or_404(cid)
    try:
        return await run_in_threadpool(tracker.generate_insights, ch)
    except RuntimeError as e:
        _bad(str(e))


# ================================================================== jobs

def _job_or_404(job_id):
    try:
        job = db.get_job(job_id)
    except ValueError:
        job = None
    if not job:
        _bad("Job not found", 404)
    return job


@app.get("/api/jobs")
def list_jobs(channel_id: int | None = None):
    out = []
    for j in db.list_jobs(channel_id, limit=200):
        clips = db.job_clips(j["id"])
        out.append(dict(j, clips=len(clips), ready=sum(1 for c in clips if c["status"] == "ready"),
                        thumb=next((c["thumb"] for c in clips if c.get("thumb")), None)))
    return out


@app.post("/api/jobs")
async def create_jobs(
    channel_id: int = Form(...),
    urls: str = Form(""),
    file: UploadFile | None = File(None),
    num_clips: int = Form(0),
    angle: str = Form(""),
    commentary_language: str = Form(""),
    language: str = Form(""),
):
    _channel_or_404(channel_id)
    options = {"angle": angle.strip(), "commentary_language": commentary_language.strip(), "language": language.strip()}
    if num_clips:
        options["num_clips"] = max(1, min(int(num_clips), 20))
    links = [u.strip() for u in urls.replace(",", "\n").splitlines() if u.strip()]
    created = []
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_VIDEO:
            _bad(f"Unsupported video type {ext}")
        job = db.new_job({"type": "upload", "filename": file.filename}, options, channel_id)
        with (db.job_dir(job["id"]) / f"source{ext}").open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
        created.append(job)
    for u in links:
        if not u.startswith(("http://", "https://")):
            _bad(f"Not a link: {u[:60]}")
    for u in links[:50]:
        created.append(db.new_job({"type": "url", "url": u}, options, channel_id))
    if not created:
        _bad("Paste at least one link or upload a video")
    for j in created:
        worker.enqueue(j["id"])
    return {"jobs": created}


def _clip_with_posts(c):
    return dict(c, posts=db.clip_posts(c["id"]))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = _job_or_404(job_id)
    return dict(job, clips=[_clip_with_posts(c) for c in db.job_clips(job_id)],
                channel=db.get_channel(job.get("channel_id")))


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    job = _job_or_404(job_id)
    if job["status"] in RUNNING:
        _bad("Job is still running", 409)
    db.update_job(job_id, status="queued", message="Retrying", error=None)
    worker.enqueue(job_id)
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = _job_or_404(job_id)
    if job["status"] in RUNNING:
        _bad("Wait until the job finishes before deleting it", 409)
    db.delete_job(job_id)
    shutil.rmtree(db.job_dir(job_id), ignore_errors=True)
    return {"ok": True}


# ================================================================== clips

def _clip_or_404(clip_id):
    c = db.get_clip(clip_id)
    if not c:
        _bad("Clip not found", 404)
    return c


def _rerender(clip, **changes):
    job = db.get_job(clip["job_id"])
    src = pipeline.find_source(clip["job_id"])
    if src is None:
        _bad("Source video is gone, can't re-render", 409)
    settings, _, _ = pipeline.channel_settings(job)
    clip = dict(clip, **changes)
    try:
        result = pipeline.render_clip(clip["job_id"], clip, src, video.probe(src), pipeline.load_transcript(clip["job_id"]), settings)
    except video.CmdError as e:
        _bad(str(e), 500)
    return db.update_clip(clip["id"], **changes, **result)


@app.post("/api/clips/{clip_id}/hook")
async def edit_hook(clip_id: str, hook: str = Form("")):
    clip = _clip_or_404(clip_id)
    return _clip_with_posts(await run_in_threadpool(_rerender, clip, hook=hook.strip()[:120]))


@app.post("/api/clips/{clip_id}/rerender")
async def rerender(clip_id: str):
    """Re-render with the channel's current settings (after changing styles, layout, etc.)."""
    clip = _clip_or_404(clip_id)
    return _clip_with_posts(await run_in_threadpool(_rerender, clip))


@app.post("/api/clips/{clip_id}/text")
def edit_text(clip_id: str, payload: dict = Body(...)):
    _clip_or_404(clip_id)
    fields = {k: payload[k] for k in ("title", "description", "commentary_script") if k in payload}
    if "hashtags" in payload:
        tags = payload["hashtags"]
        fields["hashtags"] = tags.split() if isinstance(tags, str) else tags
    return _clip_with_posts(db.update_clip(clip_id, **fields))


@app.post("/api/clips/{clip_id}/voiceover")
async def add_voiceover(clip_id: str, file: UploadFile = File(...), offset: float = Form(0.0),
                        voice_gain_db: float = Form(2.0), duck: float = Form(0.85)):
    clip = _clip_or_404(clip_id)
    if clip["status"] != "ready":
        _bad("Clip isn't rendered yet", 409)
    d = db.job_dir(clip["job_id"]) / "clips"
    tag = Path(clip["file"]).stem
    ext = Path(file.filename or "voice.webm").suffix.lower()
    ext = ext if ext in ALLOWED_AUDIO else ".webm"
    voice = d / f"{tag}_voice{ext}"
    with voice.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    out = d / f"{tag}_final.mp4"
    try:
        await run_in_threadpool(video.mix_voiceover, d / f"{tag}.mp4", voice, out, max(0.0, offset),
                                max(-10.0, min(voice_gain_db, 12.0)), max(0.0, min(duck, 1.0)))
    except video.CmdError as e:
        _bad(str(e), 500)
    db.update_clip(clip_id, final=f"clips/{tag}_final.mp4")
    poster.maybe_auto_schedule(clip_id)
    return _clip_with_posts(db.get_clip(clip_id))


@app.post("/api/clips/{clip_id}/schedule")
def schedule(clip_id: str, payload: dict = Body(default={})):
    clip = _clip_or_404(clip_id)
    if not poster.enabled():
        _bad("Auto-posting needs UPLOADPOST_API_KEY in .env. You can still post manually and add the links.")
    when = None
    if payload.get("when"):
        try:
            t = dt.datetime.fromisoformat(payload["when"])
        except ValueError:
            _bad("Bad date")
        if t.tzinfo is None:  # a time picked on the phone means the channel's local time, not the server's
            from zoneinfo import ZoneInfo
            ch = db.get_channel(clip["channel_id"])
            tz = (ch["settings"].get("timezone") if ch else None) or "America/Toronto"
            t = t.replace(tzinfo=ZoneInfo(tz))
        when = t.timestamp()
    try:
        ids = poster.schedule_clip(clip_id, when, payload.get("platforms"))
    except RuntimeError as e:
        _bad(str(e))
    return {"posts": [db.get_post(i) for i in ids]}


@app.post("/api/clips/{clip_id}/posts")
def add_manual_post(clip_id: str, payload: dict = Body(...)):
    """You posted it yourself: paste the link so views get tracked."""
    clip = _clip_or_404(clip_id)
    url = (payload.get("url") or "").strip()
    if not url.startswith("http"):
        _bad("Paste the post link")
    platform = payload.get("platform") or next(
        (p for p in ("tiktok", "instagram", "youtube", "facebook", "x") if p in url.replace("youtu.be", "youtube")), "other")
    import time as _t
    pid = db.add_post(clip_id, clip["channel_id"], platform, status="posted", url=url, posted_at=_t.time(),
                      scheduled_at=None)
    threading.Thread(target=lambda: _safe_refresh(pid), daemon=True).start()
    return db.get_post(pid)


def _safe_refresh(pid):
    try:
        tracker.refresh_post(db.get_post(pid))
    except Exception as e:
        db.update_post(pid, error=f"stats: {str(e)[-200:]}")


# ================================================================== posts

@app.get("/api/queue")
def post_queue():
    rows = db.q("SELECT p.*, c.data AS clip_data, c.job_id FROM posts p JOIN clips c ON c.id=p.clip_id "
                "ORDER BY CASE WHEN p.status IN ('queued','posting') THEN 0 ELSE 1 END, "
                "COALESCE(p.scheduled_at, p.posted_at, p.created) DESC LIMIT 300")
    import json as _j
    chans = {c["id"]: c["name"] for c in db.list_channels()}
    out = []
    for r in rows:
        d = _j.loads(r.pop("clip_data"))
        out.append(dict(r, hook=d.get("hook"), title=d.get("title"), thumb=d.get("thumb"),
                        channel=chans.get(r["channel_id"], "—")))
    return out


@app.delete("/api/posts/{post_id}")
def cancel_post(post_id: int):
    p = db.get_post(post_id) or _bad("Post not found", 404)
    if p["status"] in ("queued", "failed"):
        db.ex("DELETE FROM posts WHERE id=?", (post_id,))
    else:  # stop tracking a posted one
        db.ex("DELETE FROM stats WHERE post_id=?", (post_id,))
        db.ex("DELETE FROM posts WHERE id=?", (post_id,))
    return {"ok": True}


@app.post("/api/posts/{post_id}/retry")
def retry_post(post_id: int):
    p = db.get_post(post_id) or _bad("Post not found", 404)
    if p["status"] != "failed":
        _bad("Only failed posts can be retried")
    db.update_post(post_id, status="queued", error=None, scheduled_at=__import__("time").time())
    return db.get_post(post_id)


@app.post("/api/posts/{post_id}/refresh")
async def refresh_post(post_id: int):
    p = db.get_post(post_id) or _bad("Post not found", 404)
    if not p.get("url"):
        _bad("This post has no link yet")
    try:
        await run_in_threadpool(tracker.refresh_post, p)
    except Exception as e:
        _bad(f"Couldn't read stats: {str(e)[-200:]}")
    return db.get_post(post_id)


@app.post("/api/stats/refresh")
def refresh_all():
    threading.Thread(target=lambda: tracker.refresh_due(force=True), daemon=True).start()
    return {"ok": True}


app.mount("/media", StaticFiles(directory=str(config.JOBS_DIR), check_dir=False), name="media")

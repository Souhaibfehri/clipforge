"""Posting via Upload-Post (TikTok, Instagram Reels, YouTube Shorts...) with per-channel time slots.

One Upload-Post *profile* per ClipForge channel holds that channel's social accounts.
Without an API key you can still post manually and paste the post links to track views.
"""
import datetime as dt
import json
import re
import time
import traceback
from zoneinfo import ZoneInfo

import requests

from . import config, db, presets

API = "https://api.upload-post.com/api"


def enabled() -> bool:
    return bool(config.UPLOADPOST_API_KEY)


def _h():
    return {"Authorization": f"Apikey {config.UPLOADPOST_API_KEY}"}


def _check(r):
    if r.status_code >= 400:
        try:
            msg = r.json().get("message") or r.text
        except ValueError:
            msg = r.text
        raise RuntimeError(f"Upload-Post {r.status_code}: {str(msg)[:300]}")
    return r.json()


# ------------------------------------------------------------------ accounts

def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:30] or "channel"


def connect_link(channel: dict) -> dict:
    """Creates the channel's Upload-Post profile if needed and returns a link to connect social accounts."""
    if not enabled():
        raise RuntimeError("Set UPLOADPOST_API_KEY in .env to connect accounts")
    s = channel["settings"]
    user = s.get("uploadpost_user")
    if not user:
        user = f"cf{channel['id']}-{_slug(channel['name'])}"
        r = requests.post(f"{API}/uploadposts/users", headers=_h(), json={"username": user}, timeout=30)
        if r.status_code not in (200, 201, 409):
            _check(r)
        s["uploadpost_user"] = user
        db.update_channel(channel["id"], settings=s)
    r = requests.post(f"{API}/uploadposts/users/generate-jwt", headers=_h(),
                      json={"username": user, "platforms": s.get("platforms") or ["tiktok", "instagram", "youtube"],
                            "connect_title": f"Connect accounts for {channel['name']}"}, timeout=30)
    return {"user": user, "url": _check(r).get("access_url")}


def connected_accounts(user: str) -> dict:
    if not enabled() or not user:
        return {}
    r = requests.get(f"{API}/uploadposts/users/{user}", headers=_h(), timeout=30)
    if r.status_code == 404:
        return {}
    accts = (_check(r).get("profile") or {}).get("social_accounts") or {}
    out = {}
    for platform, v in accts.items():
        if isinstance(v, dict) and v:
            out[platform] = v.get("username") or v.get("handle") or v.get("display_name") or "connected"
        elif isinstance(v, str) and v:
            out[platform] = v
    return out


# ------------------------------------------------------------------ scheduling

def _settings(channel_id):
    ch = db.get_channel(channel_id)
    return (presets.settings_for(ch["niche"], ch["settings"]) if ch else presets.settings_for("podcast")), ch


def next_slot(channel_id, after: float | None = None) -> float:
    s, _ = _settings(channel_id)
    tz = ZoneInfo(s.get("timezone") or "America/Toronto")
    slots = sorted(x for x in (s.get("slots") or []) if re.fullmatch(r"\d{1,2}:\d{2}", x)) or ["18:00"]
    taken = {round(r["scheduled_at"]) for r in db.q(
        "SELECT DISTINCT scheduled_at FROM posts WHERE channel_id=? AND status IN ('queued','posting','posted') "
        "AND scheduled_at IS NOT NULL", (channel_id,))}
    earliest = max(time.time() + 600, after or 0)
    day = dt.datetime.fromtimestamp(earliest, tz).date()
    for _ in range(120):
        for hm in slots:
            h, m = map(int, hm.split(":"))
            ts = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz).timestamp()
            if ts >= earliest and round(ts) not in taken:
                return ts
        day += dt.timedelta(days=1)
    return earliest


def schedule_clip(clip_id: str, when: float | None = None, platforms: list[str] | None = None) -> list[int]:
    clip = db.get_clip(clip_id)
    if not clip or clip["status"] != "ready":
        raise RuntimeError("Clip isn't ready")
    s, _ = _settings(clip["channel_id"])
    already = {p["platform"] for p in db.clip_posts(clip_id) if p["status"] in ("queued", "posting", "posted")}
    platforms = [p for p in (platforms or s.get("platforms") or []) if p not in already]
    if not platforms:
        return []
    when = when or next_slot(clip["channel_id"])
    return [db.add_post(clip_id, clip["channel_id"], p, scheduled_at=when) for p in platforms]


def auto_schedule(job_id: str) -> int:
    n = 0
    for clip in db.job_clips(job_id):
        n += maybe_auto_schedule(clip["id"])
    return n


def maybe_auto_schedule(clip_id: str) -> int:
    clip = db.get_clip(clip_id)
    if not clip or clip["status"] != "ready":
        return 0
    s, ch = _settings(clip["channel_id"])
    if not ch or not s.get("auto_post") or not enabled() or not s.get("uploadpost_user"):
        return 0
    if s.get("require_voiceover") and not clip.get("final"):
        return 0
    if db.clip_posts(clip_id):
        return 0
    return len(schedule_clip(clip_id))


# ------------------------------------------------------------------ publishing

def caption_for(clip: dict, settings: dict) -> str:
    tags = " ".join(dict.fromkeys((clip.get("hashtags") or []) + (settings.get("hashtags") or "").split()))
    parts = [clip.get("title", ""), clip.get("description", ""), tags]
    return "\n\n".join(p for p in parts if p).strip()


def publish(posts: list[dict]) -> None:
    clip = db.get_clip(posts[0]["clip_id"])
    s, _ = _settings(clip["channel_id"])
    user = s.get("uploadpost_user")
    if not enabled() or not user:
        for p in posts:
            db.update_post(p["id"], status="failed", error="Upload-Post not configured for this channel")
        return
    path = db.job_dir(clip["job_id"]) / (clip.get("final") or clip["file"])
    caption = caption_for(clip, s)
    data = [("user", user), ("title", caption[:2100]), ("async_upload", "true"),
            ("youtube_title", (clip.get("title") or clip.get("hook") or "Clip")[:95]),
            ("youtube_description", caption[:4900]), ("privacyStatus", "public")]
    data += [("platform[]", p["platform"]) for p in posts]
    for p in posts:
        db.update_post(p["id"], status="posting", error=None)
    try:
        with open(path, "rb") as f:
            r = requests.post(f"{API}/upload", headers=_h(), data=data,
                              files={"video": (path.name, f, "video/mp4")}, timeout=900)
        res = _check(r)
        rid = res.get("request_id")
        if rid:
            for p in posts:
                db.update_post(p["id"], request_id=rid)
        else:  # synchronous answer
            _apply_results(posts, [dict(v, platform=k) for k, v in (res.get("results") or {}).items()])
    except Exception as e:
        for p in posts:
            db.update_post(p["id"], status="failed", error=str(e)[:500])


def _apply_results(posts, results):
    by_platform = {r.get("platform"): r for r in results}
    for p in posts:
        r = by_platform.get(p["platform"])
        if not r:
            continue
        if r.get("success"):
            db.update_post(p["id"], status="posted", posted_at=time.time(),
                           url=r.get("post_url") or r.get("url"),
                           platform_post_id=str(r.get("platform_post_id") or r.get("post_id") or ""), error=None)
        else:
            db.update_post(p["id"], status="failed", error=str(r.get("message") or r.get("error") or "failed")[:500])


def check_pending() -> None:
    for rid in {p["request_id"] for p in db.q("SELECT request_id FROM posts WHERE status='posting' AND request_id IS NOT NULL")}:
        try:
            r = requests.get(f"{API}/uploadposts/status", headers=_h(), params={"request_id": rid}, timeout=30)
            res = _check(r)
            posts = db.q("SELECT * FROM posts WHERE request_id=?", (rid,))
            _apply_results(posts, res.get("results") or [])
            if res.get("status") == "completed":
                for p in db.q("SELECT * FROM posts WHERE request_id=? AND status='posting'", (rid,)):
                    db.update_post(p["id"], status="failed", error="No result returned for this platform")
        except Exception:
            traceback.print_exc()


def tick() -> None:
    """Called every minute: publish due posts, then check async uploads."""
    if not enabled():
        return
    due = db.q("SELECT * FROM posts WHERE status='queued' AND scheduled_at <= ? ORDER BY scheduled_at", (time.time(),))
    groups = {}
    for p in due:
        groups.setdefault((p["clip_id"], p["scheduled_at"]), []).append(p)
    for posts in groups.values():
        publish(posts)
    check_pending()

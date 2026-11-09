"""View tracking, earnings estimates, stats aggregation and Claude insights."""
import datetime as dt
import json
import time
import traceback
from collections import defaultdict

from . import db, llm, presets, video

HOUR = 3600


def _due(post, now):
    if not post.get("url"):
        return False
    age = now - (post.get("posted_at") or post["created"])
    last = post.get("last_checked") or 0
    if age < 2 * 24 * HOUR:
        every = 3 * HOUR
    elif age < 7 * 24 * HOUR:
        every = 12 * HOUR
    elif age < 30 * 24 * HOUR:
        every = 48 * HOUR
    else:
        return False
    return now - last >= every


def refresh_post(post) -> dict:
    s = video.post_stats(post["url"])
    db.add_snapshot(post["id"], s["views"], s["likes"], s["comments"], s["shares"])
    db.update_post(post["id"], error=None)
    return s


def refresh_due(force=False) -> int:
    now = time.time()
    n = 0
    for p in db.q("SELECT * FROM posts WHERE status='posted' AND url IS NOT NULL AND url != ''"):
        if force or _due(p, now):
            try:
                refresh_post(p)
                n += 1
            except Exception as e:
                db.update_post(p["id"], last_checked=now, error=f"stats: {str(e)[-200:]}")
            time.sleep(1.5)  # be gentle with the platforms
    return n


# ------------------------------------------------------------------ aggregation

def _settings(ch):
    return presets.settings_for(ch["niche"], ch["settings"])


def _daily(post_ids, days=30, tz_name="America/Toronto"):
    """Views gained per day across the given posts (from snapshots)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_name)
    gains = defaultdict(int)
    if post_ids:
        marks = ",".join("?" * len(post_ids))
        rows = db.q(f"SELECT post_id, ts, views FROM stats WHERE post_id IN ({marks}) ORDER BY post_id, ts", post_ids)
        last = {}
        for r in rows:
            day = dt.datetime.fromtimestamp(r["ts"], tz).date().isoformat()
            prev = last.get(r["post_id"], 0)
            gains[day] += max(0, (r["views"] or 0) - prev)
            last[r["post_id"]] = r["views"] or 0
    today = dt.datetime.now(tz).date()
    return [{"day": (today - dt.timedelta(days=i)).isoformat(),
             "views": gains.get((today - dt.timedelta(days=i)).isoformat(), 0)} for i in range(days - 1, -1, -1)]


def channel_summary(ch) -> dict:
    s = _settings(ch)
    posts = db.q("SELECT * FROM posts WHERE channel_id=?", (ch["id"],))
    posted = [p for p in posts if p["status"] == "posted"]
    views = sum(p["views"] or 0 for p in posted)
    cpm = float(s.get("cpm") or 0)
    earnings = views / 1000 * cpm
    budget = float(s.get("budget") or 0)
    if budget:
        earnings = min(earnings, budget)
    clips = db.q("SELECT status, COUNT(*) AS n FROM clips WHERE channel_id=? GROUP BY status", (ch["id"],))
    by_status = {r["status"]: r["n"] for r in clips}
    week_ago = time.time() - 7 * 24 * HOUR
    return {
        "id": ch["id"], "name": ch["name"], "niche": ch["niche"], "color": s.get("color"),
        "label": presets.PRESETS.get(ch["niche"], {}).get("label", ch["niche"]),
        "clips_ready": by_status.get("ready", 0), "clips_total": sum(by_status.values()),
        "posts": len(posted), "queued": sum(1 for p in posts if p["status"] == "queued"),
        "failed": sum(1 for p in posts if p["status"] == "failed"),
        "views": views, "avg_views": round(views / len(posted)) if posted else 0,
        "likes": sum(p["likes"] or 0 for p in posted),
        "posts_7d": sum(1 for p in posted if (p["posted_at"] or 0) >= week_ago),
        "cpm": cpm, "budget": budget, "earnings": round(earnings, 2),
        "auto_post": bool(s.get("auto_post")), "watch": bool(s.get("watch")),
    }


def overview() -> dict:
    chans = [channel_summary(ch) for ch in db.list_channels()]
    posted_ids = [p["id"] for p in db.q("SELECT id FROM posts WHERE status='posted'")]
    daily = _daily(posted_ids)
    return {
        "channels": chans,
        "totals": {
            "views": sum(c["views"] for c in chans), "posts": sum(c["posts"] for c in chans),
            "earnings": round(sum(c["earnings"] for c in chans), 2),
            "clips_ready": sum(c["clips_ready"] for c in chans),
            "queued": sum(c["queued"] for c in chans),
            "views_7d": sum(d["views"] for d in daily[-7:]),
        },
        "daily": daily,
        "top": top_clips(limit=8),
    }


def top_clips(channel_id=None, limit=10) -> list[dict]:
    where, args = ("WHERE p.status='posted'", ())
    if channel_id:
        where, args = ("WHERE p.status='posted' AND p.channel_id=?", (channel_id,))
    rows = db.q(f"SELECT c.id AS clip_id, c.job_id, c.channel_id, c.data, SUM(p.views) AS views, "
                f"SUM(p.likes) AS likes, GROUP_CONCAT(p.platform) AS platforms "
                f"FROM posts p JOIN clips c ON c.id=p.clip_id {where} GROUP BY c.id ORDER BY views DESC LIMIT ?",
                (*args, limit))
    out = []
    for r in rows:
        d = json.loads(r["data"])
        out.append({"clip_id": r["clip_id"], "job_id": r["job_id"], "channel_id": r["channel_id"],
                    "hook": d.get("hook"), "title": d.get("title"), "thumb": d.get("thumb"),
                    "views": r["views"] or 0, "likes": r["likes"] or 0, "platforms": r["platforms"]})
    return out


def _bucket(rows, key):
    agg = defaultdict(lambda: [0, 0])
    for r in rows:
        k = key(r)
        if k is None:
            continue
        agg[k][0] += r["views"] or 0
        agg[k][1] += 1
    return [{"key": k, "posts": n, "avg_views": round(v / n)} for k, (v, n) in sorted(agg.items(), key=lambda x: str(x[0]))]


def channel_stats(ch) -> dict:
    s = _settings(ch)
    rows = db.q("SELECT p.*, c.data FROM posts p JOIN clips c ON c.id=p.clip_id WHERE p.channel_id=? AND p.status='posted'",
                (ch["id"],))
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(s.get("timezone") or "America/Toronto")
    for r in rows:
        d = json.loads(r.pop("data"))
        r["clip"] = d
        r["hour"] = dt.datetime.fromtimestamp(r["posted_at"] or r["created"], tz).hour
    def dur_bucket(r):
        x = r["clip"].get("out_duration") or r["clip"].get("duration") or 0
        return "<20s" if x < 20 else "20-35s" if x < 35 else "35-50s" if x < 50 else "50s+"
    return {
        "summary": channel_summary(ch),
        "daily": _daily([r["id"] for r in rows], tz_name=s.get("timezone") or "America/Toronto"),
        "by_platform": _bucket(rows, lambda r: r["platform"]),
        "by_hour": _bucket(rows, lambda r: f"{r['hour']:02d}:00"),
        "by_length": _bucket(rows, dur_bucket),
        "by_voiceover": _bucket(rows, lambda r: "with voiceover" if r["clip"].get("final") else "no voiceover"),
        "by_style": _bucket(rows, lambda r: r["clip"].get("caption_style") or "bold"),
        "top": top_clips(ch["id"], 10),
        "insight": db.latest_insight(ch["id"]),
    }


def generate_insights(ch) -> dict:
    s = _settings(ch)
    stats = channel_stats(ch)
    rows = db.q("SELECT p.*, c.data FROM posts p JOIN clips c ON c.id=p.clip_id WHERE p.channel_id=? AND p.status='posted' "
                "ORDER BY p.views DESC LIMIT 80", (ch["id"],))
    if len(rows) < 5:
        raise RuntimeError("Post at least 5 clips with tracked views first, then insights become meaningful.")
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(s.get("timezone") or "America/Toronto")
    lines = []
    for r in rows:
        d = json.loads(r["data"])
        hour = dt.datetime.fromtimestamp(r["posted_at"] or r["created"], tz).strftime("%H:00")
        lines.append(f"{r['views']} | {r['likes']} | {d.get('out_duration') or d.get('duration')} | {d.get('hook')} | "
                     f"{d.get('title')} | {hour} | {d.get('caption_style', 'bold')} | {'yes' if d.get('final') else 'no'} | {r['platform']}")
    data = llm.insights(ch["name"], ch["niche"], lines)
    data["based_on"] = len(rows)
    db.save_insight(ch["id"], data)
    return data


def safe_refresh():
    try:
        refresh_due()
    except Exception:
        traceback.print_exc()

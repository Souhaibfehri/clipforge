"""SQLite storage: channels, jobs, clips, posts, view snapshots, insights.

Media files live on disk in DATA_DIR/jobs/<job_id>/; everything else is here.
"""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  niche TEXT NOT NULL,
  settings TEXT NOT NULL DEFAULT '{}',
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  channel_id INTEGER,
  created REAL NOT NULL,
  source TEXT NOT NULL,
  options TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  message TEXT,
  error TEXT,
  title TEXT,
  duration REAL
);
CREATE TABLE IF NOT EXISTS clips (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  channel_id INTEGER,
  status TEXT NOT NULL,
  created REAL NOT NULL,
  data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  clip_id TEXT NOT NULL,
  channel_id INTEGER,
  platform TEXT NOT NULL,
  status TEXT NOT NULL,              -- queued | posting | posted | failed
  scheduled_at REAL,
  posted_at REAL,
  url TEXT,
  platform_post_id TEXT,
  request_id TEXT,
  error TEXT,
  views INTEGER DEFAULT 0,
  likes INTEGER DEFAULT 0,
  comments INTEGER DEFAULT 0,
  shares INTEGER DEFAULT 0,
  last_checked REAL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stats (
  post_id INTEGER NOT NULL,
  ts REAL NOT NULL,
  views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER
);
CREATE TABLE IF NOT EXISTS seen_sources (
  channel_id INTEGER NOT NULL,
  video_id TEXT NOT NULL,
  ts REAL NOT NULL,
  PRIMARY KEY (channel_id, video_id)
);
CREATE TABLE IF NOT EXISTS insights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER,
  created REAL NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_clips_job ON clips(job_id);
CREATE INDEX IF NOT EXISTS ix_clips_channel ON clips(channel_id);
CREATE INDEX IF NOT EXISTS ix_posts_clip ON posts(clip_id);
CREATE INDEX IF NOT EXISTS ix_posts_status ON posts(status, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_stats_post ON stats(post_id, ts);
"""


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(config.DATA_DIR / "clipforge.db", check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.executescript(SCHEMA)
        return _conn


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid


def job_dir(job_id: str) -> Path:
    if not job_id or "/" in job_id or ".." in job_id:
        raise ValueError("bad job id")
    return config.JOBS_DIR / job_id


# ------------------------------------------------------------------ channels

def _channel(row):
    if not row:
        return None
    row["settings"] = json.loads(row["settings"])
    return row


def create_channel(name: str, niche: str, settings: dict) -> dict:
    cid = ex("INSERT INTO channels(name, niche, settings, created) VALUES (?,?,?,?)",
             (name, niche, json.dumps(settings), time.time()))
    return get_channel(cid)


def get_channel(cid) -> dict | None:
    if cid is None:
        return None
    return _channel(q1("SELECT * FROM channels WHERE id=?", (cid,)))


def list_channels() -> list[dict]:
    return [_channel(r) for r in q("SELECT * FROM channels ORDER BY id")]


def update_channel(cid, name=None, niche=None, settings=None) -> dict:
    ch = get_channel(cid)
    if not ch:
        raise KeyError(cid)
    ex("UPDATE channels SET name=?, niche=?, settings=? WHERE id=?",
       (name or ch["name"], niche or ch["niche"], json.dumps(settings if settings is not None else ch["settings"]), cid))
    return get_channel(cid)


def delete_channel(cid):
    ex("DELETE FROM channels WHERE id=?", (cid,))


# ------------------------------------------------------------------ jobs

def _job(row):
    if not row:
        return None
    row["source"] = json.loads(row["source"])
    row["options"] = json.loads(row["options"])
    return row


def new_job(source: dict, options: dict, channel_id=None) -> dict:
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    job_dir(job_id).mkdir(parents=True, exist_ok=True)
    ex("INSERT INTO jobs(id, channel_id, created, source, options, status, message, title) VALUES (?,?,?,?,?,?,?,?)",
       (job_id, channel_id, time.time(), json.dumps(source), json.dumps(options), "queued", "Waiting in queue",
        source.get("url") or source.get("filename")))
    return get_job(job_id)


def get_job(job_id) -> dict | None:
    return _job(q1("SELECT * FROM jobs WHERE id=?", (job_id,)))


def list_jobs(channel_id=None, limit=100) -> list[dict]:
    if channel_id:
        rows = q("SELECT * FROM jobs WHERE channel_id=? ORDER BY created DESC LIMIT ?", (channel_id, limit))
    else:
        rows = q("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,))
    return [_job(r) for r in rows]


def update_job(job_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = [json.dumps(v) if k in ("source", "options") else v for k, v in fields.items()]
    ex(f"UPDATE jobs SET {cols} WHERE id=?", (*vals, job_id))


def delete_job(job_id):
    clip_ids = [c["id"] for c in q("SELECT id FROM clips WHERE job_id=?", (job_id,))]
    for cid in clip_ids:
        post_ids = [p["id"] for p in q("SELECT id FROM posts WHERE clip_id=?", (cid,))]
        for pid in post_ids:
            ex("DELETE FROM stats WHERE post_id=?", (pid,))
        ex("DELETE FROM posts WHERE clip_id=?", (cid,))
    ex("DELETE FROM clips WHERE job_id=?", (job_id,))
    ex("DELETE FROM jobs WHERE id=?", (job_id,))


# ------------------------------------------------------------------ clips

CLIP_COLUMNS = {"id", "job_id", "channel_id", "status", "created"}


def _clip(row):
    if not row:
        return None
    data = json.loads(row.pop("data"))
    return {**data, **row}


def add_clip(job_id, channel_id, clip: dict) -> dict:
    clip_id = f"{job_id}_{clip['n']:02d}"
    data = {k: v for k, v in clip.items() if k not in CLIP_COLUMNS}
    ex("INSERT OR REPLACE INTO clips(id, job_id, channel_id, status, created, data) VALUES (?,?,?,?,?,?)",
       (clip_id, job_id, channel_id, clip.get("status", "pending"), time.time(), json.dumps(data, ensure_ascii=False)))
    return get_clip(clip_id)


def get_clip(clip_id) -> dict | None:
    return _clip(q1("SELECT * FROM clips WHERE id=?", (clip_id,)))


def job_clips(job_id) -> list[dict]:
    return [_clip(r) for r in q("SELECT * FROM clips WHERE job_id=? ORDER BY id", (job_id,))]


def channel_clips(channel_id, limit=500) -> list[dict]:
    return [_clip(r) for r in q("SELECT * FROM clips WHERE channel_id=? ORDER BY created DESC LIMIT ?",
                                 (channel_id, limit))]


def update_clip(clip_id, **fields) -> dict:
    with _lock:
        clip = get_clip(clip_id)
        if not clip:
            raise KeyError(clip_id)
        clip.update(fields)
        data = {k: v for k, v in clip.items() if k not in CLIP_COLUMNS}
        ex("UPDATE clips SET status=?, data=? WHERE id=?",
           (clip["status"], json.dumps(data, ensure_ascii=False), clip_id))
        return clip


# ------------------------------------------------------------------ posts & stats

def add_post(clip_id, channel_id, platform, scheduled_at=None, status="queued", url=None, posted_at=None) -> int:
    return ex("INSERT INTO posts(clip_id, channel_id, platform, status, scheduled_at, url, posted_at, created) "
              "VALUES (?,?,?,?,?,?,?,?)",
              (clip_id, channel_id, platform, status, scheduled_at, url, posted_at, time.time()))


def update_post(post_id, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    ex(f"UPDATE posts SET {cols} WHERE id=?", (*fields.values(), post_id))


def get_post(post_id):
    return q1("SELECT * FROM posts WHERE id=?", (post_id,))


def clip_posts(clip_id):
    return q("SELECT * FROM posts WHERE clip_id=? ORDER BY id", (clip_id,))


def add_snapshot(post_id, views, likes, comments, shares):
    now = time.time()
    ex("INSERT INTO stats(post_id, ts, views, likes, comments, shares) VALUES (?,?,?,?,?,?)",
       (post_id, now, views, likes, comments, shares))
    update_post(post_id, views=views, likes=likes, comments=comments, shares=shares, last_checked=now)


def is_seen(channel_id, video_id) -> bool:
    return q1("SELECT 1 AS x FROM seen_sources WHERE channel_id=? AND video_id=?", (channel_id, video_id)) is not None


def mark_seen(channel_id, video_id):
    ex("INSERT OR IGNORE INTO seen_sources(channel_id, video_id, ts) VALUES (?,?,?)", (channel_id, video_id, time.time()))


def save_insight(channel_id, data: dict):
    ex("INSERT INTO insights(channel_id, created, data) VALUES (?,?,?)",
       (channel_id, time.time(), json.dumps(data, ensure_ascii=False)))


def latest_insight(channel_id):
    row = q1("SELECT * FROM insights WHERE channel_id=? ORDER BY created DESC LIMIT 1", (channel_id,))
    if row:
        row["data"] = json.loads(row["data"])
    return row

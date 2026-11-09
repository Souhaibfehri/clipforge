"""Volume engine: watches each channel's sources (YouTube channels/playlists) and auto-clips new uploads."""
import traceback

from . import db, presets, video


def check_channel(ch: dict, enqueue) -> list[str]:
    s = presets.settings_for(ch["niche"], ch["settings"])
    created = []
    initialized = set(ch["settings"].get("sources_initialized") or [])
    for src in s.get("sources") or []:
        src = src.strip()
        if not src:
            continue
        try:
            vids = video.latest_videos(src, n=6)
        except Exception as e:
            print(f"watcher: {src}: {e}")
            continue
        first_time = src not in initialized
        backfill = int(ch["settings"].get("backfill", 1)) if first_time else len(vids)
        for i, v in enumerate(vids):  # newest first
            if db.is_seen(ch["id"], v["id"]):
                continue
            db.mark_seen(ch["id"], v["id"])
            if i < backfill:
                job = db.new_job({"type": "url", "url": v["url"]}, {}, ch["id"])
                db.update_job(job["id"], title=v["title"], message="Queued by source watcher")
                enqueue(job["id"])
                created.append(job["id"])
        initialized.add(src)
    if initialized != set(ch["settings"].get("sources_initialized") or []):
        fresh = db.get_channel(ch["id"])
        fresh["settings"]["sources_initialized"] = sorted(initialized)
        db.update_channel(ch["id"], settings=fresh["settings"])
    return created


def check_all(enqueue) -> int:
    n = 0
    for ch in db.list_channels():
        s = presets.settings_for(ch["niche"], ch["settings"])
        if s.get("watch") and s.get("sources"):
            try:
                n += len(check_channel(ch, enqueue))
            except Exception:
                traceback.print_exc()
    return n

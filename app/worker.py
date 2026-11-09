"""Background work: one job worker (a small VPS renders one video at a time) + a scheduler loop
that publishes due posts every minute, refreshes view stats, and checks watched sources."""
import queue
import threading
import time
import traceback

from . import config, db, pipeline, poster, tracker, watcher

_q: "queue.Queue[str]" = queue.Queue()
_queued: set[str] = set()
_started = False
_last = {"stats": 0.0, "watch": 0.0, "clean": 0.0}


def cleanup_sources() -> int:
    """Free disk: remove big source videos of finished jobs older than SOURCE_RETENTION_DAYS."""
    if config.SOURCE_RETENTION_DAYS <= 0:
        return 0
    cutoff = time.time() - config.SOURCE_RETENTION_DAYS * 86400
    n = 0
    for job in db.list_jobs(limit=100000):
        if job["status"] in ("done", "error") and job["created"] < cutoff:
            for p in db.job_dir(job["id"]).glob("source.*"):
                p.unlink(missing_ok=True)
                n += 1
    return n


def enqueue(job_id: str) -> None:
    if job_id in _queued:
        return
    _queued.add(job_id)
    _q.put(job_id)


def _jobs_loop():
    while True:
        job_id = _q.get()
        _queued.discard(job_id)
        try:
            pipeline.process(job_id)
        except Exception:
            traceback.print_exc()
        finally:
            _q.task_done()


def _scheduler_loop():
    time.sleep(5)
    while True:
        try:
            poster.tick()
        except Exception:
            traceback.print_exc()
        now = time.time()
        if now - _last["stats"] >= config.STATS_EVERY_MINUTES * 60:
            _last["stats"] = now
            threading.Thread(target=tracker.safe_refresh, daemon=True).start()
        if now - _last["clean"] >= 6 * 3600:
            _last["clean"] = now
            try:
                cleanup_sources()
            except Exception:
                traceback.print_exc()
        if now - _last["watch"] >= config.WATCH_EVERY_HOURS * 3600:
            _last["watch"] = now
            try:
                watcher.check_all(enqueue)
            except Exception:
                traceback.print_exc()
        time.sleep(60)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    for job in sorted(db.list_jobs(limit=10000), key=lambda j: j["created"]):
        if job["status"] not in ("done", "error"):
            db.update_job(job["id"], status="queued", message="Resumed after restart")
            enqueue(job["id"])
    threading.Thread(target=_jobs_loop, daemon=True, name="jobs").start()
    threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()


def queue_size() -> int:
    return _q.qsize()

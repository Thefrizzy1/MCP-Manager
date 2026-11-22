"""core/organiser.py — files finished videos into the archive.

A video folder in ``01_Open_Jobs`` moves to ``02_Archive/<year>/<month>`` once
the video is live and either its Shorts are marked done or it has been live
for a while (the grace period, so the folder does not move while the Shorts
and the last fixes are still being made).

Only ever a rename inside the video root: nothing is deleted, nothing is
overwritten, and every move is written to the activity log. Whether it may
move at all is the ``nas.archive_videos`` permission: allow moves at once, ask
files a request and moves after the approval, never does nothing. A read-only
mount simply reports that it cannot move.
"""
from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

from core import jobs, permissions

GRACE_DAYS = 14
ACTION = "nas.archive_videos"


def _days_live(job: dict) -> int | None:
    pub = (job.get("published") or {}).get("date") or ""
    try:
        return (date.today() - date.fromisoformat(pub[:10])).days
    except ValueError:
        return job.get("idle_days")       # live by a pasted URL: go by how long it has been quiet


def due(job: dict, grace_days: int = GRACE_DAYS) -> str:
    """Why this job should be archived now, or '' if it should stay."""
    if job.get("where") != "open" or job.get("pinned"):
        return ""
    stages = {s["id"]: s["status"] for s in job["stages"]}
    if stages.get("publish") != "done":
        return ""
    if stages.get("shorts") == "done":
        return "published and Shorts done"
    days = _days_live(job)
    if days is not None and days >= grace_days:
        return f"published {days} days ago"
    return ""


def destination(job_id: str) -> Path:
    return jobs.youtube_root() / jobs.ARCHIVE_DIR / job_id[:4] / job_id[4:6] / job_id


def archive(job_id: str) -> Path:
    """Move one job folder to the archive. Raises with a plain reason if it cannot."""
    src = jobs.job_folder(job_id)
    if not src or src.parent.name != jobs.OPEN_DIR:
        raise ValueError("not a folder in the open jobs")
    dst = destination(job_id)
    if dst.exists():
        raise FileExistsError(f"{dst.relative_to(jobs.youtube_root())} already exists")
    try:
        for parent in (dst.parent.parent, dst.parent):          # year, then month
            if not parent.is_dir():
                parent.mkdir()
                jobs.inherit(parent)
        os.rename(src, dst)
    except OSError as e:
        raise PermissionError(
            "the video folder is mounted read-only, so nothing can be moved" if e.errno == 30
            else f"could not move: {e.strerror or e}") from e
    jobs._cache["ts"] = 0.0
    return dst


def run(root: Path, *, dry_run: bool = False, grace_days: int = GRACE_DAYS) -> list[dict]:
    """Archive every video that is due. Returns one row per video considered due."""
    board = jobs.list_jobs(root, force=True)
    if not (jobs.youtube_root() / jobs.OPEN_DIR).is_dir():
        return []                       # flat layout: there is no archive folder to file into
    setting = permissions.check(root, ACTION)
    approved = {r.get("ref") for r in permissions.requests(root, "approved") if r["action"] == ACTION}
    out = []
    for job in board["jobs"]:
        why = due(job, grace_days)
        if not why:
            continue
        row = {"job": job["id"], "title": job["title"], "why": why, "result": "would move"}
        if dry_run or setting == "never":
            row["result"] = "would move" if setting != "never" else "not allowed"
            out.append(row)
            continue
        if setting == "ask" and job["id"] not in approved:
            permissions.request(root, ACTION, f"Archive “{job['title']}” ({why})", ref=job["id"])
            row["result"] = "waiting for your OK"
            out.append(row)
            continue
        try:
            dst = archive(job["id"])
        except (ValueError, FileExistsError, PermissionError) as e:
            row["result"] = f"failed: {e}"
            out.append(row)
            continue
        row["result"] = "moved"
        row["to"] = str(dst.relative_to(jobs.youtube_root()))
        jobs.log_activity(root, f"Archived “{job['title']}” ({why})", job["id"], "organiser")
        for r in permissions.requests(root, "approved"):
            if r["action"] == ACTION and r.get("ref") == job["id"]:
                permissions.mark_done(root, r["id"])
        out.append(row)
    return out


async def loop(root: Path, every: int = 30 * 60) -> None:
    """Background: refresh the channel's uploads, then file what is due."""
    import asyncio
    await asyncio.sleep(90)                       # let the server finish starting
    while True:
        started = time.time()
        try:
            from tools.jobs import sync_uploads
            await sync_uploads(root)
            await asyncio.to_thread(run, root)
        except Exception:                          # a bad pass must not end the loop
            pass
        await asyncio.sleep(max(60, every - (time.time() - started)))

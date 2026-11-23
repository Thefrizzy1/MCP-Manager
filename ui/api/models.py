"""Models surface: what the Trend Radar found, and what the model lab did about it."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import asyncio

from core import jobs, lab_board, permissions, studio
from core import trend_radar as tr
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])

# Radar stages that mean "files you can download exist".
RELEASED = ("weights", "gguf")


@router.get("/api/v1/models")
async def api_v1_models():
    lab = lab_board.entries(ROOT)
    now = time.time()
    rows = []
    for c in tr.candidates(ROOT, limit=300):
        entry = lab.get(c["slug"])
        rows.append({
            "slug": c["slug"], "name": c["name"], "hot": round(c.get("hot") or 0),
            "stage": c.get("stage") or "", "released": (c.get("stage") or "") in RELEASED,
            "covered": str(c.get("action") or "").startswith("DONE"),
            "first_seen": c.get("first_ts") or 0,
            "age_days": int((now - (c.get("first_ts") or now)) / 86400),
            "signals": c.get("signal_count", 0),
            "lab": entry,
        })
    known = {r["slug"] for r in rows}
    for slug, entry in lab.items():          # queued by hand, not (yet) on the radar
        if slug not in known:
            rows.append({"slug": slug, "name": entry.get("name") or slug, "hot": 0, "stage": "",
                         "released": True, "covered": False, "first_seen": entry.get("added", 0),
                         "age_days": 0, "signals": 0, "lab": entry})
    return {"models": rows, "heartbeat": lab_board.heartbeat(ROOT)}


class QueueBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=120)
    name: str = Field(default="", max_length=200)
    status: str = Field(default="queued", max_length=12)   # queued | front | skipped | "" (remove)


@router.post("/api/v1/models/set")
async def api_v1_models_set(body: QueueBody):
    try:
        if body.status == "front":
            lab_board.to_front(ROOT, body.slug, body.name)
        elif body.status == "queued":
            lab_board.queue(ROOT, body.slug, body.name)
        else:
            lab_board.set_status(ROOT, body.slug.strip().lower(), body.status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await api_v1_models()


class PublishBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=120)


@router.post("/api/v1/models/publish")
async def api_v1_models_publish(body: PublishBody):
    """The Publish button: approve merging this model's pull request.

    The lab carries it out (merge, then the Hugging Face mirror) the next time
    it checks for approvals. Pressing it is the approval itself, so it works
    whatever the permission is set to, except Never.
    """
    row = lab_board.entries(ROOT).get(body.slug.strip().lower())
    if not row or not row.get("pr"):
        raise HTTPException(404, "This model has no pull request yet")
    # Already live and a 3060 follow-up is waiting: this press publishes the update.
    target = row.get("update_pr") if row.get("status") == "published" and row.get("update_pr") else row["pr"]
    verb = "Update" if target != row["pr"] else "Publish"
    try:
        req = permissions.request(ROOT, "lab.merge_pr", f"{verb} workflow: {row.get('name') or row['slug']}",
                                  target, ref=target)
        if req["status"] == "pending":
            permissions.decide(ROOT, req["id"], True)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    lab_board.report(ROOT, row["slug"], "You approved publishing. The lab will merge it shortly.")
    lab_board.set_flag(ROOT, row["slug"], "publish_requested", True)
    return await api_v1_models()


def video_brief(row: dict) -> str:
    """The idea text for a video about a tested workflow: what it is and what was measured."""
    r = row.get("review") or {}
    lines = [f"Make a video about {row.get('name') or row['slug']}, using the workflow the lab built and tested.", ""]
    if r.get("title"):
        lines.append(f"Workflow: {r['title']}")
    if r.get("repo") and r.get("folder"):
        branch = "main" if row.get("status") == "published" else r.get("branch", "main")
        lines.append(f"Workflow files: https://github.com/{r['repo']}/tree/{branch}/{r['folder']}")
    if row.get("pr"):
        lines.append(f"Pull request: {row['pr']}")
    for key, label in (("summary", "What it does"), ("tested", "Tested"), ("critique", "Review"),
                       ("not_verified", "Not verified")):
        if r.get(key):
            lines.append(f"{label}: {r[key]}")
    g = row.get("gpu3060") or {}
    if g.get("status") == "done":
        lines.append(f"On the 12 GB card: {g.get('text', '')}")
    return "\n".join(lines)


@router.post("/api/v1/models/to_video")
async def api_v1_models_to_video(body: PublishBody):
    """'Make a video' on a tested model: create the video folder with what the lab
    found, and have the Channel Manager start research and script. Asking twice
    opens the same video instead of making a second one."""
    slug = body.slug.strip().lower()
    row = lab_board.entries(ROOT).get(slug)
    if not row:
        raise HTTPException(404, "No such model on the lab's list")
    if row.get("status") not in ("built", "published"):
        raise HTTPException(409, "The workflow is not tested yet")
    if row.get("video") and jobs.job_folder(row["video"]):
        return {"job": row["video"], "created": False}
    idea = video_brief(row)
    name = row.get("name") or slug
    try:
        try:
            job = await asyncio.to_thread(jobs.create_job, name, idea)
        except FileExistsError:
            job = await asyncio.to_thread(jobs.create_job, f"{name} workflow", idea)
    except (ValueError, PermissionError, FileExistsError) as e:
        raise HTTPException(400, str(e))
    studio.post(ROOT, job, "user", idea)
    studio.add_task(ROOT, job, "intake", idea)
    lab_board.set_flag(ROOT, slug, "video", job)
    return {"job": job, "created": True}


class QueueNewBody(BaseModel):
    max_age_days: int = Field(default=45, ge=1, le=400)


@router.post("/api/v1/models/queue_new")
async def api_v1_models_queue_new(body: QueueNewBody):
    """Queue every released model first seen recently that is neither covered nor on the list."""
    data = await api_v1_models()
    for m in sorted(data["models"], key=lambda m: -m["hot"]):
        if m["released"] and not m["covered"] and not m["lab"] and m["age_days"] <= body.max_age_days:
            lab_board.queue(ROOT, m["slug"], m["name"])
    return await api_v1_models()

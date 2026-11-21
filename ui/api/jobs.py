"""Job board surface: the NAS video folders as jobs, their files, and manual facts."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import jobs
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])

_TEXT_LIMIT = 400_000


@router.get("/api/v1/jobs")
async def api_v1_jobs(request: Request):
    """Every job, newest first. ``?refresh=1`` rescans the NAS now."""
    force = request.query_params.get("refresh") in ("1", "true")
    # The scan is blocking filesystem work (slow on a network mount).
    data = await asyncio.to_thread(jobs.list_jobs, ROOT, force=force)
    data["activity"] = jobs.recent_activity(ROOT, limit=30)
    return data


@router.get("/api/v1/jobs/detail")
async def api_v1_job_detail(job: str, rel: str = ""):
    """One job plus one level of its folder (``rel`` = subfolder to open)."""
    rec = await asyncio.to_thread(jobs.get_job, ROOT, job)
    if not rec:
        raise HTTPException(404, "No such job folder")
    listing = await asyncio.to_thread(jobs.list_folder, job, rel)
    if listing is None:
        raise HTTPException(400, "Not a folder inside this job")
    return {"job": rec, "rel": rel, "files": listing,
            "activity": jobs.recent_activity(ROOT, job_id=job, limit=30)}


@router.get("/api/v1/jobs/file")
async def api_v1_job_file(job: str, rel: str):
    """Serve one file from inside a job folder: images/PDF/video inline, text as JSON."""
    path = jobs.resolve_file(job, rel)
    if not path:
        raise HTTPException(404, "No such file in this job")
    kind = jobs.file_kind(path)
    if kind == "text":
        if path.stat().st_size > _TEXT_LIMIT:
            raise HTTPException(413, "File too large to preview")
        return {"name": path.name, "text": path.read_text(encoding="utf-8", errors="replace")}
    if kind in ("image", "pdf", "video"):
        return FileResponse(str(path), headers={"Cache-Control": "private, max-age=300"})
    raise HTTPException(415, "This file type has no preview")


class JobUpdate(BaseModel):
    job: str = Field(..., min_length=1, max_length=300)
    stages: dict[str, str | None] | None = None
    archived: bool | None = None
    archived_auto: bool = False          # true = go back to automatic archiving
    pinned: bool | None = None
    deadline: str | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=4000)
    publish_url: str | None = Field(default=None, max_length=500)
    thumbnail: str | None = Field(default=None, max_length=500)


@router.post("/api/v1/jobs/update")
async def api_v1_job_update(body: JobUpdate):
    changes = body.model_dump(exclude_unset=True, exclude={"job", "archived_auto"})
    if body.archived_auto:
        changes["archived"] = None
    try:
        return {"ok": True, "job": jobs.update_job(ROOT, body.job, changes)}
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

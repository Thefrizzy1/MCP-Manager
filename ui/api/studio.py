"""Studio surface: add an idea, do the next sensible thing on a video, chat, film."""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import jobs, prompter, studio
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])


def _title_from(idea: str) -> str:
    """A short working title out of the first line of an idea."""
    first = re.split(r"[.\n!?]", idea.strip(), maxsplit=1)[0]
    words = re.findall(r"[\w'’.+-]+", first)[:7]
    return " ".join(words)[:60] or "New idea"


class IdeaBody(BaseModel):
    idea: str = Field(..., min_length=5, max_length=6000)
    title: str = Field(default="", max_length=70)


@router.post("/api/v1/studio/idea")
async def api_studio_idea(body: IdeaBody):
    """The plus button: make the video folder, keep the idea, and have the
    Channel Manager set the production up (research if it needs any, then the script)."""
    try:
        job = await asyncio.to_thread(jobs.create_job, body.title or _title_from(body.idea), body.idea)
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))
    studio.post(ROOT, job, "user", body.idea)
    task = studio.add_task(ROOT, job, "intake", body.idea)
    return {"job": job, "task": task["id"]}


class ActionBody(BaseModel):
    job: str = Field(..., max_length=300)
    action: str = Field(..., max_length=30)
    note: str = Field(default="", max_length=4000)


@router.post("/api/v1/studio/action")
async def api_studio_action(body: ActionBody):
    """A context button on a video: hand that piece of work to its agent."""
    if not jobs.job_folder(body.job):
        raise HTTPException(404, "No such video")
    if body.action not in studio.KIND_AGENT or body.action in ("chat", "intake"):
        raise HTTPException(400, "Unknown action")
    task = studio.add_task(ROOT, body.job, body.action, body.note)
    studio.post(ROOT, body.job, "system", f"Asked the {task['agent']} for: {body.action.replace('_', ' ')}", task_id=task["id"])
    jobs._cache["ts"] = 0.0
    return {"task": task}


@router.get("/api/v1/studio/thread")
async def api_studio_thread(job: str = studio.CHANNEL):
    if job != studio.CHANNEL and not jobs.job_folder(job):
        raise HTTPException(404, "No such video")
    return {"job": job, "messages": studio.thread(ROOT, job),
            "tasks": [t for t in studio.tasks(ROOT, job, limit=25)]}


class SayBody(BaseModel):
    job: str = Field(default=studio.CHANNEL, max_length=300)
    text: str = Field(..., min_length=1, max_length=8000)


@router.post("/api/v1/studio/say")
async def api_studio_say(body: SayBody):
    """Write to the Channel Manager about a video (or the whole channel)."""
    if body.job != studio.CHANNEL and not jobs.job_folder(body.job):
        raise HTTPException(404, "No such video")
    msg = studio.post(ROOT, body.job, "user", body.text)
    studio.add_task(ROOT, body.job, "chat", body.text)
    return await api_studio_thread(body.job) | {"posted": msg["id"]}


class TaskBody(BaseModel):
    id: int
    status: str = Field(..., max_length=12)       # queued (retry) | cancelled | approved


@router.post("/api/v1/studio/task")
async def api_studio_task(body: TaskBody):
    try:
        return {"task": studio.set_status(ROOT, body.id, body.status)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/v1/studio/agent")
async def api_studio_agent(id: str):
    """One agent's timeline: its recent runs and what is waiting for it."""
    return {"runs": studio.runs(ROOT, id, 60),
            "queue": [t for t in studio.tasks(ROOT, open_only=True) if t["agent"] == id]}


_READ_OFF = (re.compile(r"^read off\b.*\.md$", re.I), re.compile(r"^filming\b.*\.md$", re.I),
             re.compile(r"^script\.md$", re.I))


def read_off_files(folder) -> list[str]:
    """Documents to read from while filming, best first."""
    names = sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".md")
    return [n for pat in _READ_OFF for n in names if pat.match(n)]


@router.get("/api/v1/studio/script")
async def api_studio_script(job: str):
    """The text to read while filming, split into sections. Prefers a read-off
    or filming document over the full script."""
    folder = jobs.job_folder(job)
    if not folder:
        raise HTTPException(404, "No such video")
    files = read_off_files(folder)
    if not files:
        return {"job": job, "file": "", "sections": [], "files": []}
    return {"job": job, "file": files[0], "sections": prompter.sections(jobs.read_doc(job, files[0])), "files": files}

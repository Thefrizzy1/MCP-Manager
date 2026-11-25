"""Teleprompter: uploaded scripts, the text on screen, and remote control of it."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from core import jobs, prompter
from ui.api.deps import verify_auth
from ui.api.studio import read_off_files
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])
LINK = prompter.Link()


@router.get("/api/v1/prompter")
async def api_prompter():
    return {"uploads": prompter.uploads(ROOT), "current": prompter.current(ROOT), "screen": LINK.screen()}


@router.post("/api/v1/prompter/upload")
async def api_prompter_upload(file: UploadFile = File(...), job: str = Form(default="")):
    data = await file.read(prompter.MAX_UPLOAD + 1)
    try:
        return await asyncio.to_thread(prompter.save_upload, ROOT, file.filename or "script.md", data, job)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/v1/prompter/upload/{uid}")
async def api_prompter_remove(uid: str):
    if not prompter.remove_upload(ROOT, uid):
        raise HTTPException(404, "That script is not here any more")
    return {"ok": True}


@router.get("/api/v1/prompter/script")
async def api_prompter_script(source: str):
    """``upload:<id>`` or ``job:<video folder>``."""
    kind, _, ref = source.partition(":")
    if kind == "upload":
        try:
            row = prompter.upload(ROOT, ref)
        except KeyError as e:
            raise HTTPException(404, e.args[0])
        return {"source": source, "title": row["name"], "sections": prompter.sections(row["text"])}
    if kind != "job":
        raise HTTPException(400, "unknown kind of script")
    folder = jobs.job_folder(ref)
    if not folder:
        raise HTTPException(404, "No such video")
    files = read_off_files(folder)
    if not files:
        raise HTTPException(404, "This video has no script yet. Upload one.")
    return {"source": source, "title": ref, "file": files[0],
            "sections": prompter.sections(jobs.read_doc(ref, files[0]))}


class CommandBody(BaseModel):
    cmd: str = Field(..., max_length=20)
    source: str = Field(default="", max_length=320)


@router.post("/api/v1/prompter/command")
async def api_prompter_command(body: CommandBody):
    if body.cmd == "load":
        await api_prompter_script(body.source)          # refuse what cannot be shown
        prompter.set_current(ROOT, body.source)
    try:
        return {"seq": LINK.send(body.cmd, body.source)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/v1/prompter/commands")
async def api_prompter_commands(after: int = -1):
    return await LINK.wait(after)


class StatusBody(BaseModel):
    source: str = Field(default="", max_length=320)
    title: str = Field(default="", max_length=320)
    playing: bool = False
    percent: int = Field(default=0, ge=0, le=100)
    part: str = Field(default="", max_length=300)
    speed: int = 0
    size: int = 0


@router.post("/api/v1/prompter/status")
async def api_prompter_status(body: StatusBody):
    LINK.report(body.model_dump())
    return {"ok": True}

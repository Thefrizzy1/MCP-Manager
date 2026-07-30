"""Agent surface: status, runs, playbooks, launch, login token, live console, schedules."""
from __future__ import annotations

import asyncio
import json
import shutil
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from core import agent_login, agent_runner, agent_tasks, schedule_store
from ui.api.deps import verify_auth
from ui.runtime import (
    ROOT,
    _agent_queue,
    _agent_service_disallow,
    _enqueue_agent,
    _run_task_bg,
    agent_scheduler,
)

router = APIRouter(dependencies=[Depends(verify_auth)])


# ─── Agent runner (headless Claude Code over Plutus's own MCP tools) ──────────

@router.get("/api/v1/agent/status")
async def api_v1_agent_status():
    st = agent_runner.status(ROOT)
    acfg = agent_runner.load_agent_config(ROOT)
    st["config"] = acfg
    st["claude_available"] = shutil.which("claude") is not None
    st["scheduler_available"] = agent_scheduler.available
    st["auth"] = agent_runner.auth_info()
    st["queue_depth"] = _agent_queue.qsize()
    st["runs_today"] = agent_runner.runs_today(ROOT)
    st["max_runs_per_day"] = acfg.get("max_runs_per_day", 20)
    return st


@router.post("/api/v1/agent/cancel")
async def api_v1_agent_cancel():
    return agent_runner.cancel()


@router.get("/api/v1/agent/runs")
async def api_v1_agent_runs():
    return {"runs": agent_runner.list_runs(ROOT, 30)}


@router.delete("/api/v1/agent/runs")
async def api_v1_agent_runs_clear():
    """Clear persisted run history (e.g. stale failures from before a rebuild)."""
    return {"ok": True, "cleared": agent_runner.clear_runs(ROOT)}


class AgentConfigBody(BaseModel):
    model: str | None = None
    allowed_tools: list[str] | None = None
    skip_permissions: bool | None = None
    give_plutus_tools: bool | None = None
    timeout_min: int | None = None
    max_cost_usd: float | None = None
    output_mode: str | None = None
    obsidian_folder: str | None = None
    fs_library_path: str | None = None
    notify_enabled: bool | None = None
    notify_on: str | None = None
    max_runs_per_day: int | None = None


@router.post("/api/v1/agent/config")
async def api_v1_agent_config(body: AgentConfigBody):
    return {"ok": True, "config": agent_runner.save_agent_config(ROOT, body.model_dump(exclude_none=True))}


# ─── Playbooks (reusable research tasks that build a knowledge library) ────────

@router.get("/api/v1/agent/tasks")
async def api_v1_agent_tasks():
    return {"tasks": agent_tasks.seed_if_empty(ROOT)}


class AgentTaskBody(BaseModel):
    id: str | None = None
    name: str = Field(..., min_length=1, max_length=80)
    description: str = ""
    prompt: str = Field(..., min_length=1, max_length=20000)
    model: str | None = None


@router.post("/api/v1/agent/tasks")
async def api_v1_agent_tasks_save(body: AgentTaskBody):
    try:
        task = agent_tasks.upsert_task(ROOT, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "task": task, "tasks": agent_tasks.load_tasks(ROOT)}


@router.delete("/api/v1/agent/tasks/{tid}")
async def api_v1_agent_tasks_delete(tid: str):
    if not agent_tasks.delete_task(ROOT, tid):
        raise HTTPException(404, "playbook not found")
    return {"ok": True, "tasks": agent_tasks.load_tasks(ROOT)}


@router.post("/api/v1/agent/tasks/{tid}/run")
async def api_v1_agent_tasks_run(tid: str):
    task = agent_tasks.get_task(ROOT, tid)
    if not task:
        raise HTTPException(404, "playbook not found")
    _run_task_bg(tid, force=True)
    return {"ok": True, "queued": agent_runner._current["running"]}


@router.post("/api/v1/agent/tasks/{tid}/preview")
async def api_v1_agent_tasks_preview(tid: str):
    """Return the fully-rendered prompt for a playbook without running it."""
    task = agent_tasks.get_task(ROOT, tid)
    if not task:
        raise HTTPException(404, "playbook not found")
    acfg = agent_runner.load_agent_config(ROOT)
    lib, hint = agent_runner.resolve_library(acfg)
    prompt = agent_tasks.render_prompt(task["prompt"], library=lib,
                                       date=time.strftime("%Y-%m-%d"), output_hint=hint)
    return {"ok": True, "prompt": prompt, "library": lib}


class BuildTaskBody(BaseModel):
    description: str = Field(..., min_length=3, max_length=4000)


@router.post("/api/v1/agent/tasks/build")
async def api_v1_agent_tasks_build(body: BuildTaskBody):
    """Use the running Claude Code to DRAFT a playbook prompt from a description."""
    acfg = agent_runner.load_agent_config(ROOT)
    lib, hint = agent_runner.resolve_library(acfg)
    meta = agent_tasks.build_meta_prompt(body.description, library=lib, output_hint=hint)
    res = await asyncio.to_thread(agent_runner.build_text, ROOT, meta)
    if not res["ok"]:
        return JSONResponse({"ok": False, "error": res["error"]}, status_code=200)
    return {"ok": True, "prompt": res["text"]}


class AgentRunBody(BaseModel):
    """Launch payload.

    There is no access level and no timeout: timeout is baked into the agent
    config, and permission levels are gone entirely. Selecting a connection grants
    the agent read *and* write on that service's tools — the connection list is
    the one and only control over what a launched agent may touch."""

    prompt: str = Field(..., min_length=1, max_length=20000)
    label: str = Field(default="agent", max_length=40)
    mcp_services: list[str] | None = None  # per-connection ACL; None = no restriction
    # Which authenticated CLI account executes the run. Empty = the legacy single
    # login (mounted ~/.claude, else a saved token).
    provider: str = ""
    account_id: str = ""


@router.post("/api/v1/agent/run")
async def api_v1_agent_run(body: AgentRunBody):
    extra = _agent_service_disallow(body.mcp_services)
    ok = _enqueue_agent(body.prompt, body.label or "agent", force=True,
                        extra_disallowed=extra, mcp_services=body.mcp_services,
                        provider=body.provider, account_id=body.account_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "Agent queue is full."}, status_code=429)
    return {"ok": True, "queued": agent_runner._current["running"]}


@router.get("/api/v1/agent/runs/{rid}/transcript")
async def api_v1_agent_run_transcript(rid: str):
    """Full detail for one run: assistant messages, every tool call with its
    arguments, and every tool result.

    The console log only ever held one-line summaries, so after a run there was no
    way to see which tools actually ran or what they returned — a run could report
    success while you could not find what it claimed to have written."""
    rec = agent_runner.get_run(ROOT, rid)
    if not rec:
        raise HTTPException(404, "run not found")
    entries = agent_runner.get_transcript(ROOT, rid)
    return {
        "id": rid,
        "label": rec.get("label"),
        "ok": rec.get("ok"),
        "error": rec.get("error"),
        "auth_source": rec.get("auth_source"),
        "mcp_services": rec.get("mcp_services"),
        # Runs recorded before transcripts existed have none; say so rather than
        # rendering an empty panel that looks broken.
        "available": entries is not None,
        "entries": entries or [],
        "log": rec.get("log") or [],
    }


@router.get("/api/v1/agent/runs/{rid}")
async def api_v1_agent_run_detail(rid: str):
    """One past run, shaped for prefilling the launch wizard.

    "Run again" opens the wizard rather than firing immediately, so the prompt,
    connection scope and account can be adjusted before spending another run —
    a repeat is usually a repeat *with a tweak*.
    """
    rec = agent_runner.get_run(ROOT, rid)
    if not rec:
        raise HTTPException(404, "run not found")
    return {
        "id": rec.get("id"),
        "label": rec.get("label") or "agent",
        "prompt": rec.get("prompt") or "",
        # None means "no restriction" — distinct from an empty list, which means
        # "every connection was deselected". The wizard has to tell them apart.
        "mcp_services": rec.get("mcp_services"),
        "provider": rec.get("provider") or "",
        "account_id": rec.get("account_id") or "",
        "started": rec.get("started"),
        "ok": rec.get("ok"),
    }


# ─── Web login (session/OAuth token — never an API key) ───────────────────────

class LoginTokenBody(BaseModel):
    token: str = Field(..., min_length=8, max_length=2000)


@router.post("/api/v1/agent/login/token")
async def api_v1_agent_login_token(body: LoginTokenBody):
    res = agent_login.save_token(body.token)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@router.get("/api/v1/agent/stream")
async def api_v1_agent_stream():
    """Server-sent live console for the current/last agent run."""
    async def gen():
        sent = 0
        while True:
            lines = agent_runner.LIVE["lines"]
            while sent < len(lines):
                yield f"data: {json.dumps(lines[sent])}\n\n"
                sent += 1
            if agent_runner.LIVE["done"]:
                yield "event: end\ndata: done\n\n"
                break
            await asyncio.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream")


# ─── Scheduler (schedule any agent task or tool call on cron) ──────────────────

def _schedule_view() -> dict:
    items = schedule_store.load_schedules(ROOT)
    nexts = agent_scheduler.next_run_times()
    for it in items:
        it["next_run"] = nexts.get(it["id"])
    return {"schedules": items, "scheduler_available": agent_scheduler.available}


@router.get("/api/v1/schedules")
async def api_v1_schedules_get():
    return _schedule_view()


class ScheduleBody(BaseModel):
    id: str | None = None
    name: str = ""
    kind: str = Field(..., pattern="^(agent|tool|task)$")
    cron: str = Field(..., min_length=1, max_length=120)
    timezone: str = "Europe/Berlin"
    enabled: bool = True
    payload: dict = Field(default_factory=dict)


@router.post("/api/v1/schedules")
async def api_v1_schedules_add(body: ScheduleBody):
    try:
        entry = schedule_store.add_schedule(ROOT, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    agent_scheduler.reschedule()
    return {"ok": True, "schedule": entry, **_schedule_view()}


@router.post("/api/v1/schedules/{sid}")
async def api_v1_schedules_update(sid: str, body: ScheduleBody):
    try:
        entry = schedule_store.update_schedule(ROOT, sid, body.model_dump(exclude={"id"}))
    except KeyError:
        raise HTTPException(404, "schedule not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    agent_scheduler.reschedule()
    return {"ok": True, "schedule": entry, **_schedule_view()}


@router.delete("/api/v1/schedules/{sid}")
async def api_v1_schedules_delete(sid: str):
    if not schedule_store.delete_schedule(ROOT, sid):
        raise HTTPException(404, "schedule not found")
    agent_scheduler.reschedule()
    return {"ok": True, **_schedule_view()}


@router.post("/api/v1/schedules/{sid}/run-now")
async def api_v1_schedules_run_now(sid: str):
    sc = schedule_store.get_schedule(ROOT, sid)
    if not sc:
        raise HTTPException(404, "schedule not found")
    agent_scheduler.run_now(sc)
    return {"ok": True}

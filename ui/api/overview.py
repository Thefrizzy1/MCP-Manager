"""Overview surface: machines, workers, and the calendar feed."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from core import calendar_feed, fleet, jobs, lab_board, studio
from core import trend_radar as tr
from tools.jobs import sync_uploads
from ui.api.deps import verify_auth
from ui.runtime import ROOT

router = APIRouter(dependencies=[Depends(verify_auth)])
# The feed itself: calendar apps cannot log in, so the token in the URL is the key.
public_router = APIRouter()


def _live_workers() -> dict[str, dict]:
    live: dict[str, dict] = {}
    hb = lab_board.heartbeat(ROOT)
    if hb.get("ts"):
        testing = any(m.get("status") == "testing" for m in lab_board.entries(ROOT).values())
        queued = sum(1 for m in lab_board.entries(ROOT).values() if m.get("status") == "queued")
        live["workflow_maker"] = {"text": hb.get("text", ""), "ts": hb["ts"], "busy": testing,
                                  "next": f"{queued} model(s) queued" if queued else ""}
    try:
        runs = tr.runs(ROOT, 1)
    except Exception:
        runs = []
    if runs:
        r = runs[0]
        d = r.get("detail") or {}
        found = d.get("new_signals", d.get("signals"))
        live["researcher"] = {
            "text": "Trend Radar scan" + (f": {found} new signals" if found is not None else " finished"),
            "ts": r.get("finished") or r.get("started") or 0, "busy": not r.get("finished")}
    return live


async def _events() -> list[dict]:
    await sync_uploads(ROOT)          # no-op while the stored list is fresh
    board = await asyncio.to_thread(jobs.list_jobs, ROOT)
    return calendar_feed.collect(ROOT, board["jobs"], jobs.load_uploads(ROOT)["videos"],
                                 lab_board.entries(ROOT))


@router.get("/api/v1/overview")
async def api_v1_overview():
    events = await _events()
    return {
        "machines": fleet.machines(ROOT, str(jobs.youtube_root()) if jobs.youtube_root().is_dir() else "/"),
        "workers": fleet.workers(ROOT, _live_workers(), studio.agent_states(ROOT)),
        "models": list(fleet.MODELS),
        "calendar": {"path": f"/calendar/{calendar_feed.token(ROOT)}.ics", "events": len(events),
                     "upcoming": [e for e in events if e["date"] >= _today()][:12]},
    }


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


class ScheduleBody(BaseModel):
    machine: str = Field(..., max_length=60)
    enabled: bool = True
    start: str = Field(..., max_length=5)
    end: str = Field(..., max_length=5)
    days: list[int] = Field(default_factory=lambda: list(range(7)), max_length=7)
    may_wake: bool = False
    may_power_off: bool = False


@router.post("/api/v1/overview/schedule")
async def api_v1_overview_schedule(body: ScheduleBody):
    """Set the hours a machine may be switched on to work by itself."""
    try:
        fleet.set_schedule(ROOT, body.machine, body.model_dump(exclude={"machine"}))
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await api_v1_overview()


class WorkerBody(BaseModel):
    worker: str = Field(..., max_length=40)
    model: str = Field(default="", max_length=20)
    home: str = Field(default="", max_length=60)
    schedule: str | None = Field(default=None, max_length=5)
    enabled: bool | None = None


@router.post("/api/v1/overview/worker")
async def api_v1_overview_worker(body: WorkerBody):
    """Change a worker's model or the machine it lives on."""
    try:
        fleet.set_worker(ROOT, body.worker, body.model, body.home, body.schedule, body.enabled)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await api_v1_overview()


class TokenBody(BaseModel):
    confirm: bool = Field(default=False)


@router.post("/api/v1/calendar/new_link")
async def api_v1_calendar_new_link(body: TokenBody):
    """Replace the feed link. Everything subscribed to the old one stops updating."""
    if not body.confirm:
        raise HTTPException(400, "confirm is required")
    return {"path": f"/calendar/{calendar_feed.token(ROOT, regenerate=True)}.ics"}


@public_router.get("/calendar/{token}.ics")
async def calendar_ics(token: str):
    if not calendar_feed.check_token(ROOT, token):
        raise HTTPException(404, "Not found")
    return Response(calendar_feed.to_ics(await _events(), "YouTube channel"),
                    media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "private, max-age=900"})

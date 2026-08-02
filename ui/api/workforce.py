"""Rooms: teams of agents that run in order on a shared brief.

Strictly additive — none of this changes how a single agent is launched from the
Agents page. A room run is a sequence of ordinary agent runs, so each step lands in
the normal run history with its own transcript and cost.
"""
from __future__ import annotations

import asyncio
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import agent_runner, workforce
from ui.api.deps import verify_auth
from ui.runtime import ROOT, _agent_mcp_target

router = APIRouter(dependencies=[Depends(verify_auth)])

# One room at a time — the lock lives in core.workforce because rooms can also be
# started over MCP, and a lock per entry point would not serialise the two.


def _room_or_404(room_id: str) -> dict:
    room = workforce.get_room(ROOT, room_id)
    if not room:
        raise HTTPException(404, "room not found")
    return room


@router.get("/api/v1/rooms")
async def api_rooms():
    return {
        "rooms": workforce.load_rooms(ROOT),
        "roles": list(workforce.ROLES),
        "live": dict(workforce.LIVE),
        "runs": workforce.list_room_runs(ROOT, 10),
    }


class RoomBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=60)
    mcp_services: list[str] | None = None


@router.post("/api/v1/rooms")
async def api_add_room(body: RoomBody):
    try:
        room = workforce.add_room(ROOT, body.label, mcp_services=body.mcp_services)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "room": room}


class RoomPatch(BaseModel):
    label: str | None = None
    brief: str | None = None
    mcp_services: list[str] | None = None
    # The room to run once this one succeeds. core.workforce has honoured this
    # since the handoff landed, but it was never accepted here or offered in the
    # UI, so the whole chain feature was unreachable. "" clears it.
    next_room: str | None = None


@router.post("/api/v1/rooms/{room_id}")
async def api_update_room(room_id: str, body: RoomPatch):
    _room_or_404(room_id)
    nxt = (body.next_room or "").strip()
    if nxt:
        # Caught here as well as at run time so the mistake surfaces while you are
        # wiring the chain, not silently as a handoff that never happens.
        if nxt == room_id:
            raise HTTPException(400, "a room cannot hand off to itself")
        if not workforce.get_room(ROOT, nxt):
            raise HTTPException(404, f"no room '{nxt}' to hand off to")
    try:
        return {"ok": True, "room": workforce.update_room(ROOT, room_id, body.model_dump(exclude_none=True))}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.delete("/api/v1/rooms/{room_id}")
async def api_delete_room(room_id: str):
    if not workforce.delete_room(ROOT, room_id):
        raise HTTPException(404, "room not found")
    return {"ok": True}


class SeatBody(BaseModel):
    role: str = Field(default=workforce.DEFAULT_ROLE)
    provider: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    goal: str = ""
    label: str = ""
    model: str = Field(default="", max_length=80)


@router.post("/api/v1/rooms/{room_id}/seats")
async def api_add_seat(room_id: str, body: SeatBody):
    _room_or_404(room_id)
    try:
        seat = workforce.add_seat(ROOT, room_id, role=body.role, provider=body.provider,
                                  account_id=body.account_id, goal=body.goal,
                                  label=body.label, model=body.model)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "seat": seat, "room": workforce.get_room(ROOT, room_id)}


class SeatPatch(BaseModel):
    role: str | None = None
    label: str | None = None
    goal: str | None = None
    provider: str | None = None
    account_id: str | None = None
    model: str | None = None


@router.post("/api/v1/rooms/{room_id}/seats/{seat_id}")
async def api_update_seat(room_id: str, seat_id: str, body: SeatPatch):
    _room_or_404(room_id)
    try:
        workforce.update_seat(ROOT, room_id, seat_id, body.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(404, "seat not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "room": workforce.get_room(ROOT, room_id)}


@router.delete("/api/v1/rooms/{room_id}/seats/{seat_id}")
async def api_remove_seat(room_id: str, seat_id: str):
    if not workforce.remove_seat(ROOT, room_id, seat_id):
        raise HTTPException(404, "seat not found")
    return {"ok": True, "room": workforce.get_room(ROOT, room_id)}


class OrderBody(BaseModel):
    seat_ids: list[str]


@router.post("/api/v1/rooms/{room_id}/order")
async def api_reorder(room_id: str, body: OrderBody):
    _room_or_404(room_id)
    try:
        room = workforce.reorder_seats(ROOT, room_id, body.seat_ids)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "room": room}


class RunBody(BaseModel):
    brief: str = Field(default="", max_length=20000)


@router.post("/api/v1/rooms/{room_id}/run")
async def api_run_room(room_id: str, body: RunBody):
    """Start the room in the background and return immediately.

    Runs on its own thread rather than the agent queue: run_agent already refuses
    to start while another run is in flight, so the room's seats serialise against
    single-agent launches without either side knowing about the other.
    """
    room = _room_or_404(room_id)
    if workforce.LIVE.get("running"):
        raise HTTPException(409, "a room is already running")
    if not (room.get("seats") or []):
        raise HTTPException(400, "this room has no agents in it yet — drag one in first")

    acfg = agent_runner.load_agent_config(ROOT)
    cap = float(acfg.get("max_cost_usd", 2.0) or 2.0) * 4  # a room is several runs
    slot_wait = max(60, agent_runner._timeout_min(acfg) * 60 + 120)

    def _work():
        with workforce.RUN_LOCK:
            url, token = _agent_mcp_target()

            def _run(root, prompt, **kw):
                # "Refuses" is not "queues": run_agent returns an error the
                # instant another run holds the slot, so a room launched while
                # the agent queue was busy used to fail on its first seat. Wait
                # the slot out — up to one full run's timeout, since that is the
                # longest the thing ahead of us can legitimately take.
                if not agent_runner.wait_for_slot(slot_wait):
                    return {"ok": False, "cost_usd": 0.0, "result": "",
                            "error": "another agent run held the runner for too long"}
                return agent_runner.run_agent(root, prompt, mcp_url=url, bearer_token=token, **kw)

            try:
                workforce.run_room(ROOT, room_id, body.brief, run_agent=_run, max_cost_usd=cap)
            except Exception:
                pass          # the record already carries the error

    threading.Thread(target=_work, name=f"room-{room_id}", daemon=True).start()
    return {"ok": True, "started": True}


@router.get("/api/v1/rooms/runs/{run_id}")
async def api_room_run(run_id: str):
    rec = workforce.get_room_run(ROOT, run_id)
    if not rec:
        raise HTTPException(404, "room run not found")
    return rec

"""Rooms: teams of agents that run in order on a shared brief.

Strictly additive — none of this changes how a single agent is launched from the
Agents page. A room run is a sequence of ordinary agent runs, so each step lands in
the normal run history with its own transcript and cost.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import agent_orchestrator, room_presets, workforce
from ui.api.deps import verify_auth
from ui.runtime import ROOT

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
    # Which rooms run on their own, keyed by room id. A room can now be a
    # schedule, and "does this run without me" is a property of the room you want
    # to see *on* the room — not something to go hunting for on another page.
    from core import schedule_store

    scheduled = {
        (s.get("payload") or {}).get("room_id"): {
            "cron": s.get("cron", ""),
            "timezone": s.get("timezone", ""),
            "enabled": bool(s.get("enabled", True)),
            "name": s.get("name", ""),
        }
        for s in schedule_store.load_schedules(ROOT)
        if s.get("kind") == "room" and (s.get("payload") or {}).get("room_id")
    }
    return {
        "rooms": workforce.load_rooms(ROOT),
        "roles": list(workforce.ROLES),
        # Read from disk, not from this process's copy: a room started by an
        # agent over MCP runs in the *other* process, and the in-memory LIVE
        # here would never learn about it — the floor would sit idle through
        # a whole run.
        "live": workforce.read_live(ROOT),
        "runs": workforce.list_room_runs(ROOT, 10),
        "scheduled": scheduled,
        "presets": room_presets.public_presets(),
        "colours": list(room_presets.COLOURS),
    }


class PresetBody(BaseModel):
    provider: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    model: str = Field(default="", max_length=80)


@router.post("/api/v1/rooms/presets/{preset_id}")
async def api_install_preset(preset_id: str, body: PresetBody):
    """Create a whole pipeline — rooms, seats, brief, chain — in one go."""
    try:
        rooms = room_presets.install(ROOT, preset_id, provider=body.provider,
                                     account_id=body.account_id, model=body.model)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "rooms": rooms}


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
    colour: str | None = None


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


class FromAgentBody(BaseModel):
    """Which desk the agent was dropped on, when it was dropped on one."""
    role: str = ""


@router.post("/api/v1/rooms/{room_id}/seats/from-agent/{agent_id}")
async def api_seat_from_agent(room_id: str, agent_id: str, body: FromAgentBody):
    """Seat a saved agent: its account, model, role and goal, in one drop.

    The seat *copies* the settings rather than referencing the agent, so editing
    or deleting a saved agent never reaches back into rooms that were staffed
    from it — a room that worked yesterday works today.

    The room's connections are widened to include the agent's. An agent is the
    tools it was given as much as the account it runs on, and a seat quietly
    missing half of them is the kind of failure that looks like a bad model
    rather than a bad drop. Explicit in the response so it is not a surprise;
    ``None`` on the agent means "no restriction" and never widens anything.
    """
    from core import saved_agents

    room = _room_or_404(room_id)
    agent = saved_agents.get_agent(ROOT, agent_id)
    if not agent:
        raise HTTPException(404, "no such saved agent")

    try:
        seat = workforce.add_seat(
            ROOT, room_id,
            role=(body.role or agent.get("role") or workforce.DEFAULT_ROLE),
            provider=agent["provider"], account_id=agent["account_id"],
            label=agent.get("label", ""), goal=agent.get("goal", ""),
            model=agent.get("model", ""))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))

    added: list[str] = []
    wanted = agent.get("mcp_services")
    if wanted:
        have = list(room.get("mcp_services") or [])
        added = [s for s in wanted if s not in have]
        if added:
            workforce.update_room(ROOT, room_id, {"mcp_services": have + added})

    return {"ok": True, "seat": seat, "added_connections": added,
            "room": workforce.get_room(ROOT, room_id)}


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
    if workforce.read_live(ROOT).get("running"):
        raise HTTPException(409, "a room is already running")
    if not (room.get("seats") or []):
        raise HTTPException(400, "this room has no agents in it yet — drag one in first")

    from config import cfg

    res = agent_orchestrator.launch_room(ROOT, cfg, room_id, body.brief)
    if not res["ok"]:
        raise HTTPException(400, res["error"])
    return {"ok": True, "started": True}


@router.get("/api/v1/rooms/runs/{run_id}")
async def api_room_run(run_id: str):
    rec = workforce.get_room_run(ROOT, run_id)
    if not rec:
        raise HTTPException(404, "room run not found")
    return rec

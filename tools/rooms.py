"""
tools/rooms.py — build and run agent rooms through Plutus's own MCP router.

Everything the Rooms page can do, an MCP client can do: create a room, staff it,
wire the chain, start it, read the result. Same core.workforce functions behind
both, so there is no second definition of what a room is.

Why running is guarded: a room's seats call the agent runner, which holds a
single execution slot. If an agent that is *itself* running calls room_run, the
room queues behind its own caller, and a room whose seat starts that same room
never stops. So room_run refuses while a run is in flight and says why. Building,
inspecting and reading results stay available at all times — those are the parts
an agent mid-run actually wants.
"""
import time
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from config import cfg
from core import agent_orchestrator, ai_providers, workforce

_ROOT = Path(__file__).resolve().parents[1]


def _room_line(room: dict) -> str:
    seats = room.get("seats") or []
    who = ", ".join(f"{s.get('label') or s['role']} ({s['role']}/{s.get('provider', '?')})"
                    for s in seats) or "empty — no agents yet"
    out = [f"- **{room.get('label')}** `{room['id']}`",
           f"  - seats in order: {who}",
           f"  - connections: {', '.join(room.get('mcp_services') or []) or '(none)'}"]
    if room.get("brief"):
        out.append(f"  - brief: {room['brief'][:200]}")
    if room.get("next_room"):
        nxt = workforce.get_room(_ROOT, room["next_room"])
        out.append(f"  - hands off to: {nxt.get('label') if nxt else '(missing room)'} "
                   f"`{room['next_room']}`")
    return "\n".join(out)


def register_room_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class RoomId(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        room_id: str = Field(..., description="Room id from room_list", min_length=1, max_length=80)

    @mcp.tool(name="room_list", annotations={"readOnlyHint": True})
    async def room_list() -> str:
        """List every agent room, its seats in running order, and what it hands off to.

        Also reports the roles and provider accounts available to staff a room
        with, so this one call tells you everything needed to build the next one.
        """
        rooms = workforce.load_rooms(_ROOT)
        parts = ["# Rooms", ""]
        parts.append("\n".join(_room_line(r) for r in rooms) if rooms
                     else "_No rooms yet. Create one with room_create._")

        live = workforce.LIVE
        if live.get("running"):
            parts += ["", f"**A room is running now:** `{live.get('room_id')}` "
                          f"(run `{live.get('run_id')}`)."]

        accounts = ai_providers.load_accounts(_ROOT)
        staff = [f"- `{pid}` / `{a['id']}` — {a.get('label', a['id'])}"
                 for pid, lst in accounts.items() for a in lst]
        parts += ["", "## Available to staff seats with", ""]
        parts += staff or ["_No provider accounts configured — add one in Settings first._"]
        parts += ["", f"Roles: {', '.join(workforce.ROLES)}"]
        return "\n".join(parts)

    class CreateInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        label: str = Field(..., description="Name of the room", min_length=1, max_length=60)
        brief: str = Field(default="", description="What the whole room works on", max_length=20_000)
        mcp_services: str = Field(default="", max_length=2000,
                                  description="Comma-separated connection ids every seat gets")

    @mcp.tool(name="room_create", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def room_create(params: CreateInput) -> str:
        """Create an agent room. Returns the room id needed to staff and run it."""
        services = [s.strip() for s in (params.mcp_services or "").split(",") if s.strip()]
        try:
            room = workforce.add_room(_ROOT, params.label, mcp_services=services)
            if params.brief:
                room = workforce.update_room(_ROOT, room["id"], {"brief": params.brief})
        except (KeyError, ValueError) as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: could not create the room: {e}"
        return f"✓ Created and read back.\n\n{_room_line(room)}\n\nStaff it with room_add_seat."

    class UpdateInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        room_id: str = Field(..., min_length=1, max_length=80)
        label: Optional[str] = Field(default=None, max_length=60)
        brief: Optional[str] = Field(default=None, max_length=20_000)
        mcp_services: Optional[str] = Field(
            default=None, max_length=2000,
            description="Comma-separated connection ids; empty string clears them")
        next_room: Optional[str] = Field(
            default=None, max_length=80,
            description="Room id to run after this one succeeds; empty string clears it")

    @mcp.tool(name="room_update", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def room_update(params: UpdateInput) -> str:
        """Change a room's name, brief, connections, or which room runs after it.

        Setting next_room builds a pipeline: the next room starts on the same
        working folder and is told what this room produced, so research reaches
        the room that builds on it as files rather than as re-derived text.
        """
        if not workforce.get_room(_ROOT, params.room_id):
            return f"Error: no room with id '{params.room_id}'. Call room_list."
        changes: dict = {}
        if params.label is not None:
            changes["label"] = params.label
        if params.brief is not None:
            changes["brief"] = params.brief
        if params.mcp_services is not None:
            changes["mcp_services"] = [s.strip() for s in params.mcp_services.split(",") if s.strip()]
        if params.next_room is not None:
            nxt = params.next_room.strip()
            if nxt == params.room_id:
                return "Error: a room cannot hand off to itself."
            if nxt and not workforce.get_room(_ROOT, nxt):
                return f"Error: no room '{nxt}' to hand off to. Call room_list."
            changes["next_room"] = nxt
        if not changes:
            return "Error: nothing to change — pass at least one field."
        try:
            room = workforce.update_room(_ROOT, params.room_id, changes)
        except (KeyError, ValueError) as e:
            return f"Error: {e}"
        return f"✓ Updated and read back.\n\n{_room_line(room)}"

    @mcp.tool(name="room_delete", annotations={"readOnlyHint": False, "destructiveHint": True})
    async def room_delete(params: RoomId) -> str:
        """Delete a room and its seats. Past run records are kept."""
        room = workforce.get_room(_ROOT, params.room_id)
        if not room:
            return f"Error: no room with id '{params.room_id}'."
        if not workforce.delete_room(_ROOT, params.room_id):
            return f"Error: could not delete '{params.room_id}'."
        return f"✓ Deleted room **{room.get('label')}** and its {len(room.get('seats') or [])} seat(s)."

    class SeatInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        room_id: str = Field(..., min_length=1, max_length=80)
        provider: str = Field(..., description="Provider id from room_list", min_length=1, max_length=40)
        account_id: str = Field(..., description="Account id from room_list", min_length=1, max_length=80)
        role: str = Field(default=workforce.DEFAULT_ROLE, max_length=40,
                          description=f"One of: {', '.join(workforce.ROLES)}")
        goal: str = Field(default="", description="What this seat specifically does", max_length=500)
        label: str = Field(default="", description="Display name for the seat", max_length=40)
        model: str = Field(default="", description="Model id; empty uses the account default", max_length=80)

    @mcp.tool(name="room_add_seat", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def room_add_seat(params: SeatInput) -> str:
        """Add an agent to a room. Seats run in the order they are added."""
        if not workforce.get_room(_ROOT, params.room_id):
            return f"Error: no room with id '{params.room_id}'. Call room_list."
        if not ai_providers.get_account(_ROOT, params.provider, params.account_id):
            return (f"Error: no account '{params.account_id}' on provider '{params.provider}'. "
                    f"Call room_list for the accounts that exist.")
        try:
            workforce.add_seat(_ROOT, params.room_id, role=params.role, provider=params.provider,
                               account_id=params.account_id, goal=params.goal,
                               label=params.label, model=params.model)
        except (KeyError, ValueError) as e:
            return f"Error: {e}"
        room = workforce.get_room(_ROOT, params.room_id)
        return f"✓ Seat added and read back.\n\n{_room_line(room)}"

    class SeatRef(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        room_id: str = Field(..., min_length=1, max_length=80)
        seat_id: str = Field(..., min_length=1, max_length=80)

    @mcp.tool(name="room_remove_seat", annotations={"readOnlyHint": False, "destructiveHint": True})
    async def room_remove_seat(params: SeatRef) -> str:
        """Remove one agent from a room."""
        if not workforce.remove_seat(_ROOT, params.room_id, params.seat_id):
            return f"Error: no seat '{params.seat_id}' in room '{params.room_id}'."
        room = workforce.get_room(_ROOT, params.room_id)
        return f"✓ Seat removed.\n\n{_room_line(room)}"

    class RunInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        room_id: str = Field(..., min_length=1, max_length=80)
        brief: str = Field(default="", description="Overrides the room's saved brief for this run",
                           max_length=20_000)

    @mcp.tool(name="room_run", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def room_run(params: RunInput) -> str:
        """Start a room. Every seat runs in order, then the chain, if one is set.

        Returns as soon as the room is started — rooms take minutes. Poll
        room_result with the returned run id to see how it went.
        """
        room = workforce.get_room(_ROOT, params.room_id)
        if not room:
            return f"Error: no room with id '{params.room_id}'. Call room_list."
        if not (room.get("seats") or []):
            return f"Error: room '{room.get('label')}' has no agents in it. Use room_add_seat first."
        if workforce.LIVE.get("running"):
            return (f"Error: room `{workforce.LIVE.get('room_id')}` is already running. "
                    f"Rooms run one at a time.")

        from core import agent_runner
        if agent_runner.busy():
            # Almost certainly this call came from inside an agent run. Starting a
            # room here would queue it behind its own caller, and a seat that
            # starts its own room would never stop. Refuse and say so.
            return ("Error: an agent run is in flight, so a room cannot be started right "
                    "now. If you are an agent inside a run, you cannot launch a room — "
                    "finish and report instead; a room started from here would wait on "
                    "you and could start itself again without end.")

        acfg = agent_runner.load_agent_config(_ROOT)
        cap = float(acfg.get("max_cost_usd", 2.0) or 2.0) * 4
        res = agent_orchestrator.launch_room(_ROOT, cfg, params.room_id, params.brief)
        if not res["ok"]:
            return f"Error: {res['error']}"

        # The run id is assigned by the worker thread a moment after it starts,
        # and it is published to disk for exactly this reason — the tool may be
        # answering from a different process than the one running the room.
        rid = ""
        for _ in range(40):
            live = workforce.read_live(_ROOT)
            if live.get("running") and live.get("room_id") == params.room_id:
                rid = live.get("run_id", "")
                break
            time.sleep(0.25)

        return (f"✓ Started **{room.get('label')}** with {len(room['seats'])} seat(s), "
                f"budget ${cap:.2f}.\n\n"
                f"- run id: `{rid or '(assigning)'}`\n"
                f"- working folder: `{workforce.room_folder(_ROOT, room)}`\n\n"
                f"Rooms take minutes. Check room_result(run_id=\"{rid}\") for the outcome — "
                f"do not assume it succeeded.")

    class AdviseInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        note: str = Field(..., min_length=1, max_length=1200,
                          description="What the seats after you should do differently")
        run_id: str = Field(default="", max_length=80,
                            description="Defaults to the room run you are part of")

    @mcp.tool(name="room_advise", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def room_advise(params: AdviseInput) -> str:
        """Redirect the seats that come after you in this room.

        Use this when you find something that changes what the *next* seat should
        do — the brief named a library that is deprecated, the data says the
        question was wrong, a whole line of work is a dead end. Your normal output
        is passed on as material; this is passed on as an instruction, above the
        brief, and it overrides the brief where they conflict.

        Keep it to what should change and why. It is not a place for findings —
        those belong in your working folder.
        """
        live = workforce.read_live(_ROOT)
        # Only a run that is actually in flight, not merely the last one seen:
        # run_id outlives a finished room, so falling back to it would let a
        # single agent leave advice for a room that stopped hours ago.
        run_id = params.run_id.strip() or (live.get("run_id", "") if live.get("running") else "")
        if not run_id:
            return ("Error: no room run to advise. room_advise only applies while a "
                    "room is running; a single agent run has no seats after it.")
        if not workforce.get_room_run(_ROOT, run_id) and live.get("run_id") != run_id:
            return f"Error: no room run '{run_id}'."
        try:
            items = workforce.add_advice(_ROOT, run_id, params.note)
        except ValueError as e:
            return f"Error: {e}"
        return (f"✓ Recorded and read back — note {len(items)} of "
                f"{workforce.MAX_ADVICE} for run `{run_id}`.\n\n"
                f"> {items[-1]['note']}\n\n"
                f"Every seat after you sees this above the brief. It does not reach "
                f"seats that have already run.")

    class ResultInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        run_id: str = Field(default="", description="Run id; empty returns the most recent runs",
                            max_length=80)

    @mcp.tool(name="room_result", annotations={"readOnlyHint": True})
    async def room_result(params: ResultInput) -> str:
        """Read how a room run went, step by step, with each seat's output."""
        if not params.run_id:
            runs = workforce.list_room_runs(_ROOT, 10)
            if not runs:
                return "No room runs yet."
            return "# Recent room runs\n\n" + "\n".join(
                f"- `{r['id']}` — {r.get('room_label')} — "
                f"{'ok' if r.get('ok') else 'FAILED'} — ${r.get('cost_usd', 0):.4f} "
                f"— {r.get('started')}" for r in runs)

        rec = workforce.get_room_run(_ROOT, params.run_id)
        if not rec:
            return f"Error: no room run '{params.run_id}'."
        live = " (still running)" if (workforce.LIVE.get("running")
                                     and workforce.LIVE.get("run_id") == rec["id"]) else ""
        out = [f"# {rec.get('room_label')} — {'ok' if rec.get('ok') else 'FAILED'}{live}", "",
               f"- run id: `{rec['id']}`",
               f"- started: {rec.get('started')}  finished: {rec.get('finished') or '—'}",
               f"- cost: ${rec.get('cost_usd', 0):.4f}",
               f"- working folder: `{rec.get('folder') or '—'}`"]
        if rec.get("error"):
            out.append(f"- error: {rec['error']}")
        if rec.get("next_run_id"):
            out.append(f"- handed off to run `{rec['next_run_id']}`")
        if rec.get("next_error"):
            out.append(f"- handoff error: {rec['next_error']}")
        out += ["", "## Steps", ""]
        for i, s in enumerate(rec.get("steps") or [], 1):
            body = (s.get("result") or "").strip()
            if len(body) > 4000:
                body = body[:4000] + "\n…(truncated — the full output is in the run record)"
            out.append(f"### {i}. {s.get('label')} ({s.get('role')}) — "
                       f"{'ok' if s.get('ok') else 'FAILED'} ${s.get('cost_usd', 0):.4f}")
            if s.get("error"):
                out.append(f"error: {s['error']}")
            out += [body or "(no output)", ""]
        return "\n".join(out)

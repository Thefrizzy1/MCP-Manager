"""Rooms — a small team of agents working one after another on a shared brief.

A **room** owns the things a team shares: which MCP connections its agents may
touch, and (later) a schedule. A **seat** is one agent in that room: a role, the
provider account that runs it, and a short goal. Seats run in order, and each one
sees the brief plus everything the seats before it produced — so a manager seat
placed after a researcher can audit and redirect that work, which is the whole
point of putting them in a room together.

Deliberately additive. Nothing here changes how a single agent is launched from
the Agents page: rooms *call* ``agent_runner.run_agent`` exactly as that page's
queue does, and a room is only ever a sequence of ordinary runs. Every run shows
up in the normal history with its own transcript and cost.

Sequential on purpose, for now. ``run_agent`` refuses to start while another run
is in flight (one ``_current``, one queue worker), so a room waits for a free slot
between seats rather than pretending to run its agents in parallel. Concurrency is
a separate change to the runner — see docs/WORKFORCE.md.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

ROOMS_FILE = "workforce.json"
RUNS_DIR = "room_runs"

ROLES = ("manager", "researcher", "developer", "reviewer", "writer")
DEFAULT_ROLE = "researcher"

# How much of an earlier seat's output to hand the next one. A long pipeline
# otherwise grows its prompt without limit and step four blows the context window.
MAX_HANDOFF_CHARS = 6000

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_LOCK = threading.Lock()


# ── storage ──────────────────────────────────────────────────────────────────

def _path(root: Path) -> Path:
    return Path(root) / "data" / ROOMS_FILE


def _slug(label: str) -> str:
    return _SLUG_RE.sub("-", (label or "").strip().lower()).strip("-")[:32] or "room"


def load_rooms(root: Path) -> list[dict]:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rooms = data.get("rooms") if isinstance(data, dict) else None
    if not isinstance(rooms, list):
        return []
    return [r for r in rooms if isinstance(r, dict) and r.get("id")]


def save_rooms(root: Path, rooms: list[dict]) -> list[dict]:
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"_v": 1, "rooms": rooms}, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return rooms


def get_room(root: Path, room_id: str) -> dict | None:
    return next((r for r in load_rooms(root) if r.get("id") == room_id), None)


def add_room(root: Path, label: str, *, mcp_services: list[str] | None = None) -> dict:
    label = (label or "").strip()[:60]
    if not label:
        raise ValueError("room name is required")
    with _LOCK:
        rooms = load_rooms(root)
        if any(r.get("label", "").lower() == label.lower() for r in rooms):
            raise ValueError(f"a room called {label!r} already exists")
        room = {
            "id": f"{_slug(label)}-{uuid.uuid4().hex[:6]}",
            "label": label,
            # The room's tools. Every seat inherits exactly this — that is what
            # makes it a room rather than a folder of unrelated agents.
            "mcp_services": list(mcp_services or []),
            "brief": "",
            "seats": [],
            "created_at": int(time.time()),
        }
        rooms.append(room)
        save_rooms(root, rooms)
    return room


def update_room(root: Path, room_id: str, changes: dict) -> dict:
    with _LOCK:
        rooms = load_rooms(root)
        room = next((r for r in rooms if r.get("id") == room_id), None)
        if not room:
            raise KeyError(room_id)
        for key in ("label", "brief", "mcp_services"):
            if key in changes and changes[key] is not None:
                room[key] = changes[key]
        save_rooms(root, rooms)
    return room


def delete_room(root: Path, room_id: str) -> bool:
    with _LOCK:
        rooms = load_rooms(root)
        keep = [r for r in rooms if r.get("id") != room_id]
        if len(keep) == len(rooms):
            return False
        save_rooms(root, keep)
    return True


# ── seats ────────────────────────────────────────────────────────────────────

def add_seat(root: Path, room_id: str, *, role: str, provider: str, account_id: str,
             goal: str = "", label: str = "", model: str = "") -> dict:
    role = (role or DEFAULT_ROLE).strip().lower()
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")
    if not provider or not account_id:
        raise ValueError("a seat needs a provider account to run it")
    seat = {
        "id": uuid.uuid4().hex[:8],
        "role": role,
        "label": (label or role.title())[:40],
        "provider": provider,
        "account_id": account_id,
        # Per seat, because a room mixes providers: the researcher's model id is
        # meaningless to the developer's CLI. Empty = the account's own default.
        "model": (model or "").strip()[:80],
        "goal": (goal or "").strip()[:500],
    }
    with _LOCK:
        rooms = load_rooms(root)
        room = next((r for r in rooms if r.get("id") == room_id), None)
        if not room:
            raise KeyError(room_id)
        room.setdefault("seats", []).append(seat)
        save_rooms(root, rooms)
    return seat


def remove_seat(root: Path, room_id: str, seat_id: str) -> bool:
    with _LOCK:
        rooms = load_rooms(root)
        room = next((r for r in rooms if r.get("id") == room_id), None)
        if not room:
            return False
        seats = room.get("seats", [])
        keep = [s for s in seats if s.get("id") != seat_id]
        if len(keep) == len(seats):
            return False
        room["seats"] = keep
        save_rooms(root, rooms)
    return True


def reorder_seats(root: Path, room_id: str, seat_ids: list[str]) -> dict:
    """Set the running order. Order is the whole handoff model: a manager placed
    after a researcher reviews that research; placed first, it briefs instead."""
    with _LOCK:
        rooms = load_rooms(root)
        room = next((r for r in rooms if r.get("id") == room_id), None)
        if not room:
            raise KeyError(room_id)
        by_id = {s["id"]: s for s in room.get("seats", [])}
        if set(seat_ids) != set(by_id):
            raise ValueError("reorder must list exactly the room's existing seats")
        room["seats"] = [by_id[i] for i in seat_ids]
        save_rooms(root, rooms)
    return room


def update_seat(root: Path, room_id: str, seat_id: str, changes: dict) -> dict:
    with _LOCK:
        rooms = load_rooms(root)
        room = next((r for r in rooms if r.get("id") == room_id), None)
        if not room:
            raise KeyError(room_id)
        seat = next((s for s in room.get("seats", []) if s.get("id") == seat_id), None)
        if not seat:
            raise KeyError(seat_id)
        if "role" in changes and changes["role"]:
            role = str(changes["role"]).strip().lower()
            if role not in ROLES:
                raise ValueError(f"unknown role {role!r}")
            seat["role"] = role
        for key in ("label", "goal", "provider", "account_id", "model"):
            if key in changes and changes[key] is not None:
                seat[key] = changes[key]
        save_rooms(root, rooms)
    return seat


# ── prompt composition ───────────────────────────────────────────────────────

_ROLE_FRAMING = {
    "manager": ("You are the manager of this room. Review the work handed to you, "
                "correct what is wrong, and state clearly what the next person "
                "should do. Do not redo their work yourself."),
    "researcher": ("You are the researcher. Gather what is needed and report "
                   "findings plainly, with sources where you have them."),
    "developer": ("You are the developer. Turn the accepted findings into working "
                  "changes. Say what you changed and what you did not."),
    "reviewer": ("You are the reviewer. Check the work against the brief and list "
                 "concrete problems, most important first."),
    "writer": ("You are the writer. Turn what you were given into the finished "
               "written output the brief asks for."),
}


def render_seat_prompt(room: dict, seat: dict, brief: str, prior: list[dict]) -> str:
    """The prompt for one seat: its role, the room brief, and what came before."""
    parts = [_ROLE_FRAMING.get(seat.get("role", ""), ""), ""]
    if seat.get("goal"):
        parts += [f"Your specific goal: {seat['goal']}", ""]
    parts += [f"## Room brief\n{brief or room.get('brief') or '(no brief given)'}", ""]

    if prior:
        parts.append("## Work already done in this room")
        for p in prior:
            out = (p.get("result") or "").strip()
            if len(out) > MAX_HANDOFF_CHARS:
                out = out[:MAX_HANDOFF_CHARS] + "\n…(truncated)"
            status = "" if p.get("ok") else " (this step FAILED — take that into account)"
            parts.append(f"### {p.get('label') or p.get('role')}{status}\n{out or '(no output)'}")
        parts.append("")
    return "\n".join(parts).strip()


# ── run records ──────────────────────────────────────────────────────────────

def _runs_dir(root: Path) -> Path:
    d = Path(root) / "data" / RUNS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_room_run(root: Path, rec: dict) -> None:
    p = _runs_dir(root) / f"{rec['id']}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def list_room_runs(root: Path, limit: int = 20) -> list[dict]:
    import glob
    out = []
    for fp in sorted(glob.glob(str(_runs_dir(root) / "*.json")), reverse=True)[:limit]:
        try:
            out.append(json.loads(Path(fp).read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def get_room_run(root: Path, run_id: str) -> dict | None:
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    p = _runs_dir(root) / f"{run_id}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── execution ────────────────────────────────────────────────────────────────

LIVE: dict = {"room_id": "", "run_id": "", "seat_id": "", "running": False}


def run_room(root: Path, room_id: str, brief: str, *,
             run_agent: Callable[..., dict],
             max_cost_usd: float = 5.0,
             on_change: Callable[[dict], None] | None = None) -> dict:
    """Run every seat in order, feeding each one the work before it.

    ``run_agent`` is injected (rather than imported) so this is testable without a
    CLI, and so it is obvious that rooms add no new way to execute an agent.
    """
    room = get_room(root, room_id)
    if not room:
        raise KeyError(room_id)
    seats = room.get("seats") or []
    if not seats:
        raise ValueError("this room has no agents in it yet")

    rec = {
        "id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
        "room_id": room_id, "room_label": room.get("label", ""),
        "brief": brief or room.get("brief", ""),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "finished": None, "ok": False, "cost_usd": 0.0, "steps": [], "error": None,
    }
    LIVE.update(room_id=room_id, run_id=rec["id"], seat_id="", running=True)
    prior: list[dict] = []
    try:
        for seat in seats:
            LIVE["seat_id"] = seat["id"]
            if on_change:
                on_change(dict(rec))
            prompt = render_seat_prompt(room, seat, rec["brief"], prior)
            out = run_agent(
                root, prompt,
                label=f"{room.get('label', 'room')} · {seat.get('label') or seat['role']}",
                mcp_services=room.get("mcp_services"),
                provider=seat.get("provider", ""), account_id=seat.get("account_id", ""),
                model=seat.get("model") or "",
            )
            step = {
                "seat_id": seat["id"], "role": seat["role"],
                "label": seat.get("label") or seat["role"],
                "run_id": out.get("id"), "ok": bool(out.get("ok")),
                "cost_usd": float(out.get("cost_usd") or 0.0),
                "result": out.get("result") or "", "error": out.get("error"),
            }
            rec["steps"].append(step)
            rec["cost_usd"] = round(rec["cost_usd"] + step["cost_usd"], 5)
            prior.append(step)

            if rec["cost_usd"] > max_cost_usd:
                # A room multiplies spend by its seat count, so the budget is
                # checked between steps rather than only per individual run.
                rec["error"] = (f"Stopped: room cost ${rec['cost_usd']} passed the "
                                f"${max_cost_usd} cap.")
                break
            if not step["ok"]:
                rec["error"] = f"Stopped: '{step['label']}' failed — {step['error'] or 'no detail'}"
                break
        else:
            rec["ok"] = True
    except Exception as exc:
        rec["error"] = str(exc)
    finally:
        rec["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_room_run(root, rec)
        LIVE.update(running=False, seat_id="")
        if on_change:
            on_change(dict(rec))
    return rec

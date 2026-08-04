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
from typing import Callable

from core import room_presets

ROOMS_FILE = "workforce.json"
RUNS_DIR = "room_runs"

ROLES = ("manager", "researcher", "developer", "reviewer", "writer")
DEFAULT_ROLE = "researcher"

# How much of an earlier seat's output to hand the next one. A long pipeline
# otherwise grows its prompt without limit and step four blows the context window.
MAX_HANDOFF_CHARS = 6000

# How far a chain of rooms may run. A room can hand off to another when it
# finishes — research → write → review — and without a ceiling a cycle, or a long
# chain somebody built by accident, would run until the cost cap noticed.
MAX_CHAIN = 6

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_LOCK = threading.Lock()


def room_folder(root: Path, room: dict) -> str:
    """The room's own folder in the research library, as a library-relative path.

    A room needs somewhere to put things. Handing a 6 KB excerpt to the next seat
    is fine for a summary and useless for a draft, a table, or a dashboard — so
    seats write files here and later seats read them, which also means the work
    survives the run and shows up in the Files page.
    """
    return f"rooms/{room.get('id') or _slug(room.get('label', 'room'))}"


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
    out = [r for r in rooms if isinstance(r, dict) and r.get("id")]
    for room in out:                       # rooms saved before colours existed
        room.setdefault("colour", room_presets.DEFAULT_COLOUR)
    return out


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
            # A tag, not decoration: a floor of a dozen rooms is unreadable when
            # every door plate looks the same, and the chain a room belongs to is
            # the thing you are actually scanning for.
            "colour": room_presets.DEFAULT_COLOUR,
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
        for key in ("label", "brief", "mcp_services", "next_room"):
            if key in changes and changes[key] is not None:
                room[key] = changes[key]
        if changes.get("colour") is not None:
            room["colour"] = room_presets.valid_colour(str(changes["colour"]))
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


def render_seat_prompt(room: dict, seat: dict, brief: str, prior: list[dict],
                       *, folder: str = "", inbox: str = "",
                       advice: list[dict] | None = None) -> str:
    """The prompt for one seat: its role, the brief, its folder, and what came before."""
    parts = [_ROLE_FRAMING.get(seat.get("role", ""), ""), ""]
    if seat.get("goal"):
        parts += [f"Your specific goal: {seat['goal']}", ""]
    parts += [f"## Room brief\n{brief or room.get('brief') or '(no brief given)'}", ""]

    if advice:
        # Above the material, not inside it: this is a correction to the brief,
        # and a correction buried under three screens of prior output is one the
        # model reads as background rather than as an instruction.
        parts.append("## Redirection from earlier seats")
        parts.append("These override the brief where they conflict with it.")
        parts += [f"- **{a.get('author') or 'earlier seat'}:** {a.get('note', '')}"
                  for a in advice]
        parts.append("")

    if folder:
        # Named explicitly rather than left implicit: a seat that does not know
        # where to put things puts them in its reply, and the reply is truncated
        # before the next seat sees it.
        parts += [
            "## Your working folder", "",
            f"`{folder}` in Plutus's research library. Use `library_write_file` to "
            "save anything substantial there — drafts, notes, tables, HTML — and "
            "`library_list_files` / `library_read_file` to pick up what earlier "
            "seats left. Only a short summary survives into the next seat's "
            "prompt, so put the real work in files.", "",
            "If you find something that changes what the *next* seat should do — "
            "the brief is wrong, a whole approach is a dead end — call "
            "`room_advise`. Your output is passed on as material; that is passed "
            "on as an instruction.", "",
        ]

    if inbox:
        parts += ["## Handed to this room", "",
                  inbox[:MAX_HANDOFF_CHARS], ""]

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
    """The most recent room runs, newest first.

    The runs directory holds more than runs: ``<run>.advice.json`` sits beside
    ``<run>.json``. A plain ``*.json`` glob picked those up too, so a run that
    used ``room_advise`` returned a JSON *list* where every caller expects a run
    record — and ate one of the ``limit`` slots doing it. Filter, then slice, or
    a room with advice on every run shows half a history.
    """
    import glob
    out: list[dict] = []
    for fp in sorted(glob.glob(str(_runs_dir(root) / "*.json")), reverse=True):
        if fp.endswith(".advice.json"):
            continue
        try:
            rec = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("id"):
            out.append(rec)
        if len(out) >= limit:
            break
    return out


def get_room_run(root: Path, run_id: str) -> dict | None:
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    p = _runs_dir(root) / f"{run_id}.json"
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # "<run>.advice" resolves to a real file that is not a run record.
    return rec if isinstance(rec, dict) else None


# ── redirection between seats ────────────────────────────────────────────────
#
# A seat that discovers the brief was wrong had no way to say so. Its output went
# to the next seat as *material*, mixed in with everything else it produced, and
# a finding like "this library was deprecated last year, use the other one" reads
# as one more paragraph rather than as an instruction.
#
# room_advise lets a seat leave a short, explicit redirection for the seats after
# it. File-backed rather than in memory because the seat's tool call is served by
# the MCP process while the room may be running in the UI process — a module
# global would be written in one and read in the other, which is to say never.

MAX_ADVICE = 12
MAX_ADVICE_CHARS = 1200


def _advice_path(root: Path, run_id: str) -> Path:
    return _runs_dir(root) / f"{run_id}.advice.json"


def add_advice(root: Path, run_id: str, note: str, *, author: str = "") -> list[dict]:
    """Record one seat's redirection for the seats that follow it."""
    note = (note or "").strip()[:MAX_ADVICE_CHARS]
    if not note:
        raise ValueError("an empty note tells the next seat nothing")
    p = _advice_path(root, run_id)
    with _LOCK:
        items = load_advice(root, run_id)
        # Capped so a seat stuck in a loop cannot crowd the brief out of the
        # prompt it is supposed to be qualifying.
        if len(items) >= MAX_ADVICE:
            raise ValueError(f"this run already has {MAX_ADVICE} notes — that is the limit")
        items.append({"author": (author or "a previous seat")[:60], "note": note,
                      "at": time.strftime("%H:%M:%S")})
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    return items


def load_advice(root: Path, run_id: str) -> list[dict]:
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return []
    try:
        data = json.loads(_advice_path(root, run_id).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


# ── execution ────────────────────────────────────────────────────────────────

LIVE: dict = {"room_id": "", "run_id": "", "seat_id": "", "running": False}

_LIVE_FILE = "room_live.json"


def publish_live(root: Path) -> None:
    """Mirror LIVE to disk so the MCP process can see which run is in flight.

    Rooms are started from two processes and their tools are served from a third
    context; "which run am I part of" has to be answerable from any of them. The
    owning pid rides along so a reader can tell a room that is still running from
    one whose process was killed mid-seat.
    """
    try:
        p = Path(root) / "data" / _LIVE_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({**LIVE, "pid": os.getpid(), "at": int(time.time())}),
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass          # a stale indicator is not worth failing a run over


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) *terminates* the target on Windows — CPython maps it to
        # TerminateProcess. Ask the OS for a handle instead.
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                       # someone else's process, but alive
    return True


def read_live(root: Path) -> dict:
    """LIVE as last published, from whichever process asks.

    A record claiming a run is in flight is only believed while the process that
    published it still exists. Without that check, killing the app mid-room
    leaves ``running: true`` on disk forever and every later room is refused with
    "a room is already running" — unrecoverable except by deleting the file.
    """
    try:
        data = json.loads((Path(root) / "data" / _LIVE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(LIVE)
    if not isinstance(data, dict):
        return dict(LIVE)
    pid = int(data.get("pid") or 0)
    if data.get("running") and pid and pid != os.getpid() and not _pid_alive(pid):
        return {**data, "running": False, "stale": True}
    return data

# One room at a time, across *every* way of starting one. It lives here rather
# than in the HTTP layer because a room can now also be started over MCP: with a
# lock per entry point, an HTTP room and an MCP room could both pass the
# LIVE["running"] check and run at once, each overwriting the other's LIVE state.
# The seats still serialise on the agent runner's slot, so the damage was a lying
# progress indicator rather than corruption — but "is a room running" has to have
# one answer.
RUN_LOCK = threading.Lock()


def _hand_off(root: Path, room: dict, room_id: str, brief: str, prior: list[dict],
              rec: dict, *, run_agent: Callable[..., dict], max_cost_usd: float,
              on_change: Callable[[dict], None] | None, chain: tuple[str, ...]) -> None:
    """After a room succeeds, run its ``next_room`` and record the follow-up id.

    Guarded three ways so a chain cannot run away: it stops at MAX_CHAIN rooms,
    refuses a room already in this chain (no cycles), and refuses to point at
    itself. The last seat's output is handed to the next room as its inbox, and
    the two rooms share a working folder so files carry across, not just text.
    A next-room failure is recorded on this room but never masks its success.
    """
    nxt = str(room.get("next_room") or "").strip()
    if not nxt or nxt == room_id or nxt in chain or len(chain) >= MAX_CHAIN:
        return
    if not get_room(root, nxt):
        return
    last = prior[-1]["result"] if prior else ""
    try:
        followup = run_room(root, nxt, brief, run_agent=run_agent,
                            max_cost_usd=max_cost_usd, on_change=on_change,
                            inbox=last, _chain=chain + (room_id,))
        rec["next_run_id"] = followup.get("id", "")
    except Exception as exc:
        rec["next_error"] = f"handoff to '{nxt}' failed: {exc}"


def run_room(root: Path, room_id: str, brief: str, *,
             run_agent: Callable[..., dict],
             max_cost_usd: float = 5.0,
             on_change: Callable[[dict], None] | None = None,
             inbox: str = "", _chain: tuple[str, ...] = ()) -> dict:
    """Run every seat in order, feeding each one the work before it.

    ``run_agent`` is injected (rather than imported) so this is testable without a
    CLI, and so it is obvious that rooms add no new way to execute an agent.

    A room that finishes successfully hands off to its ``next_room``, if it has
    one — research → write → review as a pipeline rather than one room of five
    seats. The handoff carries the last seat's output and the shared working
    folder, so the next room can pick up files rather than re-derive them.
    """
    room = get_room(root, room_id)
    if not room:
        raise KeyError(room_id)
    seats = room.get("seats") or []
    if not seats:
        raise ValueError("this room has no agents in it yet")

    folder = room_folder(root, room)
    try:
        from core.library import ensure_library, resolve_in_library
        ensure_library(root)
        resolve_in_library(folder, root).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass          # a missing folder is a worse room, not a failed one

    # A room's connections are its tool slice — exactly like a single launch.
    # Turn the selected services into the deny-list every seat runs under, so a
    # room cannot reach tools outside what it declared. Previously mcp_services
    # was handed to the runner for the record but never enforced, so every seat
    # saw the full tool surface regardless of the room's connections.
    from core.agent_orchestrator import service_disallow
    seat_disallow = service_disallow(root, room.get("mcp_services"))

    rec = {
        "id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
        "room_id": room_id, "room_label": room.get("label", ""),
        "brief": brief or room.get("brief", ""),
        "folder": folder,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "finished": None, "ok": False, "cost_usd": 0.0, "steps": [], "error": None,
        "next_run_id": "",
    }
    LIVE.update(room_id=room_id, run_id=rec["id"], seat_id="", running=True)
    publish_live(root)
    prior: list[dict] = []
    try:
        for seat in seats:
            LIVE["seat_id"] = seat["id"]
            publish_live(root)
            if on_change:
                on_change(dict(rec))
            # Re-read per seat, not once up front: the whole point is that the
            # seat before this one may have just left a correction.
            prompt = render_seat_prompt(room, seat, rec["brief"], prior,
                                        folder=folder, inbox=inbox,
                                        advice=load_advice(root, rec["id"]))
            out = run_agent(
                root, prompt,
                label=f"{room.get('label', 'room')} · {seat.get('label') or seat['role']}",
                mcp_services=room.get("mcp_services"),
                disallowed_tools=seat_disallow,
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
            # Hand off to the next room in the pipeline (research → write → review).
            _hand_off(root, room, room_id, brief, prior, rec, run_agent=run_agent,
                      max_cost_usd=max_cost_usd, on_change=on_change, chain=_chain)
    except Exception as exc:
        rec["error"] = str(exc)
    finally:
        rec["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_room_run(root, rec)
        LIVE.update(running=False, seat_id="")
        publish_live(root)
        if on_change:
            on_change(dict(rec))
    return rec

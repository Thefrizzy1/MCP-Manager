"""Agents you built once and can use again — in a launch, a schedule, or a room.

Everything the launch wizard asks for was thrown away the moment a run finished.
Which account, which model, which connections, whether it may write or publish —
all of it had to be re-picked every time, and the Rooms bench could only offer
raw provider *accounts*, so dragging one into a room produced a seat that had
forgotten every choice that made the agent worth having.

A saved agent is that whole answer under a name. It is deliberately the same
shape as a launch: nothing here can express a run the wizard could not, which is
what keeps "save this" honest rather than a second, subtly different config
model.

**Not a running thing.** No schedule, no state, no history. Saving an agent does
not create anything that executes — it records the settings a launch, a schedule
or a room seat will be filled in from. That is why deleting one is safe and why
the rooms that were staffed from it keep working: a seat copies the settings at
drop time rather than pointing back here.
"""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from core.atomic_json import read_json, write_json

AGENTS_FILE = "saved_agents.json"

MAX_AGENTS = 60           # a bench, not a database
_SLUG = re.compile(r"[^a-z0-9]+")


def _path(root: Path) -> Path:
    return Path(root) / "data" / AGENTS_FILE


def load_agents(root: Path) -> list[dict]:
    data = read_json(_path(root), {"_v": 1, "agents": []})
    agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(agents, list):
        return []
    return [a for a in agents if isinstance(a, dict) and a.get("id")]


def save_agents(root: Path, agents: list[dict]) -> list[dict]:
    write_json(_path(root), {"_v": 1, "agents": agents})
    return agents


def get_agent(root: Path, agent_id: str) -> dict | None:
    return next((a for a in load_agents(root) if a["id"] == agent_id), None)


def add_agent(root: Path, *, label: str, provider: str, account_id: str,
              model: str = "", mcp_services: list[str] | None = None,
              allow_write: bool = True, allow_publish: bool = False,
              smart_fallback: bool = True, preset: str = "",
              goal: str = "", role: str = "researcher") -> dict:
    """Record one agent. Raises ValueError on a name that is missing or taken."""
    label = (label or "").strip()[:40]
    if not label:
        raise ValueError("give this agent a name")
    if not provider or not account_id:
        raise ValueError("an agent needs a provider account to run it")

    agents = load_agents(root)
    if any(a.get("label", "").lower() == label.lower() for a in agents):
        raise ValueError(f"an agent called {label!r} already exists")
    if len(agents) >= MAX_AGENTS:
        raise ValueError(f"that is {MAX_AGENTS} saved agents — delete one first")

    agent = {
        "id": f"{_SLUG.sub('-', label.lower()).strip('-')[:24] or 'agent'}-{uuid.uuid4().hex[:6]}",
        "label": label,
        "provider": provider,
        "account_id": account_id,
        "model": (model or "").strip()[:80],
        # None means "do not narrow", which is a different run from [] ("no
        # connections at all") — so it is preserved rather than coerced.
        "mcp_services": list(mcp_services) if mcp_services is not None else None,
        "allow_write": bool(allow_write),
        "allow_publish": bool(allow_publish),
        "smart_fallback": bool(smart_fallback),
        "preset": (preset or "").strip()[:40],
        # What this agent is for. Becomes the seat's goal when dropped into a
        # room, so a bench of agents reads as a bench of *jobs*.
        "goal": (goal or "").strip()[:500],
        "role": role if role in ROLES else "researcher",
        "created_at": int(time.time()),
    }
    agents.append(agent)
    save_agents(root, agents)
    return agent


# The room roles a saved agent can be dropped in as. Imported from workforce
# would be circular at module level and is one constant, so it is stated here and
# the agreement is guarded by a test.
ROLES = ("manager", "researcher", "developer", "reviewer", "writer")

_EDITABLE = ("label", "provider", "account_id", "model", "mcp_services",
             "allow_write", "allow_publish", "smart_fallback", "preset", "goal", "role")


def update_agent(root: Path, agent_id: str, changes: dict) -> dict:
    agents = load_agents(root)
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        raise KeyError(agent_id)

    label = changes.get("label")
    if label is not None:
        label = str(label).strip()[:40]
        if not label:
            raise ValueError("give this agent a name")
        if any(a["id"] != agent_id and a.get("label", "").lower() == label.lower()
               for a in agents):
            raise ValueError(f"an agent called {label!r} already exists")
        changes = {**changes, "label": label}

    if changes.get("role") is not None and changes["role"] not in ROLES:
        raise ValueError(f"unknown role {changes['role']!r}")

    for key in _EDITABLE:
        if key in changes and changes[key] is not None:
            agent[key] = changes[key]
    save_agents(root, agents)
    return agent


def delete_agent(root: Path, agent_id: str) -> bool:
    agents = load_agents(root)
    keep = [a for a in agents if a["id"] != agent_id]
    if len(keep) == len(agents):
        return False
    save_agents(root, keep)
    return True


def as_launch(agent: dict, prompt: str) -> dict:
    """The saved settings as a launch payload, with the prompt supplied.

    One place that knows the mapping, so a field added to the store cannot start
    silently applying in rooms but not in launches.
    """
    return {
        "prompt": prompt,
        "label": agent.get("label", "agent"),
        "provider": agent.get("provider", ""),
        "account_id": agent.get("account_id", ""),
        "model": agent.get("model", ""),
        "mcp_services": agent.get("mcp_services"),
        "allow_write": bool(agent.get("allow_write", True)),
        "allow_publish": bool(agent.get("allow_publish", False)),
        "smart_fallback": bool(agent.get("smart_fallback", True)),
        "preset": agent.get("preset", ""),
    }

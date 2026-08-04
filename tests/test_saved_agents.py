"""Saved agents — the wizard's answers under a name.

The point of the store is that a choice survives: which account, which model,
which connections, how far it may go. So what is worth testing is the ways that
survival could quietly break — a seat that keeps a *reference* instead of a copy,
`None` connections coerced into `[]`, or the launch mapping drifting from the
store as fields are added.
"""
from __future__ import annotations

import pytest

from core import saved_agents, workforce


def _agent(root, **over):
    kwargs = {"label": "Scout", "provider": "openrouter", "account_id": "a1",
              "model": "openrouter/free", "mcp_services": ["websearch", "reddit"],
              "goal": "map the territory", "role": "researcher"}
    kwargs.update(over)
    return saved_agents.add_agent(root, **kwargs)


def test_an_agent_round_trips(tmp_path):
    made = _agent(tmp_path)
    got = saved_agents.get_agent(tmp_path, made["id"])
    assert got == made
    assert got["model"] == "openrouter/free"
    assert got["mcp_services"] == ["websearch", "reddit"]
    assert got["allow_publish"] is False       # off unless asked for


def test_no_connections_and_all_connections_stay_different(tmp_path):
    """`None` means "do not narrow" and `[]` means "no connections at all".
    Collapsing them silently turns an unrestricted agent into a useless one."""
    unrestricted = _agent(tmp_path, label="Open", mcp_services=None)
    none_at_all = _agent(tmp_path, label="Sealed", mcp_services=[])

    assert saved_agents.get_agent(tmp_path, unrestricted["id"])["mcp_services"] is None
    assert saved_agents.get_agent(tmp_path, none_at_all["id"])["mcp_services"] == []


def test_names_are_unique_and_required(tmp_path):
    _agent(tmp_path, label="Scout")
    with pytest.raises(ValueError, match="already exists"):
        _agent(tmp_path, label="scout")            # case-insensitive
    with pytest.raises(ValueError, match="name"):
        _agent(tmp_path, label="   ")
    with pytest.raises(ValueError, match="provider account"):
        _agent(tmp_path, label="Nameless", provider="", account_id="")


def test_renaming_onto_another_name_is_refused_but_onto_its_own_is_fine(tmp_path):
    a = _agent(tmp_path, label="Scout")
    _agent(tmp_path, label="Editor")

    with pytest.raises(ValueError, match="already exists"):
        saved_agents.update_agent(tmp_path, a["id"], {"label": "Editor"})
    # Re-saving an agent without changing its name must not trip its own guard.
    assert saved_agents.update_agent(tmp_path, a["id"], {"label": "Scout"})["label"] == "Scout"


def test_updating_and_deleting(tmp_path):
    a = _agent(tmp_path)
    saved_agents.update_agent(tmp_path, a["id"], {"model": "gpt-5", "allow_publish": True})
    got = saved_agents.get_agent(tmp_path, a["id"])
    assert got["model"] == "gpt-5" and got["allow_publish"] is True

    with pytest.raises(ValueError, match="role"):
        saved_agents.update_agent(tmp_path, a["id"], {"role": "janitor"})
    with pytest.raises(KeyError):
        saved_agents.update_agent(tmp_path, "nope", {"model": "x"})

    assert saved_agents.delete_agent(tmp_path, a["id"]) is True
    assert saved_agents.delete_agent(tmp_path, a["id"]) is False
    assert saved_agents.load_agents(tmp_path) == []


def test_the_bench_has_a_ceiling(tmp_path):
    for n in range(saved_agents.MAX_AGENTS):
        _agent(tmp_path, label=f"Agent {n}")
    with pytest.raises(ValueError, match="delete one"):
        _agent(tmp_path, label="One too many")


def test_the_launch_mapping_covers_every_launch_field(tmp_path):
    """`as_launch` is the single place the store meets a run. If a field is added
    to one and not the other, an agent silently stops honouring it."""
    launch = saved_agents.as_launch(_agent(tmp_path), "do the thing")
    assert launch["prompt"] == "do the thing"
    assert launch["provider"] == "openrouter" and launch["account_id"] == "a1"
    assert launch["mcp_services"] == ["websearch", "reddit"]

    from ui.api.agents import AgentRunBody
    # Every field the launch endpoint accepts is either supplied here or is
    # deliberately not something a saved agent decides.
    unmapped = set(AgentRunBody.model_fields) - set(launch)
    assert unmapped == {"label"} or unmapped == set(), unmapped


def test_the_roles_agree_with_the_rooms_that_use_them():
    """A saved agent is dropped in as a room seat, so an extra role here would be
    rejected by workforce.add_seat at the moment of the drop."""
    assert set(saved_agents.ROLES) == set(workforce.ROLES)


def test_a_corrupt_store_reads_as_empty(tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / saved_agents.AGENTS_FILE).write_text("{not json", encoding="utf-8")
    assert saved_agents.load_agents(tmp_path) == []


# ── dropping one into a room ─────────────────────────────────────────────────

def _seat_agent(tmp_path, monkeypatch, room_id, agent_id, role=""):
    import asyncio

    from ui.api import workforce as api

    monkeypatch.setattr(api, "ROOT", tmp_path)
    return asyncio.run(api.api_seat_from_agent(room_id, agent_id,
                                               api.FromAgentBody(role=role)))


def test_seating_an_agent_copies_its_settings(tmp_path, monkeypatch):
    room = workforce.add_room(tmp_path, "Research", mcp_services=["websearch"])
    agent = _agent(tmp_path, model="gpt-5", goal="find the primary sources")

    res = _seat_agent(tmp_path, monkeypatch, room["id"], agent["id"])
    seat = res["seat"]

    assert seat["provider"] == "openrouter" and seat["account_id"] == "a1"
    assert seat["model"] == "gpt-5"
    assert seat["goal"] == "find the primary sources"
    assert seat["role"] == "researcher"          # the agent's own role
    assert seat["label"] == "Scout"


def test_the_desk_it_was_dropped_on_wins(tmp_path, monkeypatch):
    """Dropping someone onto the Reviewer desk should seat a reviewer, whatever
    the agent's usual role is."""
    room = workforce.add_room(tmp_path, "Office")
    agent = _agent(tmp_path, role="researcher")

    res = _seat_agent(tmp_path, monkeypatch, room["id"], agent["id"], role="reviewer")
    assert res["seat"]["role"] == "reviewer"


def test_the_room_gains_the_connections_the_agent_needs(tmp_path, monkeypatch):
    """An agent is the tools it was given as much as the account it runs on. A
    seat silently missing half of them looks like a bad model, not a bad drop."""
    room = workforce.add_room(tmp_path, "Research", mcp_services=["websearch"])
    agent = _agent(tmp_path, mcp_services=["websearch", "reddit", "firecrawl"])

    res = _seat_agent(tmp_path, monkeypatch, room["id"], agent["id"])

    assert res["added_connections"] == ["reddit", "firecrawl"]
    assert res["room"]["mcp_services"] == ["websearch", "reddit", "firecrawl"]


def test_an_unrestricted_agent_does_not_widen_the_room(tmp_path, monkeypatch):
    """`None` means "this agent was never narrowed" — not "give it everything",
    which would quietly hand a scoped room the whole tool surface."""
    room = workforce.add_room(tmp_path, "Sealed", mcp_services=["websearch"])
    agent = _agent(tmp_path, mcp_services=None)

    res = _seat_agent(tmp_path, monkeypatch, room["id"], agent["id"])

    assert res["added_connections"] == []
    assert res["room"]["mcp_services"] == ["websearch"]


def test_deleting_the_agent_leaves_the_room_working(tmp_path, monkeypatch):
    """The seat copies rather than references, so a bench cleanup cannot empty
    the rooms that were staffed from it."""
    room = workforce.add_room(tmp_path, "Research")
    agent = _agent(tmp_path)
    _seat_agent(tmp_path, monkeypatch, room["id"], agent["id"])

    saved_agents.delete_agent(tmp_path, agent["id"])

    seats = workforce.get_room(tmp_path, room["id"])["seats"]
    assert len(seats) == 1
    assert seats[0]["provider"] == "openrouter" and seats[0]["model"] == "openrouter/free"


def test_seating_something_that_is_not_there(tmp_path, monkeypatch):
    from fastapi import HTTPException

    room = workforce.add_room(tmp_path, "Research")
    with pytest.raises(HTTPException) as e:
        _seat_agent(tmp_path, monkeypatch, room["id"], "no-such-agent")
    assert e.value.status_code == 404

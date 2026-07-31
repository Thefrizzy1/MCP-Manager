"""Sub-agents — a coordinator handing work to cheaper models.

The expensive account should be deciding what to do, not doing all of it. Three
properties carry the design, and each is here because getting it wrong is
expensive rather than merely broken:

- workers run on HTTP providers only, which is what lets them run *alongside* the
  main agent instead of fighting it for the runner's single slot
- a worker cannot delegate, or a runaway coordinator bills you for the recursion
- a batch runs concurrently but bounded, so a free tier's rate limit is not what
  discovers the ceiling
"""
from __future__ import annotations

import asyncio

import pytest

from core import ai_providers as AP
from core import subagents as SA


def _gemini(tmp_path, label="Personal"):
    acct = AP.add_account(tmp_path, "gemini", label)
    AP.save_token(tmp_path, "gemini", acct["id"], "AIza-fake")
    AP.forget_models()
    return acct["id"]


def _openrouter(tmp_path, label="Router"):
    acct = AP.add_account(tmp_path, "openrouter", label)
    AP.save_token(tmp_path, "openrouter", acct["id"], "sk-or-fake")
    AP.forget_models()
    return acct["id"]


def _answers(monkeypatch, script):
    """Scripted api_turn replies; records the declarations each turn was given."""
    seen: list[dict] = []
    seq = list(script)

    def fake(root, provider, account_id, *, contents, declarations=None, model="",
             timeout=120, search=False, extras=None):
        seen.append({"provider": provider, "account_id": account_id, "model": model,
                     "declarations": [d.get("name") or
                                      (d.get("function") or {}).get("name")
                                      for d in (declarations or [])],
                     "contents": list(contents)})
        got = seq.pop(0) if seq else {"text": "done"}
        return {"ok": got.get("ok", True), "text": got.get("text", ""),
                "calls": got.get("calls", []), "raw_message": {"role": "model"},
                "error": got.get("error", ""), "model": model or "m",
                "finish": "STOP", "parts": []}

    monkeypatch.setattr(AP, "api_turn", fake)
    return seen


# ── which accounts can be workers ────────────────────────────────────────────

def test_only_http_providers_can_be_workers(tmp_path):
    """Not a rule for its own sake: run_agent holds one global slot, so a CLI
    sub-agent launched from inside a run would be refused. An HTTP worker is just
    a request, so several can be in flight without touching that slot."""
    claude = AP.add_account(tmp_path, "claude", "Pro")
    (AP.account_dir(tmp_path, "claude", claude["id"]) / ".credentials.json").write_text(
        "{}", encoding="utf-8")
    codex = AP.add_account(tmp_path, "codex", "ChatGPT")
    (AP.account_dir(tmp_path, "codex", codex["id"]) / "auth.json").write_text(
        "{}", encoding="utf-8")
    gid = _gemini(tmp_path)

    workers = SA.worker_accounts(tmp_path)
    assert [w["account_id"] for w in workers] == [gid]
    assert {w["provider"] for w in workers} == {"gemini"}


def test_an_unlinked_account_is_not_a_worker(tmp_path):
    AP.add_account(tmp_path, "gemini", "No key")
    assert SA.worker_accounts(tmp_path) == []


def test_delegating_with_no_workers_explains_what_to_add(tmp_path):
    res = asyncio.run(SA.delegate(tmp_path, "summarise this"))
    assert res["ok"] is False
    assert "Gemini or OpenRouter" in res["error"]


def test_a_provider_can_be_named_without_an_account_id(tmp_path, monkeypatch):
    """A coordinator should not have to know account ids to delegate."""
    _gemini(tmp_path)
    oid = _openrouter(tmp_path)
    seen = _answers(monkeypatch, [{"text": "ok"}])

    res = asyncio.run(SA.delegate(tmp_path, "task", provider="openrouter", use_tools=False))
    assert res["ok"] is True
    assert seen[0]["provider"] == "openrouter" and seen[0]["account_id"] == oid


def test_naming_a_provider_with_no_account_lists_what_there_is(tmp_path):
    _gemini(tmp_path)
    res = asyncio.run(SA.delegate(tmp_path, "task", provider="openrouter"))
    assert res["ok"] is False and "gemini" in res["error"]


# ── running one ──────────────────────────────────────────────────────────────

def test_a_worker_returns_its_answer(tmp_path, monkeypatch):
    _gemini(tmp_path)
    _answers(monkeypatch, [{"text": "Three findings: a, b, c."}])

    res = asyncio.run(SA.delegate(tmp_path, "summarise", use_tools=False))
    assert res["ok"] is True
    assert res["text"] == "Three findings: a, b, c."
    assert res["turns"] == 1
    assert "gemini/" in res["worker"]


def test_a_worker_can_use_tools_then_answer(tmp_path, monkeypatch):
    _gemini(tmp_path)
    _answers(monkeypatch, [
        {"calls": [{"id": "c1", "name": "library_write_file",
                    "args": {"path": "w/out.md", "content": "# From a worker\n"}}]},
        {"text": "Saved."},
    ])

    res = asyncio.run(SA.delegate(tmp_path, "write it up"))
    assert res["ok"] is True and res["turns"] == 2
    assert (tmp_path / "data" / "library" / "w" / "out.md").read_text() == "# From a worker\n"


def test_a_worker_never_gets_the_delegation_tools(tmp_path, monkeypatch):
    """Nesting coordinators is how a runaway bill happens."""
    _gemini(tmp_path)
    seen = _answers(monkeypatch, [{"text": "ok"}])

    class FakeMcp:
        def list_tools(self):
            return [{"name": "agent_delegate", "description": "d",
                     "inputSchema": {"type": "object"}},
                    {"name": "agent_list_workers", "description": "d",
                     "inputSchema": {"type": "object"}},
                    {"name": "sonarr_queue", "description": "d",
                     "inputSchema": {"type": "object"}}]

        def call_tool(self, name, args):
            return {"text": "", "is_error": False}

        def close(self):
            pass

    monkeypatch.setattr("core.mcp_client.McpHttpClient", lambda url, token="", **kw: FakeMcp())
    asyncio.run(SA.delegate(tmp_path, "task", mcp_url="http://x/mcp"))

    offered = seen[0]["declarations"]
    assert "sonarr_queue" in offered
    assert "library_write_file" in offered, "workers still get the library"
    assert not any(n in offered for n in SA.WORKER_DENIED), offered


def test_a_worker_that_never_answers_is_stopped(tmp_path, monkeypatch):
    _gemini(tmp_path)
    _answers(monkeypatch, [{"calls": [{"id": "c", "name": "library_list_files", "args": {}}]}] * 20)

    res = asyncio.run(SA.delegate(tmp_path, "loop", max_turns=3))
    assert res["ok"] is False and "without answering" in res["error"]
    assert res["turns"] == 3


def test_a_provider_error_comes_back_as_data(tmp_path, monkeypatch):
    """The coordinator asked a question; an exception would end *its* run too."""
    _gemini(tmp_path)
    _answers(monkeypatch, [{"ok": False, "error": "quota exhausted"}])

    res = asyncio.run(SA.delegate(tmp_path, "task", use_tools=False))
    assert res["ok"] is False and res["error"] == "quota exhausted"


def test_the_worker_starts_with_only_the_task(tmp_path, monkeypatch):
    """No conversation history: the task has to stand alone, and the tool
    description says so."""
    _gemini(tmp_path)
    seen = _answers(monkeypatch, [{"text": "ok"}])
    asyncio.run(SA.delegate(tmp_path, "the whole instruction", use_tools=False))
    assert len(seen[0]["contents"]) == 1


# ── batches ──────────────────────────────────────────────────────────────────

def test_a_batch_accepts_lines_or_json():
    assert SA.parse_batch("a\nb\n\nc") == ["a", "b", "c"]
    assert SA.parse_batch('["x", "y"]') == ["x", "y"]
    assert SA.parse_batch("  ") == []
    # Malformed JSON falls back to lines rather than returning nothing.
    assert SA.parse_batch('["broken') == ['["broken']


def test_a_batch_runs_concurrently_but_bounded(tmp_path, monkeypatch):
    """Unbounded fan-out finds a free tier's rate limit the hard way."""
    _gemini(tmp_path)
    live = {"now": 0, "peak": 0}

    def fake(root, provider, account_id, *, contents, declarations=None, model="",
             timeout=120, search=False, extras=None):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        import time
        time.sleep(0.05)
        live["now"] -= 1
        return {"ok": True, "text": "done", "calls": [], "raw_message": {},
                "error": "", "model": "m", "finish": "STOP", "parts": []}

    monkeypatch.setattr(AP, "api_turn", fake)

    async def run_all():
        return await asyncio.gather(*[
            SA.delegate(tmp_path, f"task {i}", use_tools=False) for i in range(12)])

    results = asyncio.run(run_all())
    assert all(r["ok"] for r in results)
    assert live["peak"] > 1, "the point of a batch is that it overlaps"
    assert live["peak"] <= SA.MAX_CONCURRENCY, live["peak"]


# ── how the coordinator reads it ─────────────────────────────────────────────

def test_a_result_names_the_worker_and_cost_shape(tmp_path):
    out = SA.render({"ok": True, "text": "the answer", "worker": "gemini/x",
                     "turns": 2, "error": ""})
    assert "gemini/x" in out and "2 turns" in out and "the answer" in out


def test_a_failure_is_legible_to_the_coordinator(tmp_path):
    out = SA.render({"ok": False, "text": "", "worker": "gemini/x",
                     "turns": 1, "error": "quota exhausted"})
    assert "Sub-agent failed" in out and "quota exhausted" in out


def test_the_worker_list_tells_the_coordinator_what_it_can_use(tmp_path):
    _gemini(tmp_path, "Personal Google")
    out = SA.render_workers(SA.worker_accounts(tmp_path))
    assert "gemini" in out and "Personal Google" in out
    assert "agent_delegate" in out


# ── registration ─────────────────────────────────────────────────────────────

def test_the_tools_are_real_mcp_tools_so_every_runtime_gets_them():
    """Claude reaches them through --mcp-config, Codex through the stdio bridge,
    Gemini through declarations built from tools/list. A built-in would have
    covered only two of the three."""
    from mcp.server.fastmcp import FastMCP

    from tools.agents import register_agent_tools

    m = FastMCP("t")
    register_agent_tools(m)
    names = {t.name for t in m._tool_manager.list_tools()}
    assert names == {"agent_delegate", "agent_delegate_batch", "agent_list_workers"}


def test_delegation_is_not_marked_read_only():
    """A worker can write files and call tools, so the blast-radius rules must
    not treat delegating as a read."""
    from mcp.server.fastmcp import FastMCP

    from tools.agents import register_agent_tools

    m = FastMCP("t")
    register_agent_tools(m)
    tools = {t.name: t for t in m._tool_manager.list_tools()}
    assert tools["agent_delegate"].annotations.readOnlyHint is False
    assert tools["agent_delegate_batch"].annotations.readOnlyHint is False
    assert tools["agent_list_workers"].annotations.readOnlyHint is True


@pytest.mark.parametrize("name", ["agent_delegate", "agent_list_workers"])
def test_the_denied_list_matches_the_registered_names(name):
    """If a tool were renamed without updating WORKER_DENIED, workers would
    quietly regain the ability to delegate."""
    assert name in SA.WORKER_DENIED

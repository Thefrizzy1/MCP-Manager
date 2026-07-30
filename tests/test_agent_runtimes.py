"""Which runtime a run actually uses — the fix for "401 Invalid bearer token".

The live failure: selecting a Codex account and launching an agent produced

    Failed to authenticate. API Error: 401 Invalid bearer token

on every run. Nothing was wrong with the Codex login. ``run_agent`` built a
``claude`` command unconditionally, so picking a Codex account ran *Claude Code*
with ``CODEX_HOME`` set — a variable Claude ignores — and Claude then fell back to
whatever ambient ``CLAUDE_CODE_OAUTH_TOKEN`` happened to be in the environment.
The error was real; it just belonged to a CLI nobody had chosen.

So the property under test is blunt: the selected account decides which binary is
spawned, or whether anything is spawned at all.
"""
from __future__ import annotations

import json

import pytest

from core import agent_runner as AR
from core import ai_providers as AP


class FakeProc:
    """Stands in for a spawned CLI."""

    def __init__(self, lines: list[str], code: int = 0):
        self.stdout = list(lines)
        self.returncode = code

    def wait(self):
        return self.returncode

    def kill(self):
        pass


class Spawns(list):
    """Every argv this run tried to launch, plus the process it gets back."""

    proc = FakeProc([])
    env: dict = {}


@pytest.fixture
def spawns(monkeypatch):
    seen = Spawns()

    def fake_popen(cmd, **kw):
        seen.append(list(cmd))
        seen.env = kw.get("env") or {}
        return seen.proc

    monkeypatch.setattr(AR.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(AP, "resolve_cli", lambda name: f"/usr/bin/{name}")
    return seen


@pytest.fixture(autouse=True)
def _no_mcp_config(monkeypatch):
    """Keep runs from writing an MCP config into the test's temp root."""
    monkeypatch.setattr(AR, "load_agent_config",
                        lambda root: {**AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": False})


def _linked_codex(tmp_path):
    acct = AP.add_account(tmp_path, "codex", "Personal ChatGPT")
    (AP.account_dir(tmp_path, "codex", acct["id"]) / "auth.json").write_text("{}", encoding="utf-8")
    return acct["id"]


def _linked_gemini(tmp_path, key="AIza-fake"):
    acct = AP.add_account(tmp_path, "gemini", "Personal Google")
    AP.save_token(tmp_path, "gemini", acct["id"], key)
    return acct["id"]


# ── the runtime follows the account ──────────────────────────────────────────

def test_a_codex_account_spawns_codex_not_claude(tmp_path, spawns):
    aid = _linked_codex(tmp_path)
    spawns.proc = FakeProc(["[t] codex", "the answer", "[t] tokens used: 5"])

    rec = AR.run_agent(tmp_path, "do the thing", label="x",
                       provider="codex", account_id=aid)

    assert len(spawns) == 1
    cmd = spawns[0]
    assert cmd[0].endswith("codex"), cmd
    assert "claude" not in " ".join(cmd)
    assert cmd[1:3] == ["exec", "--skip-git-repo-check"]
    assert cmd[-1] == "do the thing"
    # And the Claude-only flags must not leak across.
    assert "--output-format" not in cmd and "--mcp-config" not in cmd
    assert rec["ok"] is True and rec["result"] == "the answer"


def test_a_claude_account_still_spawns_claude_in_stream_mode(tmp_path, spawns):
    acct = AP.add_account(tmp_path, "claude", "Personal Pro")
    (AP.account_dir(tmp_path, "claude", acct["id"]) / ".credentials.json").write_text(
        "{}", encoding="utf-8")
    spawns.proc = FakeProc([json.dumps(
        {"type": "result", "subtype": "success", "total_cost_usd": 0.2,
         "num_turns": 3, "result": "done"})])

    rec = AR.run_agent(tmp_path, "hi", label="x", provider="claude", account_id=acct["id"])

    assert spawns[0][0].endswith("claude")
    assert "stream-json" in spawns[0]
    assert rec["ok"] is True and rec["cost_usd"] == 0.2 and rec["turns"] == 3


def test_a_gemini_account_never_spawns_anything(tmp_path, spawns, monkeypatch):
    """It is an HTTP provider: a subprocess here would mean a CLI we no longer ship."""
    aid = _linked_gemini(tmp_path)
    monkeypatch.setattr(AP, "_http", lambda *a, **k: {
        "code": 200, "error": "",
        "json": {"candidates": [{"content": {"parts": [{"text": "researched"}]}}]}})

    rec = AR.run_agent(tmp_path, "look it up", label="x", provider="gemini", account_id=aid)

    assert spawns == [], "an HTTP provider must not shell out"
    assert rec["ok"] is True and rec["result"] == "researched"
    assert rec["turns"] == 1


def test_no_account_keeps_the_legacy_claude_path(tmp_path, spawns, monkeypatch):
    monkeypatch.setattr(AR, "legacy_credential_source", lambda: ("cli", "mounted login"))
    spawns.proc = FakeProc([json.dumps(
        {"type": "result", "subtype": "success", "total_cost_usd": 0, "result": "ok"})])

    rec = AR.run_agent(tmp_path, "hi", label="x")
    assert spawns[0][0].endswith("claude")
    assert rec["auth_source"] == "cli"


# ── fail before spending a run ───────────────────────────────────────────────

def test_an_unlinked_codex_account_never_spawns(tmp_path, spawns):
    """Spawning it could only ever come back 401 — the failure the user saw."""
    acct = AP.add_account(tmp_path, "codex", "Personal ChatGPT")

    rec = AR.run_agent(tmp_path, "hi", label="x", provider="codex", account_id=acct["id"])

    assert spawns == []
    assert rec["ok"] is False
    assert "not linked" in rec["error"] and "CODEX_HOME=" in rec["error"]


def test_a_gemini_account_without_a_key_says_so(tmp_path, spawns):
    acct = AP.add_account(tmp_path, "gemini", "Personal Google")
    rec = AR.run_agent(tmp_path, "hi", label="x", provider="gemini", account_id=acct["id"])

    assert rec["ok"] is False
    assert "API key" in rec["error"] and "aistudio" in rec["error"]


# ── failures are explained, not parroted ─────────────────────────────────────

def test_a_401_from_another_cli_names_the_account_to_re_link(tmp_path, spawns):
    aid = _linked_codex(tmp_path)
    spawns.proc = FakeProc(
        ["Failed to authenticate. API Error: 401 Invalid bearer token"], code=1)

    rec = AR.run_agent(tmp_path, "hi", label="x", provider="codex", account_id=aid)

    assert rec["ok"] is False
    assert "Invalid bearer token" not in rec["error"]
    assert aid in rec["error"] and "Settings → AI providers" in rec["error"]


def test_a_clean_exit_with_no_output_is_not_a_success(tmp_path, spawns):
    aid = _linked_codex(tmp_path)
    spawns.proc = FakeProc([], code=0)

    rec = AR.run_agent(tmp_path, "hi", label="x", provider="codex", account_id=aid)
    assert rec["ok"] is False and "no output" in rec["error"]


def test_a_gemini_api_error_reaches_the_run_record(tmp_path, spawns, monkeypatch):
    aid = _linked_gemini(tmp_path)
    monkeypatch.setattr(AP, "_http", lambda *a, **k: {
        "code": 429, "json": {}, "error": "Quota exceeded"})

    rec = AR.run_agent(tmp_path, "hi", label="x", provider="gemini", account_id=aid)
    assert rec["ok"] is False and "quota" in rec["error"]


# ── the model is per run ─────────────────────────────────────────────────────

def test_the_chosen_model_reaches_the_cli(tmp_path, spawns):
    aid = _linked_codex(tmp_path)
    spawns.proc = FakeProc(["answer"])

    rec = AR.run_agent(tmp_path, "hi", label="x", provider="codex", account_id=aid,
                       model="gpt-5.1-codex")

    assert spawns[0][spawns[0].index("--model") + 1] == "gpt-5.1-codex"
    assert rec["model"] == "gpt-5.1-codex"


def test_the_chosen_model_reaches_the_api(tmp_path, spawns, monkeypatch):
    aid = _linked_gemini(tmp_path)
    urls: list[str] = []

    def fake(method, url, key, *, payload=None, timeout=60):
        urls.append(url)
        return {"code": 200, "error": "",
                "json": {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}}

    monkeypatch.setattr(AP, "_http", fake)
    AR.run_agent(tmp_path, "hi", label="x", provider="gemini", account_id=aid,
                 model="gemini-2.5-pro")

    assert "models/gemini-2.5-pro:generateContent" in urls[0]


def test_an_omitted_model_does_not_pin_one(tmp_path, spawns):
    aid = _linked_codex(tmp_path)
    spawns.proc = FakeProc(["answer"])
    AR.run_agent(tmp_path, "hi", label="x", provider="codex", account_id=aid)
    assert "--model" not in spawns[0], "empty means 'whatever the account defaults to'"


# ── tools ────────────────────────────────────────────────────────────────────

def test_plutus_tools_are_only_wired_into_claude(tmp_path, spawns, monkeypatch):
    """--mcp-config is Claude's flag. Passing it to another CLI would break the
    run; passing nothing and staying quiet would leave an agent looking useless
    for reasons the operator cannot see."""
    monkeypatch.setattr(AR, "load_agent_config",
                        lambda root: {**AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": True})
    written: list[str] = []
    monkeypatch.setattr(AR, "write_plutus_mcp_config",
                        lambda root, **kw: written.append("yes") or "/tmp/mcp.json")

    aid = _linked_codex(tmp_path)
    spawns.proc = FakeProc(["answer"])
    rec = AR.run_agent(tmp_path, "hi", label="x", provider="codex", account_id=aid)

    assert written == [], "no MCP config for a runtime that cannot read it"
    assert any("MCP tools are not wired" in line for line in rec["log"]), rec["log"]


# ── stopping ─────────────────────────────────────────────────────────────────

def test_stop_works_for_a_run_with_no_process(monkeypatch):
    """An HTTP provider's run has nothing to kill, but Stop must not claim there
    is no run — it is plainly running, and the answer has to be discarded."""
    monkeypatch.setitem(AR._current, "running", True)
    monkeypatch.setitem(AR._current, "proc", None)
    monkeypatch.setitem(AR._current, "cancelled", False)

    res = AR.cancel()
    assert res["ok"] is True and res["killed"] is False
    assert AR._current["cancelled"] is True


def test_stop_with_nothing_running_still_says_so(monkeypatch):
    monkeypatch.setitem(AR._current, "running", False)
    assert AR.cancel()["ok"] is False


# ── the room's slot wait ─────────────────────────────────────────────────────

def test_wait_for_slot_returns_immediately_when_free():
    assert AR.wait_for_slot(timeout=1) is True


def test_wait_for_slot_gives_up_rather_than_hanging(monkeypatch):
    """Rooms call this between seats; an unbounded wait would wedge the thread."""
    monkeypatch.setattr(AR, "busy", lambda: True)
    assert AR.wait_for_slot(timeout=0.2, poll=0.05) is False

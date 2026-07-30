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
from pathlib import Path

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

@pytest.fixture
def with_tools(monkeypatch):
    monkeypatch.setattr(AR, "load_agent_config",
                        lambda root: {**AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": True})


def test_codex_gets_plutus_mcp_through_the_stdio_bridge(tmp_path, spawns, with_tools):
    """`codex exec` has no --mcp-config, but it does read $CODEX_HOME/config.toml —
    and each account already has its own CODEX_HOME."""
    aid = _linked_codex(tmp_path)
    spawns.proc = FakeProc(["answer"])

    rec = AR.run_agent(tmp_path, "hi", label="x", provider="codex", account_id=aid,
                       mcp_url="http://127.0.0.1:8765/mcp", bearer_token="secret",
                       disallowed_tools=["mcp__plutus__docker_stop_container"])

    conf = (AP.account_dir(tmp_path, "codex", aid) / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.plutus]" in conf
    assert "mcp_stdio_bridge.py" in conf
    assert "http://127.0.0.1:8765/mcp" in conf
    # The connection ACL has to reach the bridge — Codex cannot enforce it.
    assert "docker_stop_container" in conf
    assert "secret" in conf
    assert any("wired into Codex" in line for line in rec["log"]), rec["log"]


def test_the_codex_block_does_not_clobber_the_rest_of_the_config(tmp_path, spawns, with_tools):
    aid = _linked_codex(tmp_path)
    conf = AP.account_dir(tmp_path, "codex", aid) / "config.toml"
    conf.write_text('model = "gpt-5.1"\n\n[mcp_servers.plutus]\ncommand = "old"\n\n'
                    '[mcp_servers.other]\ncommand = "keep-me"\n', encoding="utf-8")
    spawns.proc = FakeProc(["answer"])

    AR.run_agent(tmp_path, "hi", label="x", provider="codex", account_id=aid,
                 mcp_url="http://x/mcp")

    text = conf.read_text(encoding="utf-8")
    assert 'model = "gpt-5.1"' in text, "operator settings must survive"
    assert "keep-me" in text, "other MCP servers must survive"
    assert text.count("[mcp_servers.plutus]") == 1, "our block must be replaced, not duplicated"
    assert 'command = "old"' not in text


def test_tools_off_writes_no_mcp_config_anywhere(tmp_path, spawns, monkeypatch):
    monkeypatch.setattr(AR, "load_agent_config",
                        lambda root: {**AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": False})
    aid = _linked_codex(tmp_path)
    spawns.proc = FakeProc(["answer"])

    AR.run_agent(tmp_path, "hi", label="x", provider="codex", account_id=aid,
                 mcp_url="http://x/mcp")
    assert not (AP.account_dir(tmp_path, "codex", aid) / "config.toml").exists()


def test_a_windows_path_survives_toml_escaping(tmp_path):
    """Unescaped backslashes become escape sequences and Codex reads a path that
    does not exist."""
    AP.add_account(tmp_path, "codex", "P")
    aid = AP.load_accounts(tmp_path)["codex"][0]["id"]
    path = AR.write_codex_mcp_config(tmp_path, aid, mcp_url="http://x/mcp")

    import tomllib
    conf = tomllib.loads(open(path, encoding="utf-8").read())
    entry = conf["mcp_servers"]["plutus"]
    assert entry["args"][0].endswith("mcp_stdio_bridge.py")
    assert Path(entry["args"][0]).is_file(), "the bridge has to actually be there"
    assert entry["env"]["PLUTUS_MCP_URL"] == "http://x/mcp"


# ── Gemini's tools are Plutus's tools ────────────────────────────────────────

class FakeMcp:
    """Stands in for Plutus's own MCP endpoint."""

    def __init__(self, tools=None, results=None):
        self.tools = tools if tools is not None else [{
            "name": "sonarr_queue", "description": "Sonarr's download queue",
            "inputSchema": {"type": "object", "properties": {}, "title": "x"},
        }]
        self.results = results or {}
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def list_tools(self):
        return self.tools

    def call_tool(self, name, args):
        self.calls.append((name, args))
        return self.results.get(name, {"text": f"{name} ok", "is_error": False})

    def close(self):
        self.closed = True


@pytest.fixture
def fake_mcp(monkeypatch):
    mcp = FakeMcp()
    monkeypatch.setattr("core.mcp_client.McpHttpClient",
                        lambda url, token="", **kw: mcp)
    return mcp


def _turns(*responses):
    """Scripted api_turn replies, in order."""
    seq = list(responses)

    def fake(root, provider, account_id, *, contents, declarations=None, model="",
             timeout=120, search=False):
        got = seq.pop(0)
        got.setdefault("ok", True)
        got.setdefault("parts", [])
        got.setdefault("text", "")
        got.setdefault("calls", [])
        got.setdefault("error", "")
        got.setdefault("model", model or "gemini-2.5-flash")
        got.setdefault("finish", "STOP")
        got["_contents"] = [dict(c) for c in contents]
        got["_declarations"] = declarations
        return got

    return fake, seq


def test_gemini_is_offered_the_same_tools_claude_gets(tmp_path, spawns, fake_mcp,
                                                      monkeypatch, with_tools):
    seen = {}

    def fake(root, provider, account_id, *, contents, declarations=None, **kw):
        seen["declarations"] = declarations
        return {"ok": True, "parts": [], "text": "done", "calls": [], "error": "",
                "model": "gemini-2.5-flash", "finish": "STOP"}

    monkeypatch.setattr(AP, "api_turn", fake)
    aid = _linked_gemini(tmp_path)

    rec = AR.run_agent(tmp_path, "hi", label="x", provider="gemini", account_id=aid,
                       mcp_url="http://127.0.0.1:8765/mcp")

    assert rec["ok"] is True
    assert [d["name"] for d in seen["declarations"]] == ["sonarr_queue"]
    assert any("Plutus MCP tools available: 1" in ln for ln in rec["log"]), rec["log"]
    assert fake_mcp.closed is True, "the MCP connection must not be leaked"


def test_gemini_actually_calls_the_tool_and_feeds_the_result_back(tmp_path, spawns,
                                                                  fake_mcp, monkeypatch,
                                                                  with_tools):
    """The whole point of function calling: a call the model asks for is really
    executed against Plutus, and its output goes back into the conversation."""
    fake_mcp.results["sonarr_queue"] = {"text": "3 stuck downloads", "is_error": False}
    fake, _ = _turns(
        {"calls": [{"name": "sonarr_queue", "args": {"params": {}}}],
         "parts": [{"functionCall": {"name": "sonarr_queue", "args": {"params": {}}}}]},
        {"text": "You have 3 stuck downloads."},
    )
    monkeypatch.setattr(AP, "api_turn", fake)
    aid = _linked_gemini(tmp_path)

    rec = AR.run_agent(tmp_path, "check sonarr", label="x", provider="gemini",
                       account_id=aid, mcp_url="http://127.0.0.1:8765/mcp")

    assert fake_mcp.calls == [("sonarr_queue", {"params": {}})]
    assert rec["ok"] is True and rec["result"] == "You have 3 stuck downloads."
    assert rec["turns"] == 2

    entries = AR.get_transcript(tmp_path, rec["id"]) or []
    kinds = [e["kind"] for e in entries]
    assert "tool_call" in kinds and "tool_result" in kinds
    assert any(e.get("text") == "3 stuck downloads" for e in entries)


def test_a_tool_error_goes_back_to_the_model_not_to_the_user(tmp_path, spawns,
                                                             fake_mcp, monkeypatch,
                                                             with_tools):
    """A failing tool is something the model should work around, not a dead run."""
    fake_mcp.results["sonarr_queue"] = {"text": "Sonarr is not configured", "is_error": True}
    fake, seq = _turns(
        {"calls": [{"name": "sonarr_queue", "args": {}}],
         "parts": [{"functionCall": {"name": "sonarr_queue", "args": {}}}]},
        {"text": "Sonarr isn't set up, so I couldn't check."},
    )
    monkeypatch.setattr(AP, "api_turn", fake)
    aid = _linked_gemini(tmp_path)

    rec = AR.run_agent(tmp_path, "check", label="x", provider="gemini", account_id=aid,
                       mcp_url="http://127.0.0.1:8765/mcp")
    assert rec["ok"] is True
    assert "couldn't check" in rec["result"]


def test_the_connection_acl_reaches_gemini(tmp_path, spawns, fake_mcp, monkeypatch,
                                           with_tools):
    """A tool the run was not scoped to must never even be named to the model."""
    fake_mcp.tools = [
        {"name": "sonarr_queue", "description": "a", "inputSchema": {"type": "object"}},
        {"name": "docker_stop_container", "description": "b", "inputSchema": {"type": "object"}},
    ]
    seen = {}

    def fake(root, provider, account_id, *, contents, declarations=None, **kw):
        seen["names"] = [d["name"] for d in declarations or []]
        return {"ok": True, "parts": [], "text": "ok", "calls": [], "error": "",
                "model": "m", "finish": "STOP"}

    monkeypatch.setattr(AP, "api_turn", fake)
    aid = _linked_gemini(tmp_path)

    AR.run_agent(tmp_path, "hi", label="x", provider="gemini", account_id=aid,
                 mcp_url="http://x/mcp",
                 disallowed_tools=["mcp__plutus__docker_stop_container"])
    assert seen["names"] == ["sonarr_queue"]


def test_a_runaway_tool_loop_is_stopped(tmp_path, spawns, fake_mcp, monkeypatch,
                                        with_tools):
    def fake(root, provider, account_id, *, contents, declarations=None, **kw):
        return {"ok": True, "text": "", "error": "", "model": "m", "finish": "STOP",
                "calls": [{"name": "sonarr_queue", "args": {}}],
                "parts": [{"functionCall": {"name": "sonarr_queue", "args": {}}}]}

    monkeypatch.setattr(AP, "api_turn", fake)
    aid = _linked_gemini(tmp_path)

    rec = AR.run_agent(tmp_path, "loop", label="x", provider="gemini", account_id=aid,
                       mcp_url="http://x/mcp")
    assert rec["ok"] is False
    assert f"{AR.MAX_TOOL_TURNS} tool rounds" in rec["error"]
    assert len(fake_mcp.calls) == AR.MAX_TOOL_TURNS


def test_an_unreachable_mcp_endpoint_degrades_instead_of_failing(tmp_path, spawns,
                                                                 monkeypatch, with_tools):
    """Losing tools is a worse run. Losing the run is worse still."""
    class Dead:
        def __init__(self, *a, **kw): pass
        def list_tools(self): raise RuntimeError("connection refused")
        def close(self): pass

    monkeypatch.setattr("core.mcp_client.McpHttpClient", Dead)
    monkeypatch.setattr(AP, "api_turn", lambda *a, **kw: {
        "ok": True, "parts": [], "text": "answered anyway", "calls": [], "error": "",
        "model": "m", "finish": "STOP"})
    aid = _linked_gemini(tmp_path)

    rec = AR.run_agent(tmp_path, "hi", label="x", provider="gemini", account_id=aid,
                       mcp_url="http://127.0.0.1:1/mcp")
    assert rec["ok"] is True and rec["result"] == "answered anyway"
    assert any("could not read Plutus tools" in ln for ln in rec["log"]), rec["log"]


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

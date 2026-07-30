"""Which credential an agent run uses — and saying so out loud.

The live failure this pins: a mounted ~/.claude/.credentials.json that merely
*exists* was preferred unconditionally, so a freshly pasted session token was
silently discarded. Updating the token had literally no effect and every run came
back "401 Invalid bearer token" against dead credentials, with nothing in the log
indicating which credential had been used.
"""
from __future__ import annotations

import json

import pytest

from core import agent_runner as AR


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point ~/.claude at a temp dir so tests never touch the real login."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(AR.os.path, "expanduser",
                        lambda p: str(home / p[2:]) if p.startswith("~/") else p)
    return home / ".claude"


def _set_token(monkeypatch, token: str, saved_at: int):
    from core import agent_login
    monkeypatch.setattr(agent_login, "read_env",
                        lambda: ({agent_login.TOKEN_KEY: token} if token else {}))
    monkeypatch.setattr(agent_login, "token_saved_at", lambda: saved_at)


# ── precedence ───────────────────────────────────────────────────────────────

def test_no_credentials_at_all_is_reported_as_such(fake_home, monkeypatch):
    _set_token(monkeypatch, "", 0)
    source, why = AR.legacy_credential_source()
    assert source == "none"
    assert "no claude credentials" in why.lower()


def test_only_a_cli_login(fake_home, monkeypatch):
    (fake_home / ".credentials.json").write_text("{}", encoding="utf-8")
    _set_token(monkeypatch, "", 0)
    source, why = AR.legacy_credential_source()
    assert source == "cli"
    assert ".credentials.json" in why


def test_only_a_saved_token(fake_home, monkeypatch):
    _set_token(monkeypatch, "sk-ant-oat01-abc", 1000)
    source, _ = AR.legacy_credential_source()
    assert source == "token"


def test_a_newly_saved_token_beats_an_older_cli_login(fake_home, monkeypatch):
    """The bug: pasting a fresh token did nothing because the old file won."""
    cred = fake_home / ".credentials.json"
    cred.write_text("{}", encoding="utf-8")
    import os as _os
    _os.utime(cred, (1_000_000, 1_000_000))
    _set_token(monkeypatch, "sk-ant-oat01-fresh", 2_000_000)   # saved later

    source, why = AR.legacy_credential_source()
    assert source == "token", "a token the user just pasted must win"
    assert "newer" in why


def test_a_newer_cli_login_beats_an_older_token(fake_home, monkeypatch):
    """The opposite failure must stay fixed too: a stale token must not shadow a
    login the user just completed."""
    cred = fake_home / ".credentials.json"
    cred.write_text("{}", encoding="utf-8")
    import os as _os
    _os.utime(cred, (3_000_000, 3_000_000))
    _set_token(monkeypatch, "sk-ant-oat01-stale", 1_000_000)

    source, why = AR.legacy_credential_source()
    assert source == "cli"
    assert "newer" in why


# ── the environment matches the decision ─────────────────────────────────────

def test_env_uses_the_token_when_the_token_wins(fake_home, monkeypatch):
    cred = fake_home / ".credentials.json"
    cred.write_text("{}", encoding="utf-8")
    import os as _os
    _os.utime(cred, (1_000, 1_000))
    _set_token(monkeypatch, "sk-ant-oat01-fresh", 9_000_000)
    from core import env_store
    monkeypatch.setattr(env_store, "read_env",
                        lambda *a, **k: {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-fresh"})

    env = AR._subprocess_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-fresh"
    assert "ANTHROPIC_API_KEY" not in env


def test_env_strips_the_token_when_the_cli_login_wins(fake_home, monkeypatch):
    cred = fake_home / ".credentials.json"
    cred.write_text("{}", encoding="utf-8")
    import os as _os
    _os.utime(cred, (9_000_000, 9_000_000))
    _set_token(monkeypatch, "sk-ant-oat01-stale", 1_000)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-stale")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-stale")

    env = AR._subprocess_env()
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_a_deliberate_api_key_survives_when_there_is_nothing_else(fake_home, monkeypatch):
    """ANTHROPIC_API_KEY is a documented compose-only opt-in; with no token and no
    CLI login it must not be stripped."""
    _set_token(monkeypatch, "", 0)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-deliberate")
    env = AR._subprocess_env()
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-api-deliberate"


# ── visibility and fail-fast ─────────────────────────────────────────────────

class _FakeProc:
    returncode = 0
    stderr = None

    def __init__(self):
        self.stdout = [json.dumps({"type": "result", "subtype": "success",
                                   "total_cost_usd": 0.1, "num_turns": 1,
                                   "result": "done"}) + "\n"]

    def wait(self): return 0
    def kill(self): pass


def test_the_run_log_names_the_credential_used(tmp_path, fake_home, monkeypatch):
    (fake_home / ".credentials.json").write_text("{}", encoding="utf-8")
    _set_token(monkeypatch, "", 0)
    monkeypatch.setattr(AR, "load_agent_config", lambda root: {
        **AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": False})
    monkeypatch.setattr(AR.subprocess, "Popen", lambda *a, **k: _FakeProc())

    rec = AR.run_agent(tmp_path, "hi", label="x")
    assert rec["auth_source"] == "cli"
    assert any(line.startswith("auth: ") for line in rec["log"]), rec["log"]


def test_no_credentials_fails_before_spending_a_run(tmp_path, fake_home, monkeypatch):
    """Previously this spawned the CLI and came back 401; now it says what to do."""
    _set_token(monkeypatch, "", 0)
    monkeypatch.setattr(AR, "load_agent_config", lambda root: {
        **AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": False})

    spawned = {"n": 0}

    def _no_spawn(*a, **k):
        spawned["n"] += 1
        return _FakeProc()

    monkeypatch.setattr(AR.subprocess, "Popen", _no_spawn)
    rec = AR.run_agent(tmp_path, "hi", label="x")

    assert spawned["n"] == 0, "must not spawn the CLI with no credentials"
    assert rec["ok"] is False
    assert "docker exec -it plutus-mcp claude" in (rec["error"] or "")

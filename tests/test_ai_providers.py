"""AI provider runtimes: multi-account isolation, auth state, capability tests.

The unit of authentication is a credentials *directory*, not a token — these tests
pin that, because the old single CLAUDE_CODE_OAUTH_TOKEN was injected into every
run and overrode any real CLI login, producing a bare 401 with no cause.
"""
from __future__ import annotations

import json

import pytest

from core import ai_providers as AP


# ── accounts ─────────────────────────────────────────────────────────────────

def test_accounts_are_isolated_by_config_dir(tmp_path):
    a = AP.add_account(tmp_path, "claude", "Personal Pro")
    b = AP.add_account(tmp_path, "claude", "Work Max")

    da = AP.account_dir(tmp_path, "claude", a["id"])
    db = AP.account_dir(tmp_path, "claude", b["id"])
    assert da != db and da.is_dir() and db.is_dir()

    # Each account points the CLI at its own directory — that is what makes
    # multiple simultaneous logins possible at all.
    env_a = AP.account_env(tmp_path, "claude", a["id"])
    env_b = AP.account_env(tmp_path, "claude", b["id"])
    assert env_a["CLAUDE_CONFIG_DIR"] == str(da)
    assert env_b["CLAUDE_CONFIG_DIR"] == str(db)
    assert env_a != env_b


def test_duplicate_label_is_rejected(tmp_path):
    AP.add_account(tmp_path, "claude", "Personal")
    with pytest.raises(ValueError, match="already exists"):
        AP.add_account(tmp_path, "claude", "Personal")


def test_unknown_provider_and_bad_account_id_are_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown provider"):
        AP.add_account(tmp_path, "notaprovider", "x")
    # account_id becomes a filesystem path — traversal must not be possible.
    for bad in ("../../etc", "a/b", ".hidden", ""):
        with pytest.raises(ValueError, match="invalid account id"):
            AP.account_dir(tmp_path, "claude", bad)


def test_auth_state_follows_the_credentials_file(tmp_path):
    acct = AP.add_account(tmp_path, "claude", "Personal")
    st = AP.account_status(tmp_path, "claude", acct)
    assert st["authenticated"] is False and st["state"] == "login_required"

    (AP.account_dir(tmp_path, "claude", acct["id"]) / ".credentials.json").write_text("{}", encoding="utf-8")
    st = AP.account_status(tmp_path, "claude", acct)
    assert st["authenticated"] is True and st["state"] == "connected"


def test_logout_drops_credentials_but_keeps_the_account(tmp_path):
    acct = AP.add_account(tmp_path, "claude", "Personal")
    cred = AP.account_dir(tmp_path, "claude", acct["id"]) / ".credentials.json"
    cred.write_text("{}", encoding="utf-8")

    assert AP.logout_account(tmp_path, "claude", acct["id"]) is True
    assert not cred.exists()
    assert AP.get_account(tmp_path, "claude", acct["id"]) is not None


def test_remove_account_deletes_its_credentials_directory(tmp_path):
    acct = AP.add_account(tmp_path, "claude", "Personal")
    d = AP.account_dir(tmp_path, "claude", acct["id"])
    (d / ".credentials.json").write_text("{}", encoding="utf-8")

    assert AP.remove_account(tmp_path, "claude", acct["id"]) is True
    assert not d.exists()
    assert AP.get_account(tmp_path, "claude", acct["id"]) is None


def test_account_store_survives_a_corrupt_file(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / AP.ACCOUNTS_FILE).write_text("{not json", encoding="utf-8")
    data = AP.load_accounts(tmp_path)
    assert data == {p: [] for p in AP.PROVIDERS}


def test_provider_status_reports_a_real_state(tmp_path, monkeypatch):
    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": False, "path": "",
                                                   "version": "", "install_hint": "npm i -g x"})
    st = AP.provider_status(tmp_path, "claude")
    assert st["state"] == "cli_missing"

    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": True, "path": "/usr/bin/claude",
                                                   "version": "2.1.92", "install_hint": ""})
    assert AP.provider_status(tmp_path, "claude")["state"] == "no_accounts"

    acct = AP.add_account(tmp_path, "claude", "Personal")
    assert AP.provider_status(tmp_path, "claude")["state"] == "login_required"

    (AP.account_dir(tmp_path, "claude", acct["id"]) / ".credentials.json").write_text("{}", encoding="utf-8")
    assert AP.provider_status(tmp_path, "claude")["state"] == "connected"


def test_login_hint_targets_the_unlinked_account(tmp_path, monkeypatch):
    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": True, "path": "/usr/bin/claude",
                                                   "version": "", "install_hint": ""})
    acct = AP.add_account(tmp_path, "claude", "Personal")
    hint = AP.provider_status(tmp_path, "claude")["login_command"]
    assert "docker exec -it" in hint
    assert f"CLAUDE_CONFIG_DIR={AP.account_dir(tmp_path, 'claude', acct['id'])}" in hint


# ── capability tests ─────────────────────────────────────────────────────────

def test_capability_test_stops_at_the_first_real_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": True, "path": "/usr/bin/claude",
                                                   "version": "2.1.92", "install_hint": ""})
    acct = AP.add_account(tmp_path, "claude", "Personal")

    res = AP.capability_test(tmp_path, "claude", acct["id"])
    assert res["ok"] is False
    names = [c["name"] for c in res["checks"]]
    assert names == ["CLI installed", "Session credentials present"]
    assert "no login found" in res["checks"][-1]["detail"]


def test_capability_test_executes_a_real_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": True, "path": "/usr/bin/claude",
                                                   "version": "2.1.92", "install_hint": ""})
    acct = AP.add_account(tmp_path, "claude", "Personal")
    (AP.account_dir(tmp_path, "claude", acct["id"]) / ".credentials.json").write_text("{}", encoding="utf-8")

    seen = {}

    def fake_run(cmd, env, timeout):
        seen["cmd"] = cmd
        seen["config_dir"] = env.get("CLAUDE_CONFIG_DIR")
        seen["has_api_key"] = "ANTHROPIC_API_KEY" in env
        return {"code": 0, "out": "PLUTUS_OK", "err": "", "timeout": False}

    monkeypatch.setattr(AP, "_run_cli", fake_run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-should-be-stripped")

    res = AP.capability_test(tmp_path, "claude", acct["id"])
    assert res["ok"] is True
    assert [c["name"] for c in res["checks"]][-1] == "Can execute prompt"
    # It ran the account's own directory, with ambient credentials stripped.
    assert seen["config_dir"] == str(AP.account_dir(tmp_path, "claude", acct["id"]))
    assert seen["has_api_key"] is False
    # And the prompt is guarded by `--`, or the CLI eats it as an option value.
    assert seen["cmd"][-2] == "--"


def test_a_401_is_explained_not_parroted(tmp_path, monkeypatch):
    """The whole point of the CLI-runtime model: 'Invalid bearer token' is not a
    useful thing to show a user who never configured a bearer token."""
    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": True, "path": "/usr/bin/claude",
                                                   "version": "", "install_hint": ""})
    acct = AP.add_account(tmp_path, "claude", "Personal")
    (AP.account_dir(tmp_path, "claude", acct["id"]) / ".credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(AP, "_run_cli", lambda *a, **k: {
        "code": 1, "out": "", "err": "Failed to authenticate. API Error: 401 Invalid bearer token",
        "timeout": False})

    res = AP.capability_test(tmp_path, "claude", acct["id"])
    detail = res["checks"][-1]["detail"]
    assert "re-link this account" in detail
    assert "Invalid bearer token" not in detail


def test_out_of_usage_is_not_reported_as_an_auth_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": True, "path": "/usr/bin/claude",
                                                   "version": "", "install_hint": ""})
    acct = AP.add_account(tmp_path, "claude", "Personal")
    (AP.account_dir(tmp_path, "claude", acct["id"]) / ".credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(AP, "_run_cli", lambda *a, **k: {
        "code": 1, "out": "You're out of extra usage · resets 12:20am", "err": "", "timeout": False})

    detail = AP.capability_test(tmp_path, "claude", acct["id"])["checks"][-1]["detail"]
    assert "out of usage" in detail
    assert "re-link" not in detail


def test_codex_is_detected_but_not_claimed_runnable(tmp_path, monkeypatch):
    """Declaring a provider we cannot actually drive must not report healthy."""
    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": True, "path": "/usr/bin/codex",
                                                   "version": "1.0", "install_hint": ""})
    acct = AP.add_account(tmp_path, "codex", "Personal")
    (AP.account_dir(tmp_path, "codex", acct["id"]) / "auth.json").write_text("{}", encoding="utf-8")

    res = AP.capability_test(tmp_path, "codex", acct["id"])
    assert res["ok"] is False
    assert any("cannot run agents through it yet" in c["detail"] for c in res["checks"])


# ── the agent runner honours the selected account ────────────────────────────

def test_agent_env_uses_the_selected_account_and_strips_ambient_creds(tmp_path, monkeypatch):
    from core import agent_runner as AR

    acct = AP.add_account(tmp_path, "claude", "Work Max")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-stale")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-stale")

    env = AR._subprocess_env(tmp_path, "claude", acct["id"])
    assert env["CLAUDE_CONFIG_DIR"] == str(AP.account_dir(tmp_path, "claude", acct["id"]))
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["IS_SANDBOX"] == "1"


def test_agent_env_without_an_account_keeps_legacy_behaviour(tmp_path, monkeypatch):
    from core import agent_runner as AR

    monkeypatch.setattr(AR, "cli_logged_in", lambda: False)
    env = AR._subprocess_env()
    assert "CLAUDE_CONFIG_DIR" not in env


def test_run_record_notes_which_account_executed_it(tmp_path, monkeypatch):
    """A failed run has to be traceable to the login that failed."""
    from core import agent_runner as AR

    monkeypatch.setattr(AR, "load_agent_config", lambda root: {
        **AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": False})

    class _P:
        returncode = 0
        stdout = [json.dumps({"type": "result", "subtype": "success",
                              "total_cost_usd": 0.1, "num_turns": 1, "result": "done"}) + "\n"]
        stderr = None

        def wait(self): return 0
        def kill(self): pass

    monkeypatch.setattr(AR.subprocess, "Popen", lambda *a, **k: _P())
    rec = AR.run_agent(tmp_path, "hi", label="x", provider="claude", account_id="work-max-abc123")
    assert rec["provider"] == "claude"
    assert rec["account_id"] == "work-max-abc123"

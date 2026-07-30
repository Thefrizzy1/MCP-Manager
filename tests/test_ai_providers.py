"""AI provider runtimes: multi-account isolation, auth state, capability tests.

The unit of authentication is a credentials *directory*, not a token — these tests
pin that, because the old single CLAUDE_CODE_OAUTH_TOKEN was injected into every
run and overrode any real CLI login, producing a bare 401 with no cause.
"""
from __future__ import annotations

import json

import pytest

from core import ai_providers as AP


@pytest.fixture(autouse=True)
def _isolated_cli_homes(tmp_path_factory, monkeypatch):
    """Point every provider's default home at an empty temp dir.

    account_status() now looks for an unclaimed login in the CLI's default home so
    it can offer to adopt it. Left unstubbed that reads the *developer's* real
    ~/.claude, so tests asserting "not linked" pass or fail depending on whether the
    machine happens to be logged in — the same divergence that broke CI twice.
    Tests that exercise adoption override this with their own directory.
    """
    base = tmp_path_factory.mktemp("cli-homes")
    monkeypatch.setattr(AP, "default_home", lambda provider: base / provider)


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


# ── the login command must match the provider ────────────────────────────────

def test_login_command_is_per_provider(tmp_path):
    """The UI hardcoded the Claude form, so a Codex account was told to run
    `CLAUDE_CONFIG_DIR=… plutus-mcp claude` — wrong env var, wrong binary."""
    claude = AP.add_account(tmp_path, "claude", "Personal Pro")
    codex = AP.add_account(tmp_path, "codex", "Personal ChatGPT")
    gemini = AP.add_account(tmp_path, "gemini", "Personal Google")

    c = AP.login_command(tmp_path, "claude", claude["id"])
    assert "CLAUDE_CONFIG_DIR=" in c and c.endswith("plutus-mcp claude")

    x = AP.login_command(tmp_path, "codex", codex["id"])
    assert "CODEX_HOME=" in x and x.endswith("plutus-mcp codex")
    assert "CLAUDE" not in x and " claude" not in x

    # Gemini has no config-dir override, so its command carries no env var at all.
    g = AP.login_command(tmp_path, "gemini", gemini["id"])
    assert g == "docker exec -it plutus-mcp gemini"

    # Each account's own directory, so two accounts never share a login.
    assert AP.account_dir(tmp_path, "codex", codex["id"]).name in x


def test_a_provider_without_an_override_gets_no_env_var(tmp_path):
    """GEMINI_CONFIG_DIR does not exist. Handing the user an env var the CLI
    ignores is worse than none: the login appears to work, lands in ~/.gemini, and
    the account still reads "not linked" with nothing explaining why."""
    acct = AP.add_account(tmp_path, "gemini", "Personal")

    assert AP.supports_isolation("gemini") is False
    assert AP.account_env(tmp_path, "gemini", acct["id"]) == {}

    cmd = AP.login_command(tmp_path, "gemini", acct["id"])
    assert cmd == "docker exec -it plutus-mcp gemini"
    assert "CONFIG_DIR" not in cmd and "-e " not in cmd


def test_providers_with_a_real_override_still_isolate(tmp_path):
    """CODEX_HOME and CLAUDE_CONFIG_DIR are documented and do work."""
    for pid, var in (("claude", "CLAUDE_CONFIG_DIR"), ("codex", "CODEX_HOME")):
        acct = AP.add_account(tmp_path, pid, "Personal")
        assert AP.supports_isolation(pid) is True
        assert AP.account_env(tmp_path, pid, acct["id"])[var] == \
            str(AP.account_dir(tmp_path, pid, acct["id"]))
        assert f"-e {var}=" in AP.login_command(tmp_path, pid, acct["id"])


def test_a_login_in_the_default_home_is_offered_for_adoption(tmp_path, monkeypatch):
    home = tmp_path / "home" / ".gemini"
    home.mkdir(parents=True)
    monkeypatch.setattr(AP, "default_home", lambda p: home)

    acct = AP.add_account(tmp_path, "gemini", "Personal")
    st = AP.account_status(tmp_path, "gemini", acct)
    assert st["adoptable"] is False and st["state"] == "login_required"

    (home / "oauth_creds.json").write_text("{}", encoding="utf-8")
    st = AP.account_status(tmp_path, "gemini", acct)
    assert st["adoptable"] is True
    assert st["state"] == "adoptable"
    assert st["authenticated"] is False, "an unclaimed login is not this account's yet"


def test_adopting_a_login_claims_it_for_the_account(tmp_path, monkeypatch):
    home = tmp_path / "home" / ".gemini"
    home.mkdir(parents=True)
    (home / "oauth_creds.json").write_text('{"token":"a"}', encoding="utf-8")
    monkeypatch.setattr(AP, "default_home", lambda p: home)

    acct = AP.add_account(tmp_path, "gemini", "Personal")
    res = AP.adopt_login(tmp_path, "gemini", acct["id"])
    assert res["ok"] is True and "oauth_creds.json" in res["copied"]

    st = AP.account_status(tmp_path, "gemini", acct)
    assert st["authenticated"] is True and st["state"] == "connected"

    # A second identity: log in again, adopt into a different account. Each keeps
    # its own copy, which is what makes multi-account work without an override.
    (home / "oauth_creds.json").write_text('{"token":"b"}', encoding="utf-8")
    other = AP.add_account(tmp_path, "gemini", "Work")
    AP.adopt_login(tmp_path, "gemini", other["id"])

    first = (AP.account_dir(tmp_path, "gemini", acct["id"]) / "oauth_creds.json").read_text()
    second = (AP.account_dir(tmp_path, "gemini", other["id"]) / "oauth_creds.json").read_text()
    assert first == '{"token":"a"}' and second == '{"token":"b"}'


def test_adopting_with_no_login_explains_the_keyring_case(tmp_path, monkeypatch):
    home = tmp_path / "home" / ".gemini"
    home.mkdir(parents=True)
    monkeypatch.setattr(AP, "default_home", lambda p: home)
    acct = AP.add_account(tmp_path, "gemini", "Personal")

    res = AP.adopt_login(tmp_path, "gemini", acct["id"])
    assert res["ok"] is False
    assert "keyring" in res["error"], "the keyring case has to be named, not guessed at"


def test_account_status_carries_its_own_login_command(tmp_path):
    acct = AP.add_account(tmp_path, "codex", "Work")
    st = AP.account_status(tmp_path, "codex", acct)
    assert "CODEX_HOME=" in st["login_command"]
    assert st["provider_label"] == "Codex"
    assert st["role"] == AP.ROLE_CODING


# ── provider roles and runnability ───────────────────────────────────────────

def test_every_provider_is_runnable_with_an_exec_builder():
    for pid, spec in AP.PROVIDERS.items():
        assert spec["runnable"] is True, pid
        assert callable(spec["exec"]), pid


def test_roles_route_coding_and_research_separately():
    assert AP.PROVIDERS["codex"]["role"] == AP.ROLE_CODING
    assert AP.PROVIDERS["gemini"]["role"] == AP.ROLE_RESEARCH
    assert AP.PROVIDERS["claude"]["role"] == AP.ROLE_GENERAL


def test_each_cli_is_driven_with_its_own_flags():
    """The capability test used to hardcode Claude's `-p --output-format text`,
    which no other CLI understands."""
    claude = AP.PROVIDERS["claude"]["exec"]("hello", "haiku")
    assert claude[:3] == ["-p", "--output-format", "text"]
    assert claude[-2:] == ["--", "hello"], "the prompt needs the -- guard"

    codex = AP.PROVIDERS["codex"]["exec"]("hello", "")
    assert codex == ["exec", "hello"]
    assert AP.PROVIDERS["codex"]["exec"]("hello", "gpt-5") == ["exec", "--model", "gpt-5", "hello"]

    gemini = AP.PROVIDERS["gemini"]["exec"]("hello", "")
    assert gemini == ["-p", "hello"], "gemini takes the prompt as -p's value"


def test_capability_test_invokes_the_providers_own_command(tmp_path, monkeypatch):
    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": True, "path": f"/usr/bin/{p}",
                                                   "version": "1.0", "install_hint": ""})
    acct = AP.add_account(tmp_path, "codex", "Personal")
    (AP.account_dir(tmp_path, "codex", acct["id"]) / "auth.json").write_text("{}", encoding="utf-8")

    seen = {}

    def fake_run(cmd, env, timeout):
        seen["cmd"] = cmd
        seen["home"] = env.get("CODEX_HOME")
        return {"code": 0, "out": "PLUTUS_OK", "err": "", "timeout": False}

    monkeypatch.setattr(AP, "_run_cli", fake_run)
    res = AP.capability_test(tmp_path, "codex", acct["id"])

    assert res["ok"] is True
    assert seen["cmd"][1] == "exec", seen["cmd"]
    assert "--output-format" not in seen["cmd"], "Claude's flags must not leak into Codex"
    assert seen["home"] == str(AP.account_dir(tmp_path, "codex", acct["id"]))


def test_mcp_check_is_only_claimed_for_claude(tmp_path, monkeypatch):
    """--mcp-config is Claude-specific; asserting MCP works for the others would
    be a lie."""
    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": True, "path": f"/usr/bin/{p}",
                                                   "version": "", "install_hint": ""})
    acct = AP.add_account(tmp_path, "gemini", "Personal")
    (AP.account_dir(tmp_path, "gemini", acct["id"]) / "oauth_creds.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(AP, "_run_cli", lambda *a, **k: {"code": 0, "out": "PLUTUS_OK",
                                                        "err": "", "timeout": False})

    res = AP.capability_test(tmp_path, "gemini", acct["id"], mcp_config_path="/tmp/x.json")
    assert [c["name"] for c in res["checks"]] == ["CLI installed", "Session credentials present",
                                                  "Can execute prompt"]


# ── the CLIs must survive a redeploy ─────────────────────────────────────────

def test_every_provider_cli_is_baked_into_the_image():
    """`docker exec plutus-mcp npm install -g …` writes to the container's
    writable layer, which `docker compose up -d` discards when it recreates the
    container from a pulled image. A hand-installed Codex or Gemini therefore
    vanished on every update and the card reverted to "CLI not installed". The
    only durable place is the image."""
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    install_line = next(ln for ln in dockerfile.splitlines()
                        if "npm install -g" in ln and not ln.lstrip().startswith("#"))
    for pkg in ("@anthropic-ai/claude-code", "@openai/codex", "@google/gemini-cli"):
        assert pkg in install_line, f"{pkg} is not installed in the image"


def test_compose_persists_every_provider_login():
    """Gemini has no config-dir override, so it always writes ~/.gemini. Without a
    mount that login dies with the container on the next update."""
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
    for home in ("/root/.claude", "/root/.codex", "/root/.gemini"):
        assert home in compose, f"{home} is not persisted across container recreation"


def test_cli_detection_is_cached_but_can_be_forced(monkeypatch):
    """The card polls, and `--version` shells out — so the probe is memoised. It
    must still be forceable, or an install would take a TTL to appear."""
    calls = {"n": 0}

    def counting_resolve(name):
        calls["n"] += 1
        return None

    AP.forget_cli_info()
    monkeypatch.setattr(AP, "resolve_cli", counting_resolve)

    AP.cli_info("claude")
    AP.cli_info("claude")
    assert calls["n"] == 1, "the second read should have come from the cache"

    AP.cli_info("claude", fresh=True)
    assert calls["n"] == 2

    AP.forget_cli_info("claude")
    AP.cli_info("claude")
    assert calls["n"] == 3
    AP.forget_cli_info()


# ── CLI resolution ───────────────────────────────────────────────────────────

def test_resolve_cli_finds_an_auto_updated_native_install(tmp_path, monkeypatch):
    """Claude Code auto-updates itself into ~/.local/bin, which the container's
    ENV PATH does not include. `docker exec … claude` worked (login-shell PATH)
    while the app's Popen failed with a bare FileNotFoundError on a CLI that was
    plainly installed."""
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    binary = home / ".local" / "bin" / "claude"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)

    monkeypatch.setattr(AP.shutil, "which", lambda n: None)          # not on PATH
    monkeypatch.setattr(AP.os.path, "expanduser",
                        lambda p: str(home / p[2:]) if p.startswith("~/") else p)

    assert AP.resolve_cli("claude") == str(binary)


def test_resolve_cli_prefers_path_when_present(monkeypatch):
    monkeypatch.setattr(AP.shutil, "which", lambda n: "/usr/bin/claude")
    assert AP.resolve_cli("claude") == "/usr/bin/claude"


def test_resolve_cli_returns_none_when_really_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(AP.shutil, "which", lambda n: None)
    monkeypatch.setattr(AP.os.path, "expanduser",
                        lambda p: str(tmp_path / "empty") if p.startswith("~/") else p)
    assert AP.resolve_cli("claude") is None


def test_child_path_includes_the_native_install_dir(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    p = AP.cli_search_path()
    assert "/usr/bin" in p
    assert any("local" in part for part in p.split(AP.os.pathsep)), p


def test_missing_cli_error_says_where_it_looked(tmp_path, monkeypatch):
    """The old message told you to install a CLI you had already installed."""
    from core import agent_runner as AR

    monkeypatch.setattr(AR, "load_agent_config", lambda root: {
        **AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": False})
    monkeypatch.setattr(AR, "legacy_credential_source", lambda: ("cli", "test"))
    monkeypatch.setattr(AP, "resolve_cli", lambda n: None)

    rec = AR.run_agent(tmp_path, "hi", label="x")
    err = rec["error"] or ""
    assert "Looked in" in err and "~/.local/bin" in err
    assert "command -v claude" in err


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


def test_a_non_runnable_provider_never_reports_healthy(tmp_path, monkeypatch):
    """Every shipped provider is runnable now, but the guard has to stay: a
    provider we cannot actually drive must say so rather than report healthy and
    fail on first use. Tested with a synthetic provider so it keeps working as
    real ones come and go."""
    monkeypatch.setitem(AP.PROVIDERS, "toy", {
        **AP.PROVIDERS["claude"], "label": "Toy CLI", "cli": "toy", "runnable": False,
    })
    monkeypatch.setattr(AP, "cli_info", lambda p: {"installed": True, "path": "/usr/bin/toy",
                                                   "version": "1.0", "install_hint": ""})
    acct = AP.add_account(tmp_path, "toy", "Personal")

    res = AP.capability_test(tmp_path, "toy", acct["id"])
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


def test_run_record_notes_which_account_executed_it(tmp_path, monkeypatch, agent_preconditions):
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

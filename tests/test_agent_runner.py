"""Agent runner — command building, event parsing, config, run storage (offline)."""
from core import agent_runner as ar


def test_build_cmd_basic():
    cfg = {"skip_permissions": True, "allowed_tools": ["mcp__plutus", "Read"], "model": "claude-sonnet-5"}
    cmd = ar.build_agent_cmd("do the thing", cfg, mcp_config_path="/x/mcp.json")
    assert cmd[0] == "claude" and cmd[1] == "-p"
    assert "--output-format" in cmd and "stream-json" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--allowedTools" in cmd and "mcp__plutus,Read" in cmd
    assert "--mcp-config" in cmd and "/x/mcp.json" in cmd
    assert "--model" in cmd and "claude-sonnet-5" in cmd
    assert cmd[-1] == "do the thing"  # prompt is last


def test_build_cmd_disallowed_and_model_override():
    cmd = ar.build_agent_cmd("hi", {"skip_permissions": True, "allowed_tools": ["mcp__plutus"], "model": "sonnet"},
                             disallowed_tools=["mcp__plutus__docker_stop_container"], model="opus")
    assert "--disallowedTools" in cmd and "mcp__plutus__docker_stop_container" in cmd
    # explicit model arg overrides the config model
    assert cmd[cmd.index("--model") + 1] == "opus"


def test_runs_today_counts_today(tmp_path):
    import datetime
    today = datetime.datetime.now().astimezone().strftime("%Y%m%d")
    ar.save_run(tmp_path, {"id": today + "-120000-aaaa", "cost_usd": 0})
    ar.save_run(tmp_path, {"id": "20200101-000000-bbbb", "cost_usd": 0})  # old
    assert ar.runs_today(tmp_path) == 1


def test_auth_info_session_token_mode(monkeypatch):
    from core import env_store
    monkeypatch.setattr(env_store, "read_env", lambda path=None: {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x"})
    monkeypatch.setattr(ar, "cli_logged_in", lambda: False)  # no CLI login -> token is used
    info = ar.auth_info()
    assert info["mode"] == "session_token"
    assert info["session_token"] is True


def test_cli_login_wins_over_saved_token(monkeypatch):
    """A real ~/.claude CLI session must be reported/used ahead of any saved
    token or API key — the fix for the '401 Invalid bearer token' bug."""
    from core import env_store
    monkeypatch.setattr(ar, "cli_logged_in", lambda: True)
    monkeypatch.setattr(env_store, "read_env", lambda path=None: {"CLAUDE_CODE_OAUTH_TOKEN": "stale"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-bad")
    assert ar.auth_info()["mode"] == "subscription"


# The credential decision runs through cli_credentials_file() + the token's saved-at
# stamp (see agent_runner.legacy_credential_source), so these patch that seam
# rather than cli_logged_in — otherwise the result depends on whether the machine
# running the tests happens to have a real ~/.claude login.

def test_subprocess_env_strips_stale_creds_when_cli_login_is_newer(monkeypatch, tmp_path):
    from core import agent_login, env_store
    cred = tmp_path / ".credentials.json"
    cred.write_text("{}", encoding="utf-8")
    import os
    os.utime(cred, (9_000_000, 9_000_000))          # login newer than the token
    monkeypatch.setattr(ar, "cli_credentials_file", lambda: cred)
    monkeypatch.setattr(agent_login, "read_env", lambda: {"CLAUDE_CODE_OAUTH_TOKEN": "stale"})
    monkeypatch.setattr(agent_login, "token_saved_at", lambda: 1_000)
    monkeypatch.setattr(env_store, "read_env", lambda path=None: {"CLAUDE_CODE_OAUTH_TOKEN": "stale"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-bad")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale")
    env = ar._subprocess_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_subprocess_env_uses_token_when_there_is_no_cli_login(monkeypatch):
    from core import agent_login, env_store
    monkeypatch.setattr(ar, "cli_credentials_file", lambda: None)
    monkeypatch.setattr(agent_login, "read_env", lambda: {"CLAUDE_CODE_OAUTH_TOKEN": "good"})
    monkeypatch.setattr(agent_login, "token_saved_at", lambda: 5_000)
    monkeypatch.setattr(env_store, "read_env", lambda path=None: {"CLAUDE_CODE_OAUTH_TOKEN": "good"})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = ar._subprocess_env()
    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == "good"


def test_build_cmd_minimal():
    cmd = ar.build_agent_cmd("hi", {"skip_permissions": False, "allowed_tools": [], "model": ""})
    assert "--dangerously-skip-permissions" not in cmd
    assert "--allowedTools" not in cmd
    assert "--model" not in cmd
    assert cmd[-1] == "hi"


def test_handle_event_init():
    out = ar.handle_event({"type": "system", "subtype": "init"}, "scout")
    assert out["line"] == "[scout] session started"
    assert out["result"] is None


def test_handle_event_assistant_text_and_tooluse():
    ev = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "thinking about it"},
        {"type": "tool_use", "name": "web_search", "input": {"query": "homelab"}},
    ]}}
    out = ar.handle_event(ev)
    assert "thinking about it" in out["line"]
    assert "web_search" in out["line"] and "homelab" in out["line"]


def test_handle_event_result():
    ev = {"type": "result", "total_cost_usd": 0.12, "num_turns": 4, "result": "done", "subtype": "success"}
    out = ar.handle_event(ev)
    assert out["result"]["cost_usd"] == 0.12
    assert out["result"]["turns"] == 4
    assert out["result"]["ok"] is True
    assert out["result"]["text"] == "done"


def test_handle_event_error_result():
    ev = {"type": "result", "is_error": True, "subtype": "error_max_turns"}
    assert ar.handle_event(ev)["result"]["ok"] is False


def test_run_storage_roundtrip(tmp_path):
    rec = {"id": "20260101-000000-abcd", "cost_usd": 0.5, "ok": True, "label": "t"}
    ar.save_run(tmp_path, rec)
    runs = ar.list_runs(tmp_path)
    assert runs and runs[0]["id"] == rec["id"]
    assert ar.total_cost(tmp_path) == 0.5


def test_agent_config_roundtrip(tmp_path):
    saved = ar.save_agent_config(tmp_path, {"model": "claude-opus-4-8", "max_cost_usd": 5.0})
    assert saved["model"] == "claude-opus-4-8"
    assert saved["max_cost_usd"] == 5.0
    # defaults preserved for unspecified keys
    assert "allowed_tools" in saved
    assert ar.load_agent_config(tmp_path)["model"] == "claude-opus-4-8"


def test_resolve_library_obsidian():
    lib, hint = ar.resolve_library({"output_mode": "obsidian", "obsidian_folder": "20-research"})
    assert lib == "20-research"
    assert "obsidian" in hint.lower()


def test_resolve_library_filesystem():
    lib, hint = ar.resolve_library({"output_mode": "filesystem", "fs_library_path": "/data/lib/"})
    assert lib == "/data/lib"  # trailing slash stripped
    assert "filesystem" in hint.lower()


def test_auth_info_api_key_mode(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
    monkeypatch.setattr(ar, "cli_logged_in", lambda: False)  # a CLI login would win otherwise
    from core import env_store
    monkeypatch.setattr(env_store, "read_env", lambda path=None: {})
    info = ar.auth_info()
    assert info["mode"] == "api_key"
    assert info["api_key"] is True


def test_auth_info_none_when_unauthenticated(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Point HOME at an empty dir so no ~/.claude login is detected.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows expanduser
    info = ar.auth_info()
    assert info["mode"] in ("none", "subscription")  # 'none' on a clean home
    assert info["api_key"] is False


def test_write_plutus_mcp_config(tmp_path, monkeypatch):
    """The endpoint and token still reach the server — through the stdio bridge
    now, which is what lets a scoped run be charged for its own scope instead of
    receiving all ~260 tool schemas on every request."""
    import json
    monkeypatch.delenv("PLUTUS_CLAUDE_MCP_HTTP", raising=False)
    p = ar.write_plutus_mcp_config(tmp_path, mcp_url="http://127.0.0.1:8765/mcp", token="secret")
    conf = json.loads(open(p, encoding="utf-8").read())
    server = conf["mcpServers"]["plutus"]
    assert server["args"] == [str(ar.MCP_BRIDGE)]
    assert server["env"]["PLUTUS_MCP_URL"] == "http://127.0.0.1:8765/mcp"
    assert server["env"]["PLUTUS_MCP_TOKEN"] == "secret"

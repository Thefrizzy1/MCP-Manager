"""Regressions for the issues found in the code audit.

Each test pins a bug that was live in the tree and that the type system, the
linter and the existing suite all missed. Grouped by the audit's severity so a
failure here tells you immediately how bad the regression is.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import pytest

from core.invoke_tool import invoke_mcp_tool_fn


def _tool(register, name, **kwargs):
    """Register a tool domain onto a throwaway FastMCP and return the tool's fn."""
    from mcp.server.fastmcp import FastMCP

    m = FastMCP("test", **kwargs)
    register(m)
    tool = m._tool_manager.get_tool(name)
    assert tool is not None, f"tool {name!r} was not registered"
    return tool.fn


# ── C1: blocking filesystem I/O must not stall the event loop ────────────────

def test_fs_search_does_not_block_the_event_loop(tmp_path, monkeypatch):
    """The MCP server is one process with one loop. A synchronous walk of a
    stalled NAS mount froze every concurrent request, including the SSE stream
    the client then dropped as dead."""
    from config import cfg
    from tools.system import register_system_tools

    monkeypatch.setattr(cfg, "filesystem_allowed_paths", [str(tmp_path)])

    # Stand in for a slow mount: one walk step that takes 300ms of wall clock.
    def slow_walk(path, followlinks=False):
        time.sleep(0.3)
        return iter(())

    monkeypatch.setattr(os, "walk", slow_walk)
    fn = _tool(register_system_tools, "fs_search_files")

    async def _main() -> int:
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(ticker())
        try:
            await invoke_mcp_tool_fn(fn, payload={"path": str(tmp_path), "pattern": "x"})
        finally:
            beat.cancel()
        return ticks

    ticks = asyncio.run(_main())
    # ~30 ticks are possible in 300ms. Anything above a handful proves the loop
    # stayed live; a blocking call pins this at 0-1.
    assert ticks >= 5, f"event loop was blocked during fs_search_files (only {ticks} ticks)"


# ── H1: the SSRF guard must screen every redirect hop ────────────────────────

def _patch_web_fetch_transport(monkeypatch, handler):
    import tools.utilities as U

    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs.pop("follow_redirects", None)
        return real_client(transport=httpx.MockTransport(handler), follow_redirects=False, **kwargs)

    monkeypatch.setattr(U.httpx, "AsyncClient", fake_client)
    return _tool(U.register_utility_tools, "web_fetch")


def test_web_fetch_screens_redirect_targets(monkeypatch):
    """A public page that 302s to link-local must be refused. Screening only the
    caller-supplied URL (follow_redirects=True) is not a guard at all."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="cloud credentials", headers={"content-type": "text/plain"})

    fn = _patch_web_fetch_transport(monkeypatch, handler)
    out = str(asyncio.run(invoke_mcp_tool_fn(fn, payload={"url": "http://93.184.216.34/start"})))

    assert "private/internal" in out, out
    assert "cloud credentials" not in out
    # Hop 1 was fetched; hop 2 was refused before any request went out.
    assert seen == ["http://93.184.216.34/start"], seen


def test_web_fetch_still_follows_safe_redirects(monkeypatch):
    """The guard must not break ordinary redirects."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"Location": "http://93.184.216.34/final"})
        return httpx.Response(200, text="hello world", headers={"content-type": "text/plain"})

    fn = _patch_web_fetch_transport(monkeypatch, handler)
    out = str(asyncio.run(invoke_mcp_tool_fn(fn, payload={"url": "http://93.184.216.34/start"})))
    assert "hello world" in out


# ── H2: the login limiter must not grow without bound ────────────────────────

def test_rate_limiter_state_is_bounded_under_forged_client_keys():
    """uvicorn derives the client host from X-Forwarded-For, which a caller can
    forge per request. The lockout is only a speed bump there, but the state must
    never become a memory-exhaustion vector."""
    from core.rate_limit import LoginRateLimiter

    lim = LoginRateLimiter(max_attempts=8, window_s=900.0, lock_s=900.0, max_keys=64)
    now = 1000.0
    for i in range(5000):
        lim.record_failure(f"10.0.{i // 256}.{i % 256}", now)

    assert len(lim._fails) <= 64
    assert len(lim._locked_until) <= 64


def test_rate_limiter_still_locks_a_repeat_offender():
    from core.rate_limit import LoginRateLimiter

    lim = LoginRateLimiter(max_attempts=3, window_s=900.0, lock_s=60.0)
    now = 1000.0
    assert lim.record_failure("1.2.3.4", now) == 0.0
    assert lim.record_failure("1.2.3.4", now + 1) == 0.0
    assert lim.record_failure("1.2.3.4", now + 2) == 60.0
    assert lim.locked_for("1.2.3.4", now + 3) > 0


# ── H3: the cost cap must actually stop a run ────────────────────────────────

class _FakeProc:
    """Minimal stand-in for the `claude` subprocess."""

    def __init__(self, lines):
        self._lines = list(lines)
        self.returncode = 0
        self.killed = False
        self.stdout = self
        self.stderr = None

    def __iter__(self):
        for ln in self._lines:
            if self.killed:
                return
            yield ln

    def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def _result_event(cost):
    return json.dumps({"type": "result", "subtype": "success", "total_cost_usd": cost,
                       "num_turns": 3, "result": "done"}) + "\n"


def test_cost_cap_kills_the_run(tmp_path, monkeypatch):
    """max_cost_usd used to be checked in the finally block — after the money was
    already spent. It must terminate the process instead."""
    from core import agent_runner as AR

    monkeypatch.setattr(AR, "load_agent_config", lambda root: {
        **AR.DEFAULT_AGENT_CONFIG, "max_cost_usd": 1.0, "give_plutus_tools": False,
    })
    proc = _FakeProc([_result_event(5.0), _result_event(9.0)])
    monkeypatch.setattr(AR.subprocess, "Popen", lambda *a, **k: proc)

    rec = AR.run_agent(tmp_path, "do something", label="test")

    assert proc.killed, "run was allowed to continue past the cost cap"
    assert rec["over_budget"] is True
    assert rec["ok"] is False
    assert "cap" in (rec["error"] or "").lower()


def test_run_under_the_cap_is_untouched(tmp_path, monkeypatch):
    from core import agent_runner as AR

    monkeypatch.setattr(AR, "load_agent_config", lambda root: {
        **AR.DEFAULT_AGENT_CONFIG, "max_cost_usd": 10.0, "give_plutus_tools": False,
    })
    proc = _FakeProc([_result_event(0.25)])
    monkeypatch.setattr(AR.subprocess, "Popen", lambda *a, **k: proc)

    rec = AR.run_agent(tmp_path, "do something", label="test")
    assert not proc.killed
    assert rec["over_budget"] is False
    assert rec["ok"] is True
    assert rec["cost_usd"] == 0.25


# ── the prompt must survive Claude Code's variadic options ───────────────────

def test_prompt_is_not_swallowed_by_variadic_options():
    """`--mcp-config <configs...>`, `--allowedTools <tools...>` and
    `--disallowedTools <tools...>` are variadic, so a bare positional prompt right
    after one of them is consumed as another value:

        Error: Invalid MCP configuration:
        MCP config file not found: /app/<the prompt text>

    The run then had no prompt and exited 1. `--` must end option parsing.
    """
    from core.agent_runner import build_agent_cmd

    cmd = build_agent_cmd(
        "research the thing",
        {"skip_permissions": True, "allowed_tools": ["mcp__plutus", "Read"]},
        mcp_config_path="/app/data/agent_mcp.json",
        disallowed_tools=["mcp__plutus__docker_restart"],
    )

    assert cmd[-1] == "research the thing"
    assert cmd[-2] == "--", f"prompt must be guarded by an end-of-options marker: {cmd[-3:]}"
    # And the marker must come after every variadic option, not before them.
    assert cmd.index("--") > cmd.index("--mcp-config")


def test_prompt_guard_holds_without_optional_flags():
    """The bug only bit when no model was selected — with --model present the
    prompt happened to land after a non-variadic option. Both shapes must work."""
    from core.agent_runner import build_agent_cmd

    bare = build_agent_cmd("do it", {"skip_permissions": False, "allowed_tools": []})
    assert bare[-2:] == ["--", "do it"]

    with_model = build_agent_cmd("do it", {"allowed_tools": []}, model="haiku")
    assert with_model[-2:] == ["--", "do it"]
    assert "--model" in with_model


# ── H5: stderr must not be left undrained ────────────────────────────────────

def test_agent_stderr_is_merged_into_stdout(tmp_path, monkeypatch):
    """stderr on its own pipe, drained only after wait(), deadlocks as soon as the
    child writes past the ~64KB pipe buffer."""
    import subprocess as SP

    from core import agent_runner as AR

    seen = {}

    def fake_popen(cmd, **kwargs):
        seen.update(kwargs)
        return _FakeProc([_result_event(0.1)])

    monkeypatch.setattr(AR, "load_agent_config", lambda root: {
        **AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": False,
    })
    monkeypatch.setattr(AR.subprocess, "Popen", fake_popen)
    AR.run_agent(tmp_path, "hi", label="test")

    assert seen.get("stderr") is SP.STDOUT, "stderr must be merged, not left on its own pipe"


# ── H4: a credential must be removable ───────────────────────────────────────

def test_empty_value_clears_the_key(tmp_path):
    """Saving a blank value used to be silently skipped, so a leaked API key could
    be set from the UI but never revoked."""
    from core.env_store import read_env, update_env

    p = tmp_path / ".env"
    p.write_text("JELLYFIN_API_KEY=supersecret\nJELLYFIN_URL=http://x\n", encoding="utf-8")

    update_env({"JELLYFIN_API_KEY": ""}, path=p)

    env = read_env(p)
    assert "JELLYFIN_API_KEY" not in env
    assert env["JELLYFIN_URL"] == "http://x"      # untouched keys survive


def test_omitted_key_is_left_alone(tmp_path):
    """None / absent still means 'keep current' — only an explicit "" clears."""
    from core.env_store import read_env, update_env

    p = tmp_path / ".env"
    p.write_text("JELLYFIN_API_KEY=supersecret\n", encoding="utf-8")
    update_env({"JELLYFIN_API_KEY": None, "JELLYFIN_URL": "http://y"}, path=p)
    assert read_env(p)["JELLYFIN_API_KEY"] == "supersecret"


def test_clearing_also_drops_it_from_the_live_process(tmp_path, monkeypatch):
    """Rewriting .env is only half a revocation — the running process holds the
    value in cfg and os.environ."""
    from config import apply_live_env, cfg

    monkeypatch.setattr(cfg, "jellyfin_api_key", "supersecret", raising=False)
    monkeypatch.setenv("JELLYFIN_API_KEY", "supersecret")

    apply_live_env({"JELLYFIN_API_KEY": ""})

    assert cfg.jellyfin_api_key == ""
    assert "JELLYFIN_API_KEY" not in os.environ


def test_env_parser_strips_quotes_and_export(tmp_path):
    """A quoted token round-tripped with its quotes attached and then failed every
    comparison that used it (e.g. the MCP bearer check)."""
    from core.env_store import read_env

    p = tmp_path / ".env"
    p.write_text('MCP_BEARER_TOKEN="abc123"\nexport OTHER_KEY=plain\n', encoding="utf-8")
    env = read_env(p)
    assert env["MCP_BEARER_TOKEN"] == "abc123"
    assert env["OTHER_KEY"] == "plain"


# ── M1: refresh tokens are single use ────────────────────────────────────────

def test_refresh_token_is_single_use(tmp_path):
    """OAuth 2.1 requires rotation for public clients: leaving the presented token
    valid made a stolen one replayable for its full 180-day TTL."""
    from core import oauth_provider as op

    client = op.register_client(tmp_path, {"redirect_uris": ["https://example.com/cb"]})
    verifier = "v" * 64
    challenge = op._b64url(__import__("hashlib").sha256(verifier.encode()).digest())
    code = op.issue_code(tmp_path, client_id=client["client_id"],
                         redirect_uri="https://example.com/cb", code_challenge=challenge)
    tokens = op.exchange_code(tmp_path, code=code, code_verifier=verifier,
                              client_id=client["client_id"], redirect_uri="https://example.com/cb")

    rotated = op.refresh_token(tmp_path, refresh=tokens["refresh_token"], client_id=client["client_id"])
    assert rotated["refresh_token"] != tokens["refresh_token"]

    with pytest.raises(ValueError, match="invalid_grant"):
        op.refresh_token(tmp_path, refresh=tokens["refresh_token"], client_id=client["client_id"])


def test_revoke_token_kills_an_access_token(tmp_path):
    from core import oauth_provider as op

    client = op.register_client(tmp_path, {"redirect_uris": ["https://example.com/cb"]})
    verifier = "v" * 64
    challenge = op._b64url(__import__("hashlib").sha256(verifier.encode()).digest())
    code = op.issue_code(tmp_path, client_id=client["client_id"],
                         redirect_uri="https://example.com/cb", code_challenge=challenge)
    tokens = op.exchange_code(tmp_path, code=code, code_verifier=verifier,
                              client_id=client["client_id"], redirect_uri="https://example.com/cb")

    assert op.validate_access_token(tmp_path, tokens["access_token"]) is True
    assert op.revoke_token(tmp_path, tokens["access_token"]) is True
    assert op.validate_access_token(tmp_path, tokens["access_token"]) is False


# ── M5: hot paths must not read every run file ───────────────────────────────

def test_total_cost_does_not_parse_every_run(tmp_path, monkeypatch):
    """total_cost() sits inside status(), which the dashboard polls."""
    from core import agent_runner as AR

    for i in range(25):
        AR.save_run(tmp_path, {"id": f"20260101-0000{i:02d}", "cost_usd": 0.10, "ok": True})

    reads = {"n": 0}
    real_read = AR.Path.read_text

    def counting_read(self, *a, **k):
        if self.parent.name == "agent_runs":
            reads["n"] += 1
        return real_read(self, *a, **k)

    monkeypatch.setattr(AR.Path, "read_text", counting_read)
    assert AR.total_cost(tmp_path) == 2.5
    assert reads["n"] == 0, f"total_cost parsed {reads['n']} run files instead of using the index"


def test_run_index_rebuilds_when_files_are_deleted_out_of_band(tmp_path):
    from core import agent_runner as AR

    for i in range(4):
        AR.save_run(tmp_path, {"id": f"20260101-0000{i:02d}", "cost_usd": 1.0, "ok": True})
    assert AR.total_cost(tmp_path) == 4.0

    next(iter((tmp_path / "data" / "agent_runs").glob("*.json"))).unlink()
    assert AR.total_cost(tmp_path) == 3.0


def test_get_run_reads_a_single_file(tmp_path):
    from core import agent_runner as AR

    AR.save_run(tmp_path, {"id": "20260101-000001", "cost_usd": 0.5, "ok": True, "label": "x"})
    assert AR.get_run(tmp_path, "20260101-000001")["label"] == "x"
    assert AR.get_run(tmp_path, "nope") is None
    assert AR.get_run(tmp_path, "../../etc/passwd") is None


# ── M6: CSRF check must consider the port ────────────────────────────────────

def test_csrf_rejects_same_host_different_port():
    """A homelab runs a dozen services on one IP; hostname-only comparison treats
    every one of them as same-origin."""
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from ui.api.deps import csrf_origin_guard

    app = Starlette(routes=[Route("/x", lambda r: JSONResponse({"ok": True}), methods=["POST"])])
    app.add_middleware(BaseHTTPMiddleware, dispatch=csrf_origin_guard)
    c = TestClient(app, base_url="http://192.168.1.111:8766")

    same = c.post("/x", headers={"Origin": "http://192.168.1.111:8766"})
    assert same.status_code == 200

    other = c.post("/x", headers={"Origin": "http://192.168.1.111:9000"})
    assert other.status_code == 403


# ── L6: redaction must not mangle the file ───────────────────────────────────

def test_redact_preserves_trailing_newline_and_ordinary_keys():
    from core.redact import redact_secrets

    out, n = redact_secrets("api_key = abc123\nauthor: Jane\n")
    assert out.endswith("\n")
    assert "***REDACTED***" in out
    assert "Jane" in out, "an ordinary 'author:' key must not be masked"
    assert n == 1

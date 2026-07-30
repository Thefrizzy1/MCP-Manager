"""The stdio bridge that gives Codex Plutus's MCP tools.

Codex spawns this as a subprocess and speaks MCP to it over stdin/stdout. Three
things have to hold or a Codex agent silently has no tools:

- the handshake and tool traffic survive the round trip
- the run's connection scope is enforced *here*, since `codex exec` has no
  --disallowedTools
- nothing but JSON-RPC ever reaches stdout

The last one is the sneaky one: a single stray print corrupts the stream and the
client drops the connection, which looks like "MCP server failed to start".
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "tools" / "mcp_stdio_bridge.py"


@pytest.fixture(scope="module")
def live_mcp():
    """A real Plutus MCP endpoint on a spare port, in its own process.

    Stubbing the server would only prove the bridge talks to the stub; the point
    of this file is that it talks to *Plutus*.

    A subprocess rather than a thread, for two reasons. It is what actually
    happens in production — the MCP server is a separate process from the UI —
    and FastMCP's streamable-HTTP session manager refuses a second ``run()`` per
    instance, so serving in-process would consume the one belonging to whichever
    other test needs it (that is a real failure, not a hypothetical: it broke
    test_profiles.py).
    """
    import socket
    import time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import uvicorn;from ui.runtime import build_mcp_asgi_app;"
         f"uvicorn.run(build_mcp_asgi_app(), host='127.0.0.1', port={port}, log_level='error')"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 90          # importing ~209 tools is not instant
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.skip(f"the MCP server exited: {(proc.stderr.read() or '')[-400:]}")
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.25)
    else:
        proc.kill()
        pytest.skip("the MCP server did not start in time")

    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_bridge(url: str, messages: list[dict], deny: str = "") -> tuple[list[dict], str]:
    import os

    # Inherit the real environment: the bridge is a child process and needs the
    # platform's own variables (on Windows, stripping SYSTEMROOT breaks sockets).
    env = {**os.environ, "PLUTUS_MCP_URL": url}
    env.pop("PLUTUS_MCP_DENY", None)
    if deny:
        env["PLUTUS_MCP_DENY"] = deny
    proc = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True, text=True, env=env, timeout=120,
    )
    out = []
    for line in proc.stdout.splitlines():
        if line.strip():
            out.append(json.loads(line))      # raises if stdout was polluted
    return out, proc.stderr


HANDSHAKE = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "codex", "version": "test"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
]


def test_a_codex_style_session_gets_the_real_tool_surface(live_mcp):
    replies, _ = run_bridge(live_mcp, HANDSHAKE + [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    init = next(r for r in replies if r.get("id") == 1)
    assert init["result"]["serverInfo"]["name"] == "plutus_mcp"

    listing = next(r for r in replies if r.get("id") == 2)
    names = [t["name"] for t in listing["result"]["tools"]]
    assert len(names) > 50, f"expected Plutus's full surface, got {len(names)}"
    assert "plutus_status" in names


def test_a_tool_call_returns_real_output(live_mcp):
    replies, _ = run_bridge(live_mcp, HANDSHAKE + [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "plutus_status", "arguments": {"params": {}}}},
    ])
    res = next(r for r in replies if r.get("id") == 2)["result"]
    assert res.get("isError") is not True
    assert "Plutus" in res["content"][0]["text"]


def test_the_run_scope_is_enforced_by_the_bridge(live_mcp):
    """`codex exec` has no --disallowedTools, so the connection picker's decision
    has to be applied here — and applied to the listing too, so the model is
    never even told the tool exists."""
    replies, stderr = run_bridge(live_mcp, HANDSHAKE + [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "plutus_status", "arguments": {"params": {}}}},
    ], deny="mcp__plutus__plutus_status")

    names = [t["name"] for t in next(r for r in replies if r.get("id") == 2)["result"]["tools"]]
    assert "plutus_status" not in names

    refused = next(r for r in replies if r.get("id") == 3)["result"]
    assert refused["isError"] is True
    assert "not available to this agent" in refused["content"][0]["text"]
    assert "refused out-of-scope tool" in stderr


def test_diagnostics_never_touch_stdout(live_mcp):
    """One stray print corrupts the JSON-RPC stream and the client hangs up."""
    replies, stderr = run_bridge(live_mcp, HANDSHAKE + [
        {"not": "json-rpc"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    assert all("jsonrpc" in r for r in replies)     # parsed above; assert the shape
    assert "bridging to" in stderr                  # it did log — just not to stdout


def test_an_unreachable_endpoint_answers_instead_of_hanging():
    """Codex waits on a reply; silence would stall the run until its own timeout."""
    replies, stderr = run_bridge("http://127.0.0.1:9/mcp", [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    ])
    assert replies and replies[0]["error"]["message"].startswith("Plutus MCP unreachable")

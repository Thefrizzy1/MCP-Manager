"""comfy-mcp through Plutus: the stdio client and the proxy tools, offline.

A twenty-line fake MCP server stands in for comfy-mcp, so these run without
comfy-cli, ComfyUI or a GPU — and the protocol edges (a server asking for
consent, a server dying mid-call, overlapping calls) are ones a real comfy-mcp
cannot be made to hit on demand.
"""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap
import threading

import pytest

from config import cfg
from core import mcp_stdio as MS
from tools import comfy_mcp as CM

FAKE = textwrap.dedent('''
    import json, sys, threading, time
    LOCK = threading.Lock()
    TOOLS = [
        {"name": "server_info", "description": "Report the environment.\\n\\nLong guide text.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "echo", "description": "Echo arguments back.",
         "inputSchema": {"type": "object", "properties": {"x": {"$ref": "#/$defs/X"}},
                         "required": ["x"], "$defs": {"X": {"type": "integer"}}}},
        {"name": "install_node", "description": "Install a pack — asks first.",
         "inputSchema": {"type": "object", "properties": {"pack": {"type": "string"}}}},
        {"name": "slow", "description": "Sleeps.", "inputSchema": {"type": "object"}},
        {"name": "die", "description": "Exits.", "inputSchema": {"type": "object"}},
    ]
    def send(m):
        with LOCK:
            sys.stdout.write(json.dumps(m) + "\\n"); sys.stdout.flush()
    def slow(mid, secs):
        time.sleep(secs)
        send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "slept"}]}})
    for line in sys.stdin:
        m = json.loads(line)
        mid, method = m.get("id"), m.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2025-06-18",
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "9.9"},
                  "instructions": "Call server_info first."}})
        elif method == "tools/list":
            print("not json", flush=True)          # stray output must not break the stream
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name, args = m["params"]["name"], m["params"]["arguments"]
            if name == "echo":
                send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "hi"}})
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(args)}]}})
            elif name == "install_node":
                send({"jsonrpc": "2.0", "id": "ask-1", "method": "elicitation/create", "params": {"message": "ok?"}})
                answer = json.loads(sys.stdin.readline())
                action = answer.get("result", {}).get("action")
                send({"jsonrpc": "2.0", "id": mid, "result": {"isError": action != "accept",
                      "content": [{"type": "text", "text": "consent " + str(action)}]}})
            elif name == "slow":
                threading.Thread(target=slow, args=(mid, float(args.get("s", 0.5)))).start()
            elif name == "die":
                sys.exit(3)
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": {"structuredContent": {"ok": True}, "content": []}})
''')


@pytest.fixture
def fake_cmd(tmp_path):
    p = tmp_path / "fake_mcp.py"
    p.write_text(FAKE, encoding="utf-8")
    return [sys.executable, str(p)]


@pytest.fixture
def client(fake_cmd):
    c = MS.StdioMcpClient(fake_cmd, env=MS.base_env())
    yield c
    c.close()


# ── the stdio client ─────────────────────────────────────────────────────────

def test_handshake_list_and_call(client):
    names = [t["name"] for t in client.list_tools()]
    assert names[:2] == ["server_info", "echo"]
    assert client.server_info["version"] == "9.9" and client.instructions == "Call server_info first."
    assert MS.result_text(client.call_tool("echo", {"x": 5})) == '{"x": 5}'


def test_a_consent_prompt_is_declined_so_the_server_fails_closed(client):
    r = client.call_tool("install_node", {"pack": "evil"})
    assert r["isError"] is True and "decline" in MS.result_text(r)


def test_calls_overlap_instead_of_queueing(client):
    client.ensure_started()
    out = {}

    def slow():
        out["slow"] = MS.result_text(client.call_tool("slow", {"s": 1.5}))
    t = threading.Thread(target=slow)
    t.start()
    import time
    start = time.monotonic()
    assert MS.result_text(client.call_tool("echo", {"x": 1})) == '{"x": 1}'
    assert time.monotonic() - start < 1.0          # did not wait behind the slow call
    t.join(5)
    assert out["slow"] == "slept"


def test_a_server_that_dies_mid_call_is_an_error_not_a_hang(client):
    with pytest.raises(MS.McpStdioError, match="exited"):
        client.call_tool("die", {}, timeout=10)
    assert not client.alive
    assert MS.result_text(client.call_tool("echo", {"x": 2})) == '{"x": 2}'   # restarts


def test_a_missing_command_is_a_clear_error():
    c = MS.StdioMcpClient(["definitely-not-a-real-binary-xyz"])
    with pytest.raises(MS.McpStdioError, match="could not start"):
        c.list_tools()


def test_result_text_shapes():
    assert MS.result_text({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "a\nb"
    assert MS.result_text({"isError": True, "content": [{"type": "text", "text": "boom"}]}) == "Error: boom"
    assert "[image: image/png" in MS.result_text({"content": [{"type": "image", "mimeType": "image/png", "data": "AAAA"}]})
    assert json.loads(MS.result_text({"content": [], "structuredContent": {"k": 1}})) == {"k": 1}
    assert MS.result_text({"content": [{"type": "text", "text": "x" * 50}]}, limit=10).startswith("x" * 10 + "\n\n[…")


# ── the proxy ────────────────────────────────────────────────────────────────

@pytest.fixture
def comfy(tmp_path, fake_cmd, monkeypatch):
    monkeypatch.setattr(CM, "_ROOT", tmp_path)
    monkeypatch.setattr(CM, "command", lambda: list(fake_cmd))
    monkeypatch.setattr(cfg, "comfyui_url", "", raising=False)
    CM._client = None
    CM._client_sig = ()
    yield
    if CM._client:
        CM._client.close()
    CM._client = None


def _tools(allow=None):
    from mcp.server.fastmcp import FastMCP
    m = FastMCP("t")
    CM.register_comfy_mcp_tools(m, allow=allow)
    return {t.name: t for t in m._tool_manager.list_tools()}


def _run(tool, payload):
    from core.invoke_tool import invoke_mcp_tool_fn
    return str(asyncio.run(invoke_mcp_tool_fn(tool.fn, payload=payload)))


def test_every_comfy_mcp_tool_is_served_with_its_schema_and_a_short_description(comfy):
    tools = _tools()
    assert set(tools) == {"comfy_mcp_status", "comfy_server_info", "comfy_echo",
                          "comfy_install_node", "comfy_slow", "comfy_die"}
    echo = tools["comfy_echo"]
    assert echo.parameters["properties"]["params"]["properties"]["x"] == {"$ref": "#/$defs/X"}
    assert echo.parameters["$defs"] == {"X": {"type": "integer"}}          # refs still resolve
    assert echo.parameters["required"] == ["params"]
    assert "Long guide text" not in tools["comfy_server_info"].description
    assert "comfy_mcp_status(tool='server_info')" in tools["comfy_server_info"].description


def test_read_write_and_destructive_are_classified(comfy):
    tools = _tools()
    assert tools["comfy_server_info"].annotations.readOnlyHint is True
    assert tools["comfy_install_node"].annotations.destructiveHint is True
    # comfy-mcp adds a tool Plutus has never seen: a destructive write until classified.
    assert tools["comfy_echo"].annotations.readOnlyHint is False
    assert tools["comfy_echo"].annotations.destructiveHint is True


def test_calls_pass_arguments_through_and_back(comfy):
    assert _run(_tools()["comfy_echo"], {"x": 7}) == '{"x": 7}'


def test_profiles_only_get_the_tools_they_allow(comfy):
    assert set(_tools(allow={"comfy_echo"})) == {"comfy_echo"}


def test_status_gives_the_rules_and_full_guides(comfy):
    tools = _tools()
    out = _run(tools["comfy_mcp_status"], {})
    assert "comfy-mcp 9.9" in out and "Call server_info first." in out and "`comfy_echo`" in out
    guide = _run(tools["comfy_mcp_status"], {"tool": "comfy_server_info"})
    assert "Long guide text" in guide


def test_the_tool_list_is_cached_so_a_restart_does_not_spawn(comfy, monkeypatch):
    _tools()

    def boom():
        raise AssertionError("spawned comfy-mcp although the list was cached")
    monkeypatch.setattr(CM, "client", boom)
    assert "comfy_echo" in _tools()


def test_without_comfy_mcp_only_the_status_tool_exists(tmp_path, monkeypatch):
    from core.tool_registry import looks_like_missing_service_config
    monkeypatch.setattr(CM, "_ROOT", tmp_path)
    monkeypatch.setattr(CM, "command", lambda: [])
    tools = _tools()
    assert set(tools) == {"comfy_mcp_status"}
    out = _run(tools["comfy_mcp_status"], {})
    assert looks_like_missing_service_config(out)


@pytest.mark.parametrize("url,expect", [
    ("", {}),
    ("http://127.0.0.1:8189", {"COMFY_LOCAL_URL": "http://127.0.0.1:8189"}),
    ("http://localhost:8188/", {"COMFY_LOCAL_URL": "http://localhost:8188"}),
    ("http://192.168.1.50:8188/comfy?x=1", {"COMFYUI_URL": "http://192.168.1.50:8188"}),
    ("gpu-box", {"COMFYUI_URL": "http://gpu-box:8188"}),
])
def test_the_comfyui_address_becomes_the_right_comfy_mcp_variable(tmp_path, monkeypatch, url, expect):
    """Loopback is this machine (comfy-cli's COMFY_LOCAL_URL); anything else is a
    remote box (comfy-mcp's COMFYUI_URL). comfy-mcp refuses paths and queries, so
    only scheme://host:port is passed on."""
    monkeypatch.setattr(CM, "_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "comfyui_url", url, raising=False)
    env = CM.target_env()
    got = {k: env[k] for k in ("COMFY_LOCAL_URL", "COMFYUI_URL") if k in env}
    assert got == expect
    assert env["COMFY_PROJECT"].endswith("project")


def test_dangerous_comfy_tools_are_in_the_blast_radius_set():
    from core.agent_permissions import DANGEROUS
    for n in ("install_node", "switch_comfyui_version", "partner_generate", "stop_comfyui"):
        assert CM.PREFIX + n in DANGEROUS
        assert n in CM.DESTRUCTIVE


def test_the_child_gets_no_plutus_secrets_and_only_the_derived_address(tmp_path, monkeypatch):
    """.env is loaded into Plutus's environment, so a naive child env would hand
    comfy-mcp every service credential — and Plutus's own COMFYUI_URL, which
    comfy-mcp reads as "a remote box" even when it is this machine."""
    monkeypatch.setattr(CM, "_ROOT", tmp_path)
    monkeypatch.setattr(CM, "command", lambda: [sys.executable, "-c", "pass"])
    monkeypatch.setattr(cfg, "comfyui_url", "http://127.0.0.1:8188", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("COMFY_MCP_ASSUME_CONSENT", "install_node")
    CM._client = None
    CM._client_sig = ()
    env = CM.client().env
    CM._client = None
    assert "GITHUB_TOKEN" not in env
    assert "COMFYUI_URL" not in env and env["COMFY_LOCAL_URL"] == "http://127.0.0.1:8188"
    assert env["COMFY_MCP_ASSUME_CONSENT"] == "install_node"
    assert "PATH" in {k.upper() for k in env}

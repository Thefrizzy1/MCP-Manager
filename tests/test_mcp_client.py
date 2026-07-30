"""The MCP client Plutus's own agents use to reach Plutus's tools.

Streamable HTTP has two shapes for the same answer — a JSON body, or an SSE
stream — and a session id that must be echoed after initialize. Getting any of
that wrong does not fail loudly; it fails as "the agent has no tools", which is
indistinguishable from the tools simply not working.
"""
from __future__ import annotations

import json

import pytest

from core import mcp_client as MC


class FakeResponse:
    def __init__(self, status=200, body="", headers=None, content_type="application/json"):
        self.status_code = status
        self.text = body
        self.content = body.encode()
        self.headers = {"content-type": content_type, **(headers or {})}

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    """Records posts and replays scripted responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posts: list[dict] = []
        self.closed = False

    def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "body": json, "headers": headers or {}})
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def client_with(responses, monkeypatch) -> tuple[MC.McpHttpClient, FakeHttp]:
    http = FakeHttp(responses)
    c = MC.McpHttpClient("http://plutus/mcp", "tok")
    monkeypatch.setattr(c, "_http", http)
    return c, http


def _json_rpc(rid, result, headers=None):
    return FakeResponse(200, json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}),
                        headers)


def _sse(rid, result, headers=None):
    body = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': result})}\n\n"
    return FakeResponse(200, body, headers, content_type="text/event-stream")


# ── transport ────────────────────────────────────────────────────────────────

def test_an_sse_response_is_parsed(monkeypatch):
    """The MCP SDK answers with an event stream by default, not a JSON body."""
    c, _ = client_with([_sse(1, {"protocolVersion": "2025-06-18"}),
                        FakeResponse(202, "")], monkeypatch)
    assert c.initialize()["protocolVersion"] == "2025-06-18"


def test_a_plain_json_response_is_parsed(monkeypatch):
    c, _ = client_with([_json_rpc(1, {"protocolVersion": "x"}),
                        FakeResponse(202, "")], monkeypatch)
    assert c.initialize()["protocolVersion"] == "x"


def test_a_multi_line_sse_payload_is_reassembled():
    body = 'data: {"jsonrpc":"2.0",\ndata: "id":7,"result":{"ok":true}}\n\n'
    assert list(MC._sse_messages(body)) == [{"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}]


def test_the_session_id_is_echoed_after_initialize(monkeypatch):
    """Without this the server treats every later request as a new, uninitialised
    session and refuses it."""
    c, http = client_with([
        _sse(1, {"protocolVersion": "x"}, {"mcp-session-id": "sess-42"}),
        FakeResponse(202, ""),                       # notifications/initialized
        _sse(2, {"tools": []}),
    ], monkeypatch)
    c.initialize()
    c.list_tools()
    assert http.posts[1]["headers"]["Mcp-Session-Id"] == "sess-42"
    assert http.posts[2]["headers"]["Mcp-Session-Id"] == "sess-42"
    assert http.posts[2]["headers"]["MCP-Protocol-Version"] == MC.PROTOCOL_VERSION


def test_the_bearer_token_is_sent():
    c = MC.McpHttpClient("http://plutus/mcp", "secret")
    assert c._http.headers["Authorization"] == "Bearer secret"
    c.close()


def test_an_accepted_notification_produces_no_reply(monkeypatch):
    c, _ = client_with([FakeResponse(202, "")], monkeypatch)
    assert c.send({"jsonrpc": "2.0", "method": "notifications/initialized"}) == []


def test_an_http_error_is_raised_not_swallowed(monkeypatch):
    c, _ = client_with([FakeResponse(401, "no token for you")], monkeypatch)
    with pytest.raises(MC.McpError, match="401"):
        c.initialize()


# ── protocol ─────────────────────────────────────────────────────────────────

def test_tools_list_follows_pagination(monkeypatch):
    c, _ = client_with([
        _sse(1, {}), FakeResponse(202, ""),
        _sse(2, {"tools": [{"name": "a"}], "nextCursor": "c1"}),
        _sse(3, {"tools": [{"name": "b"}]}),
    ], monkeypatch)
    assert [t["name"] for t in c.list_tools()] == ["a", "b"]


def test_a_tool_error_is_returned_as_data(monkeypatch):
    """A tool that fails is something the model should read and work around; an
    exception here would end the whole run instead."""
    c, _ = client_with([
        _sse(1, {}), FakeResponse(202, ""),
        FakeResponse(200, json.dumps({"jsonrpc": "2.0", "id": 2,
                                      "error": {"code": -32602, "message": "bad args"}})),
    ], monkeypatch)
    res = c.call_tool("x", {})
    assert res["is_error"] is True and "bad args" in res["text"]


def test_tool_result_content_blocks_are_flattened(monkeypatch):
    c, _ = client_with([
        _sse(1, {}), FakeResponse(202, ""),
        _sse(2, {"content": [{"type": "text", "text": "line one"},
                             {"type": "text", "text": "line two"}]}),
    ], monkeypatch)
    assert c.call_tool("x", {})["text"] == "line one\nline two"


def test_a_huge_tool_result_is_clipped_before_it_reaches_a_model(monkeypatch):
    big = "x" * (MC.MAX_RESULT_CHARS + 5000)
    c, _ = client_with([
        _sse(1, {}), FakeResponse(202, ""),
        _sse(2, {"content": [{"type": "text", "text": big}]}),
    ], monkeypatch)
    text = c.call_tool("x", {})["text"]
    assert len(text) < MC.MAX_RESULT_CHARS + 200 and "more chars" in text


def test_a_missing_response_is_an_error_not_a_hang(monkeypatch):
    c, _ = client_with([_sse(99, {"nope": True})], monkeypatch)   # wrong id
    with pytest.raises(MC.McpError, match="no response"):
        c.initialize()

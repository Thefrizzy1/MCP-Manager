"""Connection Manager export builder — output shape, JSON validity, token gating."""
import json

import pytest

from core.mcp_export import build_connection_exports


def _build(token="", is_http=True):
    return build_connection_exports(
        mcp_url="http://192.168.1.111:8765/mcp",
        sse_url="http://192.168.1.111:8765/sse",
        is_http=is_http,
        token=token,
    )


def test_all_clients_present():
    d = _build()
    ids = {c["id"] for c in d["clients"]}
    expected = {
        "claude_desktop", "claude_code", "cursor", "vscode", "cline",
        "windsurf", "openai", "lmstudio", "openwebui", "n8n", "generic",
    }
    assert expected <= ids


def test_every_client_has_required_fields():
    d = _build()
    for c in d["clients"]:
        for field in ("id", "label", "download_name", "content", "instructions", "mime", "format"):
            assert c.get(field), f"{c['id']} missing {field}"


@pytest.mark.parametrize("token", ["", "secrettoken123"])
def test_json_clients_parse(token):
    d = _build(token=token)
    for c in d["clients"]:
        if c["format"] == "json":
            json.loads(c["content"])  # raises on invalid JSON


def test_token_embedded_only_when_present():
    with_token = _build(token="SECRET_ABC")
    without = _build(token="")
    assert any("SECRET_ABC" in c["content"] for c in with_token["clients"])
    assert all("SECRET_ABC" not in c["content"] for c in without["clients"])
    assert with_token["has_token"] is True
    assert without["has_token"] is False


def test_allow_http_flag_only_for_http():
    http = _build(is_http=True)
    cd_http = next(c for c in http["clients"] if c["id"] == "claude_desktop")
    assert "--allow-http" in cd_http["content"]

    https = build_connection_exports(
        mcp_url="https://x.ts.net/mcp", sse_url="https://x.ts.net/sse",
        is_http=False, token="",
    )
    cd_https = next(c for c in https["clients"] if c["id"] == "claude_desktop")
    assert "--allow-http" not in cd_https["content"]

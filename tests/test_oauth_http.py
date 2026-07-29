"""OAuth endpoints over HTTP (Starlette TestClient) + bearer-gate token acceptance.

Drives the real routes end to end: discovery -> dynamic client registration ->
authorize (login) -> code -> token, and confirms the MCP gate accepts an
OAuth-issued access token.
"""
import base64
import hashlib

from starlette.applications import Starlette
from starlette.testclient import TestClient

from core import mcp_bearer_middleware as gate
from core import oauth_routes as orr


def _pkce():
    v = "verifier-abcdefghijklmnopqrstuvwxyz-0123456789"
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(orr, "ROOT", tmp_path)
    monkeypatch.setattr(orr, "read_env", lambda: {"UI_USERNAME": "admin", "UI_PASSWORD": "secret"})
    return TestClient(Starlette(routes=orr.oauth_routes()))


REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def test_discovery_documents(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    prm = c.get("/.well-known/oauth-protected-resource")
    assert prm.status_code == 200 and prm.json()["authorization_servers"]
    asm = c.get("/.well-known/oauth-authorization-server").json()
    assert asm["authorization_endpoint"].endswith("/authorize")
    assert asm["token_endpoint"].endswith("/token")
    assert asm["code_challenge_methods_supported"] == ["S256"]


def test_full_browser_flow(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    v, chal = _pkce()

    reg = c.post("/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT]})
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]

    q = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
         "code_challenge": chal, "code_challenge_method": "S256", "state": "xyz", "scope": "mcp"}
    page = c.get("/authorize", params=q)
    assert page.status_code == 200 and "Connect to Plutus" in page.text

    # Wrong password re-renders the form, does NOT redirect.
    bad = c.post("/authorize", data={**q, "username": "admin", "password": "nope"}, follow_redirects=False)
    assert bad.status_code == 200 and "Incorrect username or password" in bad.text

    # Correct login -> 302 back to the client with a code + preserved state.
    ok = c.post("/authorize", data={**q, "username": "admin", "password": "secret"}, follow_redirects=False)
    assert ok.status_code == 302
    loc = ok.headers["location"]
    assert loc.startswith(REDIRECT) and "state=xyz" in loc
    code = loc.split("code=")[1].split("&")[0]

    tok = c.post("/token", data={"grant_type": "authorization_code", "code": code,
                                 "code_verifier": v, "client_id": client_id, "redirect_uri": REDIRECT})
    assert tok.status_code == 200
    access = tok.json()["access_token"]
    assert access.startswith("mcp_at_")

    # The MCP gate accepts this OAuth token when oauth mode is on.
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    assert gate._token_ok(access, expected="", oauth=True) is True
    assert gate._token_ok("garbage", expected="", oauth=True) is False


def test_authorize_rejects_unregistered_redirect(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    reg = c.post("/register", json={"client_name": "X", "redirect_uris": [REDIRECT]})
    client_id = reg.json()["client_id"]
    r = c.get("/authorize", params={"response_type": "code", "client_id": client_id,
                                    "redirect_uri": "https://evil.example/cb", "code_challenge": "x"})
    assert r.status_code == 400  # never redirect to an unregistered URI


def test_token_rejects_bad_grant(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/token", data={"grant_type": "authorization_code", "code": "nope",
                               "code_verifier": "x", "client_id": "y", "redirect_uri": REDIRECT})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_oauth_paths_are_gate_exempt():
    assert gate.is_oauth_path("/.well-known/oauth-protected-resource")
    assert gate.is_oauth_path("/authorize")
    assert gate.is_oauth_path("/token")
    assert not gate.is_oauth_path("/mcp")

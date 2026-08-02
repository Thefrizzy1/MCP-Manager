"""The login/session HTTP surface (ui/api/auth.py + verify_auth cookie path).

Uses the assembled UI app but points the user store at a tmp dir so the suite
never touches the real data/ui_users.json, and constructs TestClient without the
lifespan context (no scheduler/queue threads needed for auth checks).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import ui.api.auth as auth_mod
    import ui.api.deps as deps
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    monkeypatch.setattr(auth_mod, "ROOT", tmp_path)
    from config import cfg
    monkeypatch.setattr(cfg, "ui_username", "admin", raising=False)
    monkeypatch.setattr(cfg, "ui_password", "", raising=False)
    from ui.api import build_ui_app
    return TestClient(build_ui_app())


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_app_redirects_to_login_when_signed_out(client):
    r = client.get("/app", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_browser_gets_no_basic_dialog(client):
    """An unauthenticated browser (Accept: text/html) must NOT receive a
    WWW-Authenticate: Basic challenge — that is the endless-prompt bug."""
    r = client.get("/api/v1/auth/whoami", headers={"Accept": "text/html"})
    assert r.status_code == 401
    assert "www-authenticate" not in {k.lower() for k in r.headers}


def test_api_client_still_gets_basic_challenge(client):
    r = client.get("/api/v1/auth/whoami", headers={"Accept": "application/json"})
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Basic"


def test_wrong_password_rejected(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_default_login_then_session_works(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "admin" and body["must_change"] is True
    assert "plutus_session" in r.cookies or "plutus_session" in client.cookies
    # The cookie now authenticates subsequent requests.
    who = client.get("/api/v1/auth/whoami")
    assert who.status_code == 200
    assert who.json()["username"] == "admin"
    assert who.json()["default_password_active"] is True
    # And the shell is served (missing-build page is fine; it's still 200).
    assert client.get("/app").status_code == 200


def test_basic_auth_path_works(client):
    r = client.get("/api/v1/auth/whoami", auth=("admin", "adminadmin"))
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_admin_can_add_user_and_nonadmin_is_blocked(client):
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    r = client.post("/api/v1/auth/users",
                    json={"username": "alice", "password": "alice-strong-pw", "role": "user"})
    assert r.status_code == 200
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    # Alice (a non-admin) cannot list users.
    client.post("/api/v1/auth/login", json={"username": "alice", "password": "alice-strong-pw"})
    r = client.get("/api/v1/auth/users")
    assert r.status_code == 403


def test_change_password_flow(client):
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    bad = client.post("/api/v1/auth/change-password",
                      json={"current_password": "wrong", "new_password": "new-strong-pw"})
    assert bad.status_code == 400
    ok = client.post("/api/v1/auth/change-password",
                     json={"current_password": "adminadmin", "new_password": "new-strong-pw"})
    assert ok.status_code == 200
    # Default flag cleared; old password no longer works.
    who = client.get("/api/v1/auth/whoami")
    assert who.json()["default_password_active"] is False
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    assert client.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "adminadmin"}).status_code == 401
    assert client.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "new-strong-pw"}).status_code == 200


def test_logout_clears_session(client):
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert client.get("/api/v1/auth/whoami").status_code == 200
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    r = client.get("/api/v1/auth/whoami", headers={"Accept": "application/json"})
    assert r.status_code == 401

"""OAuth 2.1 provider — PKCE authorization-code flow, DCR, tokens, metadata.

Security-critical, so the happy path AND every failure mode are pinned down.
"""
import base64
import hashlib

import pytest

from core import oauth_provider as op


def _pkce(verifier: str = "verifier-0123456789-abcdefghijklmnop") -> tuple[str, str]:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _register(root) -> dict:
    return op.register_client(root, {"client_name": "Test", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]})


# ── registration ──────────────────────────────────────────────────────────────

def test_register_issues_public_client(tmp_path):
    c = _register(tmp_path)
    assert c["client_id"].startswith("plutus-")
    assert c["token_endpoint_auth_method"] == "none"  # public client, PKCE
    assert op.get_client(tmp_path, c["client_id"])["client_name"] == "Test"


def test_register_rejects_missing_or_insecure_redirect(tmp_path):
    with pytest.raises(ValueError):
        op.register_client(tmp_path, {"redirect_uris": []})
    with pytest.raises(ValueError):
        op.register_client(tmp_path, {"redirect_uris": ["http://evil.example/cb"]})  # not https/localhost


# ── PKCE ──────────────────────────────────────────────────────────────────────

def test_pkce_s256_roundtrip():
    v, c = _pkce()
    assert op.verify_pkce(v, c, "S256") is True
    assert op.verify_pkce("wrong", c, "S256") is False


# ── full authorization-code + PKCE flow ───────────────────────────────────────

def test_happy_path_authorize_exchange_validate(tmp_path):
    client = _register(tmp_path)
    v, chal = _pkce()
    redirect = client["redirect_uris"][0]
    code = op.issue_code(tmp_path, client_id=client["client_id"], redirect_uri=redirect,
                         code_challenge=chal, code_challenge_method="S256")
    tok = op.exchange_code(tmp_path, code=code, code_verifier=v,
                           client_id=client["client_id"], redirect_uri=redirect)
    assert tok["token_type"] == "Bearer"
    assert tok["access_token"].startswith("mcp_at_")
    assert op.validate_access_token(tmp_path, tok["access_token"]) is True
    assert op.validate_access_token(tmp_path, "mcp_at_nope") is False


def test_code_is_single_use(tmp_path):
    client = _register(tmp_path)
    v, chal = _pkce()
    redirect = client["redirect_uris"][0]
    code = op.issue_code(tmp_path, client_id=client["client_id"], redirect_uri=redirect, code_challenge=chal)
    op.exchange_code(tmp_path, code=code, code_verifier=v, client_id=client["client_id"], redirect_uri=redirect)
    with pytest.raises(ValueError):
        op.exchange_code(tmp_path, code=code, code_verifier=v, client_id=client["client_id"], redirect_uri=redirect)


def test_pkce_mismatch_rejected(tmp_path):
    client = _register(tmp_path)
    _, chal = _pkce()
    redirect = client["redirect_uris"][0]
    code = op.issue_code(tmp_path, client_id=client["client_id"], redirect_uri=redirect, code_challenge=chal)
    with pytest.raises(ValueError):
        op.exchange_code(tmp_path, code=code, code_verifier="not-the-verifier",
                         client_id=client["client_id"], redirect_uri=redirect)


def test_redirect_uri_must_match(tmp_path):
    client = _register(tmp_path)
    v, chal = _pkce()
    code = op.issue_code(tmp_path, client_id=client["client_id"],
                         redirect_uri=client["redirect_uris"][0], code_challenge=chal)
    with pytest.raises(ValueError):
        op.exchange_code(tmp_path, code=code, code_verifier=v,
                         client_id=client["client_id"], redirect_uri="https://evil.example/cb")


def test_refresh_token_issues_new_access(tmp_path):
    client = _register(tmp_path)
    v, chal = _pkce()
    redirect = client["redirect_uris"][0]
    code = op.issue_code(tmp_path, client_id=client["client_id"], redirect_uri=redirect, code_challenge=chal)
    tok = op.exchange_code(tmp_path, code=code, code_verifier=v, client_id=client["client_id"], redirect_uri=redirect)
    tok2 = op.refresh_token(tmp_path, refresh=tok["refresh_token"], client_id=client["client_id"])
    assert tok2["access_token"] != tok["access_token"]
    assert op.validate_access_token(tmp_path, tok2["access_token"]) is True


def test_expired_code_pruned(tmp_path, monkeypatch):
    client = _register(tmp_path)
    v, chal = _pkce()
    redirect = client["redirect_uris"][0]
    code = op.issue_code(tmp_path, client_id=client["client_id"], redirect_uri=redirect, code_challenge=chal)
    monkeypatch.setattr(op.time, "time", lambda: 9_999_999_999.0)  # far future
    with pytest.raises(ValueError):
        op.exchange_code(tmp_path, code=code, code_verifier=v, client_id=client["client_id"], redirect_uri=redirect)


# ── metadata + creds ──────────────────────────────────────────────────────────

def test_metadata_documents():
    prm = op.protected_resource_metadata("https://mcp.example.com", "https://mcp.example.com/mcp")
    assert prm["authorization_servers"] == ["https://mcp.example.com"]
    asm = op.authorization_server_metadata("https://mcp.example.com/")
    assert asm["issuer"] == "https://mcp.example.com"
    assert asm["authorization_endpoint"] == "https://mcp.example.com/authorize"
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["token_endpoint_auth_methods_supported"] == ["none"]


def test_credential_check():
    assert op.check_user_credentials("admin", "pw", expected_user="admin", expected_pass="pw") is True
    assert op.check_user_credentials("admin", "bad", expected_user="admin", expected_pass="pw") is False
    assert op.check_user_credentials("x", "pw", expected_user="admin", expected_pass="pw") is False

"""_handle_error must not leak upstream bodies / exception text unless opted in."""
import httpx
import pytest

import client


def _status_error(status=500, body="SECRET_TOKEN=abc leaked"):
    req = httpx.Request("GET", "http://svc/api")
    resp = httpx.Response(status, text=body, request=req)
    return httpx.HTTPStatusError("boom", request=req, response=resp)


def test_status_body_hidden_by_default(monkeypatch):
    monkeypatch.delenv("PLUTUS_VERBOSE_ERRORS", raising=False)
    msg = client._handle_error(_status_error(), "Sonarr")
    assert "SECRET_TOKEN" not in msg
    assert "500" in msg


def test_status_body_shown_when_verbose(monkeypatch):
    monkeypatch.setenv("PLUTUS_VERBOSE_ERRORS", "1")
    msg = client._handle_error(_status_error(), "Sonarr")
    assert "SECRET_TOKEN" in msg


def test_generic_exception_text_hidden_by_default(monkeypatch):
    monkeypatch.delenv("PLUTUS_VERBOSE_ERRORS", raising=False)
    msg = client._handle_error(ValueError("password=hunter2 in /home/.env"), "Foo")
    assert "hunter2" not in msg
    assert "ValueError" in msg


@pytest.mark.parametrize("status,needle", [(401, "authentication"), (403, "permission"), (404, "not found"), (429, "rate limit")])
def test_known_statuses_keep_friendly_message(monkeypatch, status, needle):
    monkeypatch.delenv("PLUTUS_VERBOSE_ERRORS", raising=False)
    msg = client._handle_error(_status_error(status=status, body="zzz"), "Svc").lower()
    assert needle in msg
    assert "zzz" not in msg

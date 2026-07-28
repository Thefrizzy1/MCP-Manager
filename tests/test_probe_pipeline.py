"""End-to-end probe pipeline self-test — deterministic, offline, no network.

Spins up a mock HTTP server that returns whatever status code the path asks for,
then drives the REAL pipeline (probe_http_service -> service_state_from_row) to
prove:

  * a reachable URL that returns 200 -> online
  * 401/403 -> auth_error, 429 -> rate_limited, 5xx -> api_error, 404 -> offline
  * a fake / unreachable URL -> offline   (a bad http address must NOT pass)
  * missing config -> unconfigured
  * saving config (apply_live_env) flips is_configured False -> True

This is the hard CI gate: it runs on a clean rebuild with no .env and no network,
so the tester/probe/state logic is guaranteed to keep working.
"""
from __future__ import annotations

import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from config import cfg
from core.builtin_services import SERVICES
from core.dashboard_health import probe_http_service, service_state_from_row
from core.service_utils import is_service_configured


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        code = 200
        parts = self.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "status" and parts[1].isdigit():
            code = int(parts[1])
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):  # silence
        pass


@pytest.fixture(scope="module")
def mock_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now -> connection refused
    return port


def _probe(url: str) -> dict:
    svc = {
        "id": "selftest",
        "label": "Self Test",
        "health_url": (lambda: url),
        "health_headers": (lambda: {}),
        "configured_keys": (),
    }
    return asyncio.run(probe_http_service(svc, cfg))


# ── real URL, various status codes -> the documented state ────────────────────

@pytest.mark.parametrize(
    "code,expected_state,expected_ok",
    [
        (200, "online", True),
        (204, "online", True),
        (301, "online", True),
        (401, "auth_error", None),
        (403, "auth_error", None),
        (429, "rate_limited", None),
        (404, "offline", False),
        (500, "api_error", False),
        (503, "api_error", False),
    ],
)
def test_probe_status_maps_to_state(mock_server, code, expected_state, expected_ok):
    row = _probe(f"{mock_server}/status/{code}")
    assert row["ok"] is expected_ok, f"{code}: ok={row['ok']!r}"
    assert service_state_from_row(row) == expected_state, f"{code} -> {service_state_from_row(row)!r}"


def test_real_url_200_is_online(mock_server):
    row = _probe(f"{mock_server}/status/200")
    assert service_state_from_row(row) == "online"


# ── fake / unreachable URL must fail ──────────────────────────────────────────

def test_unreachable_url_is_offline():
    row = _probe(f"http://127.0.0.1:{_closed_port()}/")
    assert row["ok"] is False
    assert service_state_from_row(row) == "offline"


def test_garbage_url_is_offline():
    row = _probe("http://nonexistent.invalid.example.doesnotresolve/")
    assert row["ok"] is False
    assert service_state_from_row(row) == "offline"


# ── config gating ─────────────────────────────────────────────────────────────

def test_missing_config_is_unconfigured():
    svc = {
        "id": "selftest2",
        "label": "Self Test 2",
        "config_from_env": True,
        "configured_env_keys": ("PLUTUS_SELFTEST_DEFINITELY_UNSET",),
        "health_url": (lambda: "http://127.0.0.1:1/"),
        "health_headers": (lambda: {}),
    }
    row = asyncio.run(probe_http_service(svc, cfg))
    assert row["kind"] == "unconfigured"
    assert service_state_from_row(row) == "unconfigured"


def test_apply_live_env_flips_is_configured(monkeypatch):
    """The regression that broke everything: saving config must make the running
    process see the service as configured, without a restart."""
    from config import apply_live_env

    comfy = next(s for s in SERVICES if s["id"] == "comfyui")  # configured_keys=("comfyui_url",)
    monkeypatch.setenv("COMFYUI_URL", "")
    monkeypatch.setattr(cfg, "comfyui_url", "", raising=False)
    assert is_service_configured(comfy, cfg) is False

    apply_live_env({"COMFYUI_URL": "http://127.0.0.1:8188"})

    assert is_service_configured(comfy, cfg) is True
    assert cfg.comfyui_url == "http://127.0.0.1:8188"

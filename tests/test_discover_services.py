"""Network discovery must only report services that actually respond — no bare
port assumptions (the old scanner reported every homelab port as present)."""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from core.discover_services import _http_probe, probe_host, tcp_open


def _server(handler_body: bytes, status: int = 200):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(handler_body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_tcp_open_closed_port():
    assert asyncio.run(tcp_open("127.0.0.1", 1, timeout=0.8)) is False


def test_unreachable_host_returns_nothing():
    # TEST-NET-1 (RFC 5737) is unroutable — nothing must be reported.
    assert asyncio.run(probe_host("192.0.2.1", timeout=1.0)) == []


def test_http_probe_rejects_wrong_fingerprint():
    srv, port = _server(b"plain nginx welcome page")
    try:
        got = asyncio.run(_http_probe("127.0.0.1", "jellyfin", port, "GET", "/", "jellyfin", 2.0))
        assert got is None  # open port, but body is not Jellyfin → reject
    finally:
        srv.shutdown()


def test_http_probe_accepts_matching_fingerprint():
    srv, port = _server(b"Jellyfin Server")
    try:
        got = asyncio.run(_http_probe("127.0.0.1", "jellyfin", port, "GET", "/", "jellyfin", 2.0))
        assert got and got["reachable"] is True and got["service_id"] == "jellyfin"
    finally:
        srv.shutdown()


def test_http_probe_auth_challenge_counts_as_present():
    srv, port = _server(b"", status=401)
    try:
        got = asyncio.run(_http_probe("127.0.0.1", "sonarr", port, "GET", "/api/v3/system/status", None, 2.0))
        assert got and "auth required" in got["status"]
    finally:
        srv.shutdown()


def test_http_probe_rejects_5xx():
    srv, port = _server(b"error", status=503)
    try:
        got = asyncio.run(_http_probe("127.0.0.1", "n8n", port, "GET", "/healthz", None, 2.0))
        assert got is None
    finally:
        srv.shutdown()

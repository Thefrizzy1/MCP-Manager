"""Live public smoke — the tester, for real, against the open internet.

Marked `live`: it hits real keyless public endpoints. It proves, end to end,
what the offline tests prove deterministically:

  * a real public HTTPS URL actually probes ONLINE
  * a fake URL actually fails (OFFLINE) — a bad http address must never pass
  * a real tool returns real STRUCTURED data (weather for Berlin -> Berlin,
    temperature in °C, humidity, wind)

If the network itself is unreachable the transport checks skip (so a CI network
blip is not a false failure), but whenever a response comes back the assertions
are real. Run just these with:  pytest -m live
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from config import cfg
from core.dashboard_health import probe_http_service, service_state_from_row
from core.invoke_tool import invoke_mcp_tool_fn
from ui.runtime import mcp

pytestmark = pytest.mark.live

# A stable, keyless public endpoint. ECB exchange rates — fast, JSON, no auth.
PUBLIC_OK_URL = "https://api.frankfurter.app/latest"


def _network_up() -> bool:
    try:
        httpx.get(PUBLIC_OK_URL, timeout=8.0)
        return True
    except Exception:
        return False


def _probe(url: str) -> dict:
    svc = {
        "id": "live",
        "label": "Live",
        "health_url": (lambda: url),
        "health_headers": (lambda: {}),
        "configured_keys": (),
    }
    return asyncio.run(probe_http_service(svc, cfg))


def test_real_public_url_probes_online():
    if not _network_up():
        pytest.skip("network unavailable")
    row = _probe(PUBLIC_OK_URL)
    assert row["ok"] is True, f"expected online, got {row}"
    assert service_state_from_row(row) == "online"


def test_fake_url_probes_offline():
    if not _network_up():
        pytest.skip("network unavailable")
    # Resolvable-looking but dead host: a fake address must not report healthy.
    row = _probe("https://nope.invalid.example.doesnotresolve/")
    assert row["ok"] is False
    assert service_state_from_row(row) == "offline"


def test_weather_tool_returns_structured_berlin():
    if not _network_up():
        pytest.skip("network unavailable")
    tool = mcp._tool_manager.get_tool("weather_current")
    assert tool is not None, "weather_current tool is not registered"
    out = str(asyncio.run(invoke_mcp_tool_fn(tool.fn, payload={"location": "Berlin"})))
    # If the upstream weather API itself is down, that's not our regression — skip.
    if out.lower().startswith("error") or "unavailable" in out.lower():
        pytest.skip(f"weather upstream unavailable: {out[:120]}")
    # Real, structured, and actually about Berlin.
    assert "Berlin" in out, out[:200]
    assert "Temperature" in out and "°C" in out, out[:200]
    assert "Humidity" in out and "Wind" in out, out[:200]
    # Not an error string.
    assert not out.lower().startswith("error"), out[:200]

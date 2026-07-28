"""Service Contract validator — every built-in card obeys docs/SERVICE_CONTRACT.md.

Offline and deterministic. If a card is malformed, drifts (configured_keys that
aren't real Config fields), probes a different address than the one you
configure, or advertises a tool that doesn't exist, this goes red — before any
image is published. This is the automated "audit all the cards" the rebuild
needs so the tester/probe/state logic can never silently rot.
"""
from __future__ import annotations

import re

import pytest

from config import Config, cfg, is_ui_writable_env_key
from core.builtin_services import SERVICES
from core.service_utils import _ck_parts
from ui.runtime import mcp

ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CONFIG_FIELDS = set(Config.model_fields)
REGISTERED_TOOLS = {t.name for t in mcp._tool_manager.list_tools()}
IDS = [s["id"] for s in SERVICES]


def _svc(sid: str) -> dict:
    return next(s for s in SERVICES if s["id"] == sid)


def _url_attr(svc: dict) -> str | None:
    """The cfg attribute holding this service's base URL, if it has one."""
    for tup in svc.get("config_keys", []) or []:
        key, _label, _ph, secret = _ck_parts(tup)
        if not secret and key.upper().endswith("_URL"):
            return key.lower()
    return None


def test_ids_are_unique():
    assert len(IDS) == len(set(IDS)), "duplicate service ids in SERVICES"


@pytest.mark.parametrize("sid", IDS)
def test_r1_structure(sid):
    s = _svc(sid)
    for k in ("id", "label", "section", "tag"):
        assert isinstance(s.get(k), str) and s[k].strip(), f"{sid}: missing/empty {k}"
    assert ID_RE.match(s["id"]), f"{sid}: id is not a lowercase slug"
    assert s["section"] in {"selfhosted", "system", "public"}, f"{sid}: bad section {s['section']!r}"
    assert isinstance(s.get("tools", []), list), f"{sid}: tools must be a list"


@pytest.mark.parametrize("sid", IDS)
def test_r2_config_keys_wellformed(sid):
    s = _svc(sid)
    for tup in s.get("config_keys", []) or []:
        key, label, _ph, secret = _ck_parts(tup)
        assert is_ui_writable_env_key(key), f"{sid}: {key!r} is not a valid UI-writable env key"
        assert isinstance(secret, bool), f"{sid}: secret flag for {key} must be bool"
        assert label, f"{sid}: {key} needs a human label"


@pytest.mark.parametrize("sid", IDS)
def test_r3_configured_keys_are_real(sid):
    """The exact bug class that broke everything: configured_keys that aren't
    real Config attributes -> is_configured never becomes True."""
    s = _svc(sid)
    for k in s.get("configured_keys", ()) or ():
        assert k in CONFIG_FIELDS, f"{sid}: configured_keys '{k}' is not a Config field (drift!)"


@pytest.mark.parametrize("sid", IDS)
def test_r4_probe_derives_from_configured_url(sid, monkeypatch):
    s = _svc(sid)
    if s.get("health_url") is None:
        pytest.skip("no HTTP probe")
    attr = _url_attr(s)
    if attr and attr in CONFIG_FIELDS:
        monkeypatch.setattr(cfg, attr, "http://sentinel.invalid:9999", raising=False)
        out = str(s["health_url"]())
        assert "sentinel.invalid" in out, (
            f"{sid}: probe URL {out!r} does not derive from the configured {attr} "
            f"(config/probe drift — you'd probe a different address than you set)"
        )


@pytest.mark.parametrize("sid", IDS)
def test_r5_probe_callables_never_crash(sid):
    s = _svc(sid)
    hu, hh = s.get("health_url"), s.get("health_headers")
    if hu is not None:
        assert isinstance(hu(), (str, type(None))), f"{sid}: health_url must return str/None"
    assert callable(hh), f"{sid}: health_headers must be callable"
    assert isinstance(hh(), dict), f"{sid}: health_headers must return a dict"


@pytest.mark.parametrize("sid", IDS)
def test_r6_declared_tools_are_registered(sid):
    """A card cannot advertise a tool that doesn't exist — the core 'not fake' rule."""
    s = _svc(sid)
    missing = [t.get("name") for t in s.get("tools", []) if t.get("name") not in REGISTERED_TOOLS]
    assert not missing, f"{sid}: declares tools that are not registered on the MCP server: {missing}"

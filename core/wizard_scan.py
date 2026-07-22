"""Unified setup wizard: Docker port mapping + optional LAN port scan."""

from __future__ import annotations

from typing import Any

from core.discover_services import probe_host
from core.docker_wizard import discover_docker_suggestions
from core.service_logos import CLEARBIT_DOMAIN_BY_ID


def _service_label(service_id: str, services: list[dict]) -> str:
    for s in services:
        if s.get("id") == service_id:
            return str(s.get("label") or service_id)
    return service_id


def _first_url_env_key(service_id: str, services: list[dict]) -> str | None:
    for s in services:
        if s.get("id") != service_id:
            continue
        for tup in s.get("config_keys") or []:
            key = tup[0] if isinstance(tup, (tuple, list)) else ""
            if key and "_URL" in key:
                return key
    return None


def _human_label(service_id: str, env_key: str, services: list[dict]) -> str:
    for s in services:
        if s.get("id") != service_id:
            continue
        for tup in s.get("config_keys") or []:
            if isinstance(tup, (tuple, list)) and len(tup) >= 2 and tup[0] == env_key:
                return str(tup[1])
    return env_key.replace("_", " ").title()


def _attach_logo_meta(row: dict) -> dict:
    sid = str(row.get("service_id") or "").strip().lower()
    ld = (row.get("logo_domain") or "").strip() or CLEARBIT_DOMAIN_BY_ID.get(sid, "")
    return {**row, **({"logo_domain": ld} if ld else {})}


def _enrich_row(row: dict, services: list[dict]) -> dict:
    sid = row.get("service_id") or ""
    lbl = _service_label(sid, services)
    eks = row.get("editable_keys")
    if not isinstance(eks, list) or not eks or not isinstance(eks[0], dict):
        return _attach_logo_meta({**row, "label": lbl})
    e0 = eks[0]
    url = str(e0.get("value") or "").rstrip("/")
    key = _first_url_env_key(sid, services) or str(e0.get("key") or "")
    if not key:
        return _attach_logo_meta({**row, "label": lbl})
    hl = _human_label(sid, key, services)
    return _attach_logo_meta(
        {
            **row,
            "label": lbl,
            "env": {key: url},
            "editable_keys": [{"key": key, "label": hl, "value": url}],
        }
    )


async def build_wizard_scan(host: str, *, include_port_scan: bool, services: list[dict]) -> dict[str, Any]:
    h = host.strip()
    dock = await discover_docker_suggestions(h)
    port_hits: list[dict[str, Any]] = []
    if include_port_scan and h:
        port_hits = await probe_host(h)

    unified: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in dock.get("suggestions") or []:
        sid = row.get("service_id")
        if isinstance(sid, str) and sid:
            unified.append(_enrich_row(dict(row), services))
            seen.add(sid)

    for hit in port_hits:
        sid = hit.get("service_id")
        if not isinstance(sid, str) or sid in seen:
            continue
        url = (hit.get("suggested_url") or "").rstrip("/")
        if not url:
            continue
        envk = _first_url_env_key(sid, services)
        if not envk:
            continue
        unified.append(
            _enrich_row(
                {
                    "service_id": sid,
                    "container": "",
                    "image": "",
                    "public_port": hit.get("port"),
                    "container_port": None,
                    "env": {envk: url},
                    "source": "port_scan",
                    "status": hit.get("status"),
                    "editable_keys": [
                        {"key": envk, "label": envk.replace("_", " ").title(), "value": url}
                    ],
                },
                services,
            )
        )
        seen.add(sid)

    return {
        "host": h,
        "include_port_scan": include_port_scan,
        "docker": dock,
        "port_scan_hits": port_hits,
        "suggestions": unified,
    }

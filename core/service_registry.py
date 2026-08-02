"""Canonical service registry for dashboard rows and router capability ownership."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.builtin_services import SERVICES
from core.custom_integrations import custom_integrations_as_services


def builtin_services() -> list[dict[str, Any]]:
    return list(SERVICES)


def all_services(root: Path) -> list[dict[str, Any]]:
    from tools.public_apis_bulk import PUBLIC_SERVICES_DASHBOARD

    custom = custom_integrations_as_services(root)
    rows = builtin_services() + list(PUBLIC_SERVICES_DASHBOARD) + custom
    return [svc for svc in rows if svc.get("tools") or svc.get("section") == "custom"]


def service_tool_map(root: Path, services: list[dict[str, Any]] | None = None,
                     tool_names: list[str] | None = None) -> dict[str, str]:
    """{tool_name: service_id} — what the connection picker gates.

    Cards are the source of truth, and they are incomplete: a card lists the
    tools worth a button, not every tool the integration registers. Since this
    map is what ``_agent_service_disallow`` iterates, anything missing from it was
    **never gated at all** — unticking Nextcloud still left an agent able to
    upload, move and delete files there, because those tools are not on the card.

    So a second pass claims the rest by prefix, and the prefixes are *learned from
    the cards themselves* rather than hardcoded: if every carded ``nextcloud_*``
    tool belongs to ``nextcloud``, so does ``nextcloud_upload_file``. A prefix
    that two services both claim is left alone rather than guessed at.
    """
    rows = services if services is not None else all_services(root)
    out: dict[str, str] = {}
    claims: dict[str, set[str]] = {}
    for svc in rows:
        sid = str(svc.get("id") or "")
        for tool in svc.get("tools") or []:
            name = str(tool.get("name") or "")
            if not name or not sid:
                continue
            out[name] = sid
            prefix = name.split("_", 1)[0]
            if prefix:
                claims.setdefault(prefix, set()).add(sid)

    if tool_names:
        owned = {p: next(iter(s)) for p, s in claims.items() if len(s) == 1}
        for name in tool_names:
            if name in out:
                continue
            sid = owned.get(name.split("_", 1)[0])
            if sid:
                out[name] = sid
    return out

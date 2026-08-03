"""Brand logos: local /icons/, Clearbit, Simple Icons, Google favicon fallback (chain via JS).

Rendering only. The brand-logo *data* (domain + Simple-Icons slug per service id)
is the single source of truth in ``core/builtin_services``, co-located with the
service catalogue — it used to be a second, hand-maintained copy here that had
drifted (duplicate keys, one of which silently changed a domain). The two names
are kept as aliases so anything importing them from this module is unchanged.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlparse

from core.builtin_services import (
    SERVICE_ICON_SLUG as SIMPLE_ICON_SLUG_BY_ID,
    SERVICE_LOGO_DOMAIN as CLEARBIT_DOMAIN_BY_ID,
)


def _google_favicon_url(hostname: str) -> str:
    h = hostname.strip().lower()
    return f"https://www.google.com/s2/favicons?domain={h}&sz=64"


def hostname_from_http_url(url: str) -> str | None:
    url = (url or "").strip()
    if not url.startswith("http"):
        return None
    try:
        host = (urlparse(url).hostname or "").lower().strip()
    except Exception:
        return None
    if not host or host == "localhost":
        return None
    # crude skip ipv4
    if host.replace(".", "").isdigit():
        return None
    return host


def logo_sources_ordered(
    *,
    service_id: str,
    root: Path,
    logo_domain_override: str | None,
    http_base_url: str | None,
) -> list[str]:
    """Return candidate image URLs (first = primary src, rest go in data-chain)."""
    sid = service_id.strip().lower()
    out: list[str] = []

    for ext in (".svg", ".png", ".webp"):
        p = root / "icons" / f"{sid}{ext}"
        if p.is_file():
            out.append(f"/icons/{sid}{ext}")
            break

    domain = (logo_domain_override or "").strip().lower() or CLEARBIT_DOMAIN_BY_ID.get(sid)
    hf = hostname_from_http_url(http_base_url or "")

    # Google s2 first — works reliably as <img>; DDG/Clearbit sometimes block hotlinks by referrer.
    if hf:
        out.append(_google_favicon_url(hf))
        out.append(f"https://icons.duckduckgo.com/ip3/{hf}.ico")

    if domain:
        if not hf or hf != domain:
            out.append(_google_favicon_url(domain))
            out.append(f"https://icons.duckduckgo.com/ip3/{domain}.ico")
        out.append(f"https://logo.clearbit.com/{domain}")

    si = SIMPLE_ICON_SLUG_BY_ID.get(sid)
    if si:
        out.append(f"https://cdn.jsdelivr.net/npm/simple-icons@v11/icons/{si}.svg")

    # de-dupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def service_logo_img_html(
    *,
    service_id: str,
    root: Path,
    logo_domain_override: str | None = None,
    http_base_url: str | None = None,
    alt_label: str,
) -> str:
    """Single <img> with chained fallbacks (requires small JS on page)."""
    chain = logo_sources_ordered(
        service_id=service_id,
        root=root,
        logo_domain_override=logo_domain_override,
        http_base_url=http_base_url,
    )
    if not chain:
        return ""
    primary = chain[0]
    rest = chain[1:]
    chain_json = html.escape(json.dumps(rest), quote=True)
    alt_esc = html.escape(alt_label, quote=True)
    src_esc = html.escape(primary, quote=True)
    return (
        f'<img class="svc-logo plutus-chain-logo" alt="" title="{alt_esc}" loading="lazy" '
        f'decoding="async" referrerpolicy="no-referrer" src="{src_esc}" data-chain="{chain_json}" />'
    )


def wizard_logo_domain(service_id: str) -> str | None:
    return CLEARBIT_DOMAIN_BY_ID.get(service_id.strip().lower())

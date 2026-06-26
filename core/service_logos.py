"""Brand logos: local /icons/, Clearbit, Simple Icons, Google favicon fallback (chain via JS)."""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlparse

# Primary brand domains for https://logo.clearbit.com/<domain>
CLEARBIT_DOMAIN_BY_ID: dict[str, str] = {
    "jellyfin": "jellyfin.org",
    "sonarr": "sonarr.tv",
    "radarr": "radarr.video",
    "lidarr": "lidarr.audio",
    "jellyseerr": "jellyfin.org",
    "qbittorrent": "qbittorrent.org",
    "immich": "immich.app",
    "homeassistant": "home-assistant.io",
    "nextcloud": "nextcloud.com",
    "habitica": "habitica.com",
    "n8n": "n8n.io",
    "ntfy": "ntfy.sh",
    "syncthing": "syncthing.net",
    "obsidian": "obsidian.md",
    "uptime_kuma": "uptime-kuma.io",
    "omv": "openmediavault.org",
    "docker": "docker.com",
    "comfyui": "github.com",
    "tailscale": "tailscale.com",
    "fail2ban": "fail2ban.org",
    "fal": "fal.ai",
    "weather": "wttr.in",
    "maps": "openstreetmap.org",
    "websearch": "duckduckgo.com",
    "smtp": "proton.me",
    "wikipedia": "wikipedia.org",
    "currency": "frankfurter.app",
    "google": "google.com",
    "pub_network": "httpbin.org",
    "pub_geo_time": "openstreetmap.org",
    "pub_finance_crypto": "coingecko.com",
    "pub_fun": "quotable.io",
    "pub_education": "openlibrary.org",
    "pub_games": "pokeapi.co",
    "pub_space": "nasa.gov",
    "pub_dev_culture": "github.com",
    "audiobookshelf": "audiobookshelf.org",
    "kavita": "kavitareader.com",
    "paperless": "docs.paperless-ngx.com",
    "vaultwarden": "vaultwarden.org",
    "portainer": "portainer.io",
    "gitea": "gitea.io",
    "grafana": "grafana.com",
    "prometheus": "prometheus.io",
    "pihole": "pi-hole.net",
    "adguard": "adguard.com",
    "transmission": "transmissionbt.com",
    "sabnzbd": "sabnzbd.org",
    "bookstack": "bookstackapp.com",
    "minio": "min.io",
    "matrix_synapse": "matrix.org",
    "ghost": "ghost.org",
    "wikijs": "js.wiki",
}

# Simple Icons slug (jsDelivr) — fallback when raster/CDN logos fail
SIMPLE_ICON_SLUG_BY_ID: dict[str, str] = {
    "docker": "docker",
    "jellyfin": "jellyfin",
    "sonarr": "sonarr",
    "radarr": "radarr",
    "lidarr": "lidarr",
    "immich": "immich",
    "homeassistant": "homeassistant",
    "nextcloud": "nextcloud",
    "n8n": "n8n",
    "syncthing": "syncthing",
    "tailscale": "tailscale",
    "qbittorrent": "qbittorrent",
    "obsidian": "obsidian",
    "postgres": "postgresql",
    "comfyui": "comfyui",
    "ntfy": "ntfy",
    "maps": "openstreetmap",
    "websearch": "duckduckgo",
    "wikipedia": "wikipedia",
    "google": "google",
    "pub_network": "cloudflare",
    "pub_geo_time": "openstreetmap",
    "pub_finance_crypto": "coingecko",
    "pub_fun": "steam",
    "pub_education": "wikimediafoundation",
    "pub_games": "nintendo",
    "pub_space": "nasa",
    "pub_dev_culture": "github",
    "audiobookshelf": "audiobookshelf",
    "vaultwarden": "bitwarden",
    "portainer": "portainer",
    "gitea": "gitea",
    "grafana": "grafana",
    "prometheus": "prometheus",
    "pihole": "pi-hole",
    "minio": "minio",
    "ghost": "ghost",
}


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

"""SSRF screening for outbound fetches of user/model-supplied URLs.

``web_fetch`` will retrieve whatever URL it's handed. Without a guard, an
attacker-crafted instruction can make it read cloud-metadata
(169.254.169.254), loopback admin panels, or internal services and hand the
body back to the model. ``screen_url`` rejects non-HTTP(S) schemes and any host
that resolves to a private / loopback / link-local / reserved address.

Note: this screens the resolved address before the request. It does not pin the
IP, so a determined DNS-rebinding attacker could still race it — acceptable for a
LAN/Tailscale homelab; documented in docs/SECURITY.md.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def ip_is_blocked(ip_str: str) -> bool:
    """True if the IP is in a range we must not let outbound fetches reach."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # not a parseable IP -> refuse rather than guess
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def screen_url(url: str, *, resolver=_resolve) -> str | None:
    """Return a rejection reason if the URL is unsafe to fetch, else None.

    ``resolver`` is injectable for testing (host -> list of IP strings).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "Malformed URL."
    if parsed.scheme not in ("http", "https"):
        return f"Only http/https URLs are allowed (got scheme '{parsed.scheme or ''}')."
    host = parsed.hostname
    if not host:
        return "URL has no host."
    # If the host is an IP literal, screen it directly.
    try:
        ipaddress.ip_address(host)
        return "Refusing to fetch a private/internal address." if ip_is_blocked(host) else None
    except ValueError:
        pass
    # Otherwise resolve and screen every resulting address.
    try:
        addrs = resolver(host)
    except Exception:
        return f"Could not resolve host '{host}'."
    if not addrs:
        return f"Could not resolve host '{host}'."
    for addr in addrs:
        if ip_is_blocked(addr):
            return "Refusing to fetch a host that resolves to a private/internal address."
    return None

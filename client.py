"""
client.py — Shared async HTTP client helpers.
All service clients use these utilities for consistency.
"""

import os
import json
import httpx
from typing import Any, Optional


TIMEOUT = httpx.Timeout(30.0)


def _verbose_errors() -> bool:
    """Echo upstream response bodies / raw exception text into tool output.

    Off by default: upstream bodies and exception strings can carry secrets,
    tokens, internal hostnames, or full URLs, which would land in MCP
    transcripts and logs. Set PLUTUS_VERBOSE_ERRORS=1 in .env when debugging.
    """
    return os.getenv("PLUTUS_VERBOSE_ERRORS", "").strip().lower() in ("1", "true", "yes")


def _handle_error(e: Exception, service: str) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 401:
            return f"Error: {service} authentication failed. Check your API key."
        elif status == 403:
            return f"Error: {service} permission denied."
        elif status == 404:
            return f"Error: {service} resource not found."
        elif status == 429:
            return f"Error: {service} rate limit hit. Try again later."
        elif _verbose_errors():
            return f"Error: {service} returned HTTP {status}: {e.response.text[:200]}"
        else:
            return f"Error: {service} returned HTTP {status}. (set PLUTUS_VERBOSE_ERRORS=1 for the response body)"
    elif isinstance(e, httpx.TimeoutException):
        return f"Error: {service} request timed out."
    elif isinstance(e, httpx.ConnectError):
        return f"Error: Cannot connect to {service}. Is it running?"
    if _verbose_errors():
        return f"Error: {service} unexpected error: {type(e).__name__}: {str(e)[:200]}"
    return f"Error: {service} unexpected error ({type(e).__name__})."


async def arr_get(base_url: str, api_key: str, path: str, params: Optional[dict] = None) -> Any:
    """Generic GET for Sonarr/Radarr (API v3)."""
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.get(
            f"{base}/api/v3/{path}",
            headers={"X-Api-Key": api_key},
            params=params or {},
        )
        r.raise_for_status()
        return r.json()


async def lidarr_get(base_url: str, api_key: str, path: str, params: Optional[dict] = None) -> Any:
    """Lidarr uses API v1, not v3."""
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.get(
            f"{base}/api/v1/{path}",
            headers={"X-Api-Key": api_key},
            params=params or {},
        )
        r.raise_for_status()
        return r.json()


async def lidarr_post(base_url: str, api_key: str, path: str, body: dict) -> Any:
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.post(
            f"{base}/api/v1/{path}",
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            json=body,
        )
        r.raise_for_status()
        return r.json()


async def arr_post(base_url: str, api_key: str, path: str, body: dict) -> Any:
    """Generic POST for *arr services."""
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.post(
            f"{base}/api/v3/{path}",
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            json=body,
        )
        r.raise_for_status()
        return r.json()


async def arr_delete(base_url: str, api_key: str, path: str, params: Optional[dict] = None) -> Any:
    """Generic DELETE for *arr services."""
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.delete(
            f"{base}/api/v3/{path}",
            headers={"X-Api-Key": api_key},
            params=params or {},
        )
        r.raise_for_status()
        return r.json() if r.content else {}


def fmt_json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def fmt_size(bytes_val: int) -> str:
    """Convert bytes to human-readable size."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"

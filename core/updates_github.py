"""Optional GitHub release check (public API, no token)."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from core.version_info import VERSION

_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


async def check_github_release(repo: str) -> dict[str, Any]:
    repo = (repo or "").strip()
    if not repo:
        return {
            "ok": False,
            "message": "Set PLUTUS_UPDATES_REPO=owner/repo in .env to enable release checks.",
            "current": VERSION,
        }
    if not _REPO_RE.match(repo):
        return {"ok": False, "message": f"Invalid repo slug: {repo!r}", "current": VERSION}

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"plutus-mcp/{VERSION}",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            r = await client.get(url, headers=headers)
    except Exception as e:
        return {"ok": False, "message": str(e), "current": VERSION}

    if r.status_code == 404:
        return {
            "ok": False,
            "message": "No releases found for that repository (404).",
            "current": VERSION,
        }
    if r.status_code != 200:
        return {
            "ok": False,
            "message": f"GitHub API HTTP {r.status_code}",
            "current": VERSION,
        }

    try:
        body = r.json()
    except Exception:
        return {"ok": False, "message": "Invalid JSON from GitHub", "current": VERSION}

    tag = (body.get("tag_name") or "").strip() or "?"
    html_url = (body.get("html_url") or "").strip()
    published = (body.get("published_at") or "").strip()
    name = (body.get("name") or "").strip()

    return {
        "ok": True,
        "current": VERSION,
        "latest_tag": tag,
        "latest_name": name,
        "published_at": published,
        "release_url": html_url,
        "up_to_date_hint": tag.lstrip("v") == VERSION or tag == VERSION,
        "message": f"Latest release: {tag}" + (f" — {name}" if name else ""),
    }

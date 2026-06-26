"""
tools/obsidian.py — Obsidian Local REST API tools
Requires the Obsidian Local REST API plugin on the Windows PC.
Only works when Obsidian is open on the PC.
"""

import httpx
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT, _handle_error


def register_obsidian_tools(mcp: FastMCP):

    def _obs_headers() -> dict:
        # Content-Type lives on the request that actually has a body; default
        # GETs don't need it. Note: write/append override this with text/markdown.
        return {"Authorization": f"Bearer {cfg.obsidian_api_key}"}

    def _obs_client():
        """httpx.AsyncClient pre-configured for Obsidian Local REST API.

        Obsidian's plugin defaults to HTTPS on port 27124 with a *self-signed*
        certificate; without verify=False every call dies with
        SSLCertVerificationError. Following plugin docs we just turn cert
        verification off — the connection is still encrypted.
        """
        return httpx.AsyncClient(timeout=TIMEOUT, verify=False)

    def _vault_url(path: str) -> str:
        """Build /vault/<encoded path> safely — Obsidian rejects raw spaces and
        unicode in URLs (e.g. 'Brain FULL/00 dashboard/file.md')."""
        from urllib.parse import quote
        return f"{cfg.obsidian_url}/vault/{quote(path or '', safe='/')}"

    @mcp.tool(name="obsidian_get_note", annotations={"readOnlyHint": True})
    async def obsidian_get_note(params: "ObsidianNoteInput") -> str:
        """Read a note from Obsidian vault by path.

        Path is relative to vault root e.g. 'Brain FULL/Brain/00-dashboard/current-focus.md'
        Only works when Obsidian is open on Friso's Windows PC.
        """
        if not cfg.is_configured("obsidian_url", "obsidian_api_key"):
            return "Error: Obsidian REST API not configured. Set OBSIDIAN_URL and OBSIDIAN_API_KEY in .env"
        try:
            async with _obs_client() as client:
                r = await client.get(
                    _vault_url(params.path),
                    headers=_obs_headers()
                )
                if r.status_code == 404:
                    return f"Error: Note not found: '{params.path}'"
                r.raise_for_status()
                content = r.text

            return f"## Note: {params.path}\n\n{content}"
        except Exception as e:
            return _handle_error(e, "Obsidian")

    @mcp.tool(name="obsidian_write_note", annotations={"readOnlyHint": False})
    async def obsidian_write_note(params: "ObsidianWriteInput") -> str:
        """Create or update a note in Obsidian vault.

        Path is relative to vault root. Creates parent directories automatically.
        """
        if not cfg.is_configured("obsidian_url", "obsidian_api_key"):
            return "Error: Obsidian REST API not configured."
        try:
            async with _obs_client() as client:
                r = await client.put(
                    _vault_url(params.path),
                    headers={**_obs_headers(), "Content-Type": "text/markdown"},
                    content=params.content.encode()
                )
                r.raise_for_status()
            return f"✓ Note written: '{params.path}' ({len(params.content)} chars)"
        except Exception as e:
            return _handle_error(e, "Obsidian")

    @mcp.tool(name="obsidian_append_to_note", annotations={"readOnlyHint": False})
    async def obsidian_append_to_note(params: "ObsidianAppendInput") -> str:
        """Append content to an existing Obsidian note."""
        if not cfg.is_configured("obsidian_url", "obsidian_api_key"):
            return "Error: Obsidian REST API not configured."
        try:
            async with _obs_client() as client:
                r = await client.post(
                    _vault_url(params.path),
                    headers={**_obs_headers(), "Content-Type": "text/markdown"},
                    content=f"\n{params.content}".encode()
                )
                r.raise_for_status()
            return f"✓ Appended to '{params.path}'"
        except Exception as e:
            return _handle_error(e, "Obsidian")

    @mcp.tool(name="obsidian_search", annotations={"readOnlyHint": True})
    async def obsidian_search(params: "ObsidianSearchInput") -> str:
        """Search Obsidian vault for notes containing a query string."""
        if not cfg.is_configured("obsidian_url", "obsidian_api_key"):
            return "Error: Obsidian REST API not configured."
        try:
            async with _obs_client() as client:
                r = await client.post(
                    f"{cfg.obsidian_url}/search/simple/",
                    headers=_obs_headers(),
                    params={"query": params.query, "contextLength": 200}
                )
                r.raise_for_status()
                results = r.json()

            if not results:
                return f"No notes found matching '{params.query}'"

            result = f"## Obsidian Search: '{params.query}' ({len(results)} results)\n\n"
            for item in results[:params.limit]:
                result += f"**{item.get('filename', '?')}**\n"
                matches = item.get("matches", [])
                for match in matches[:2]:
                    context = match.get("context", "")
                    if context:
                        result += f"  ...{context.strip()[:200]}...\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Obsidian")

    @mcp.tool(name="obsidian_list_directory", annotations={"readOnlyHint": True})
    async def obsidian_list_directory(params: "ObsidianDirInput") -> str:
        """List files and folders in an Obsidian vault directory."""
        if not cfg.is_configured("obsidian_url", "obsidian_api_key"):
            return "Error: Obsidian REST API not configured."
        try:
            # Obsidian's directory listing requires a trailing slash on the
            # path. For the root vault, an empty path becomes just "/vault/" —
            # the previous code produced "/vault//" by appending an extra slash.
            path = params.path.strip("/")
            url = f"{cfg.obsidian_url}/vault/" if not path else _vault_url(path) + "/"
            async with _obs_client() as client:
                r = await client.get(url, headers=_obs_headers())
                r.raise_for_status()
                data = r.json()

            files = data.get("files", [])
            result = f"## Obsidian: {params.path or 'vault root'}\n\n"
            dirs = [f for f in files if f.endswith("/")]
            notes = [f for f in files if not f.endswith("/")]
            for d in sorted(dirs):
                result += f"📁 {d}\n"
            for n in sorted(notes):
                result += f"📄 {n}\n"
            result += f"\n{len(dirs)} folders, {len(notes)} files"
            return result
        except Exception as e:
            return _handle_error(e, "Obsidian")

    @mcp.tool(name="obsidian_get_daily_note", annotations={"readOnlyHint": True})
    async def obsidian_get_daily_note() -> str:
        """Get today's Obsidian daily note content."""
        if not cfg.is_configured("obsidian_url", "obsidian_api_key"):
            return "Error: Obsidian REST API not configured."
        try:
            async with _obs_client() as client:
                r = await client.get(
                    f"{cfg.obsidian_url}/periodic/daily/",
                    headers=_obs_headers()
                )
                if r.status_code == 404:
                    return "No daily note exists for today yet. Create one in Obsidian first."
                r.raise_for_status()
                content = r.text

            from datetime import datetime
            from zoneinfo import ZoneInfo
            today = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")
            return f"## Daily Note: {today}\n\n{content}"
        except Exception as e:
            return _handle_error(e, "Obsidian")

    @mcp.tool(name="obsidian_create_daily_note", annotations={"readOnlyHint": False})
    async def obsidian_create_daily_note() -> str:
        """Create today's Obsidian daily note using the configured template."""
        if not cfg.is_configured("obsidian_url", "obsidian_api_key"):
            return "Error: Obsidian REST API not configured."
        try:
            async with _obs_client() as client:
                r = await client.post(
                    f"{cfg.obsidian_url}/periodic/daily/",
                    headers=_obs_headers()
                )
                r.raise_for_status()
            return "✓ Daily note created for today."
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                return "Daily note already exists for today."
            return _handle_error(e, "Obsidian")
        except Exception as e:
            return _handle_error(e, "Obsidian")


# ─── INPUT MODELS ─────────────────────────────────────────────────────────────

class ObsidianNoteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(..., description="Note path relative to vault root e.g. 'Brain FULL/Brain/00-dashboard/current-focus.md'", min_length=1)

class ObsidianWriteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(..., description="Note path relative to vault root", min_length=1)
    content: str = Field(..., description="Full markdown content to write", min_length=1)

class ObsidianAppendInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(..., description="Note path relative to vault root", min_length=1)
    content: str = Field(..., description="Content to append to the note", min_length=1)

class ObsidianSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., description="Search query", min_length=1, max_length=200)
    limit: int = Field(default=10, description="Max results", ge=1, le=50)

class ObsidianDirInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(default="", description="Directory path relative to vault root. Leave empty for root.")

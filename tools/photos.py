"""
tools/photos.py — Photo management tools.
Covers: Immich (search, albums, memories, people)
"""

import httpx
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import fmt_json, TIMEOUT, _handle_error


def register_photo_tools(mcp: FastMCP):

    def _immich_headers() -> dict:
        return {"x-api-key": cfg.immich_api_key, "Accept": "application/json"}

    # ─── IMMICH ───────────────────────────────────────────────────────────────

    class ImmichSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Smart search query e.g. 'sunset at beach', 'birthday party 2024', 'Hamburg'", min_length=1, max_length=500)
        limit: int = Field(default=10, description="Max results to return", ge=1, le=50)

    @mcp.tool(
        name="immich_search",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def immich_search(params: ImmichSearchInput) -> str:
        """Search Immich photo library using semantic/smart search (CLIP ML model).

        Can search by description, location, objects in photos, people, events.
        Examples: 'cats on the couch', 'Hamburg Hafen', 'Christmas 2023'
        """
        if not cfg.is_configured("immich_url", "immich_api_key"):
            return "Error: Immich not configured. Set IMMICH_URL and IMMICH_API_KEY in .env"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{cfg.immich_url}/api/search/smart",
                    headers=_immich_headers(),
                    json={"query": params.query, "limit": params.limit}
                )
                r.raise_for_status()
                data = r.json()

            assets = data.get("assets", {}).get("items", [])
            if not assets:
                return f"No photos found for '{params.query}'"

            result = f"## Immich Search: '{params.query}' ({len(assets)} results)\n\n"
            for asset in assets:
                date = asset.get("localDateTime", "")[:10]
                city = asset.get("exifInfo", {}).get("city", "")
                country = asset.get("exifInfo", {}).get("country", "")
                location = f" — {city}, {country}" if city else ""
                result += f"- **{date}**{location} | ID: `{asset.get('id')}`\n"
                result += f"  View: {cfg.immich_url}/photos/{asset.get('id')}\n"

            return result
        except Exception as e:
            return _handle_error(e, "Immich")

    @mcp.tool(
        name="immich_list_albums",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def immich_list_albums() -> str:
        """List all Immich photo albums with asset counts."""
        if not cfg.is_configured("immich_url", "immich_api_key"):
            return "Error: Immich not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{cfg.immich_url}/api/albums",
                    headers=_immich_headers()
                )
                r.raise_for_status()
                albums = r.json()

            if not albums:
                return "No albums found in Immich."

            result = f"## Immich Albums ({len(albums)} total)\n\n"
            for album in sorted(albums, key=lambda x: x.get("albumName", "")):
                result += f"- **{album.get('albumName')}** — {album.get('assetCount', 0)} photos"
                last = album.get("lastModifiedAssetTimestamp", "")
                if last:
                    result += f" | Last: {last[:10]}"
                result += f" | ID: `{album.get('id')}`\n"
            return result
        except Exception as e:
            return _handle_error(e, "Immich")

    class ImmichAlbumInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        album_id: str = Field(..., description="Album ID from immich_list_albums", min_length=1)

    @mcp.tool(
        name="immich_get_album",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def immich_get_album(params: ImmichAlbumInput) -> str:
        """Get photos in a specific Immich album."""
        if not cfg.is_configured("immich_url", "immich_api_key"):
            return "Error: Immich not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{cfg.immich_url}/api/albums/{params.album_id}",
                    headers=_immich_headers()
                )
                r.raise_for_status()
                album = r.json()

            assets = album.get("assets", [])
            result = f"## Album: {album.get('albumName')} ({len(assets)} photos)\n\n"
            for asset in assets[:20]:
                date = asset.get("localDateTime", "")[:10]
                result += f"- {date} | `{asset.get('id')}` — {cfg.immich_url}/photos/{asset.get('id')}\n"
            if len(assets) > 20:
                result += f"\n...and {len(assets) - 20} more photos\n"
            return result
        except Exception as e:
            return _handle_error(e, "Immich")

    @mcp.tool(
        name="immich_get_memories",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def immich_get_memories() -> str:
        """Get today's Immich memories — photos from this day in previous years."""
        if not cfg.is_configured("immich_url", "immich_api_key"):
            return "Error: Immich not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{cfg.immich_url}/api/memories",
                    headers=_immich_headers()
                )
                # /api/memories appeared in Immich ~v1.92. Older instances
                # 404 here; return a clear message instead of a generic crash.
                if r.status_code == 404:
                    return "Memories endpoint not available — your Immich version is older than v1.92, or the feature is disabled."
                r.raise_for_status()
                memories = r.json()

            if not memories:
                return "No memories for today."

            result = "## Immich Memories — Today\n\n"
            for memory in memories:
                year = memory.get("data", {}).get("year", "?")
                assets = memory.get("assets", [])
                result += f"### {year} ({len(assets)} photos)\n"
                for asset in assets[:5]:
                    result += f"  - {cfg.immich_url}/photos/{asset.get('id')}\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Immich")

    @mcp.tool(
        name="immich_list_people",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def immich_list_people() -> str:
        """List all recognized people/faces in Immich library."""
        if not cfg.is_configured("immich_url", "immich_api_key"):
            return "Error: Immich not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{cfg.immich_url}/api/people",
                    headers=_immich_headers()
                )
                r.raise_for_status()
                data = r.json()

            people = data.get("people", [])
            if not people:
                return "No people recognized in Immich library."

            result = f"## Immich People ({len(people)} recognized)\n\n"
            for person in people:
                name = person.get("name") or "(unnamed)"
                count = person.get("assetCount", 0)
                result += f"- **{name}** — {count} photos | ID: `{person.get('id')}`\n"
            return result
        except Exception as e:
            return _handle_error(e, "Immich")

    class ImmichPersonSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        person_id: str = Field(..., description="Person ID from immich_list_people", min_length=1)
        limit: int = Field(default=10, description="Max photos to return", ge=1, le=50)

    @mcp.tool(
        name="immich_search_by_person",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def immich_search_by_person(params: ImmichPersonSearchInput) -> str:
        """Get photos of a specific person from Immich."""
        if not cfg.is_configured("immich_url", "immich_api_key"):
            return "Error: Immich not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{cfg.immich_url}/api/search/metadata",
                    headers=_immich_headers(),
                    json={"personIds": [params.person_id], "size": params.limit}
                )
                r.raise_for_status()
                data = r.json()

            assets = data.get("assets", {}).get("items", [])
            if not assets:
                return "No photos found for this person."

            result = f"## Photos of person ({len(assets)} results)\n\n"
            for asset in assets:
                date = asset.get("localDateTime", "")[:10]
                result += f"- {date} | {cfg.immich_url}/photos/{asset.get('id')}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Immich")

    class ImmichDateSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        year: Optional[int] = Field(default=None, description="Filter by year e.g. 2023")
        month: Optional[int] = Field(default=None, description="Filter by month 1-12")
        city: Optional[str] = Field(default=None, description="Filter by city name")
        limit: int = Field(default=20, description="Max results", ge=1, le=100)

    @mcp.tool(
        name="immich_search_by_metadata",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def immich_search_by_metadata(params: ImmichDateSearchInput) -> str:
        """Search Immich photos by date, year, month, or location."""
        if not cfg.is_configured("immich_url", "immich_api_key"):
            return "Error: Immich not configured."
        try:
            body: dict = {"size": params.limit}
            if params.year:
                body["takenAfter"] = f"{params.year}-01-01T00:00:00.000Z"
                body["takenBefore"] = f"{params.year}-12-31T23:59:59.999Z"
            if params.city:
                body["city"] = params.city

            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{cfg.immich_url}/api/search/metadata",
                    headers=_immich_headers(),
                    json=body
                )
                r.raise_for_status()
                data = r.json()

            assets = data.get("assets", {}).get("items", [])
            if not assets:
                return "No photos found matching those criteria."

            result = f"## Immich Metadata Search ({len(assets)} results)\n\n"
            for asset in assets:
                date = asset.get("localDateTime", "")[:10]
                exif = asset.get("exifInfo", {})
                city = exif.get("city", "")
                location = f" — {city}" if city else ""
                result += f"- {date}{location} | {cfg.immich_url}/photos/{asset.get('id')}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Immich")

"""
tools/media.py — Media server tools.
Covers: Jellyfin, Sonarr, Radarr, Lidarr, Jellyseerr, qBittorrent
"""

import json
from contextlib import asynccontextmanager

import httpx
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import arr_get, arr_post, arr_delete, lidarr_get, lidarr_post, fmt_json, fmt_size, TIMEOUT, _handle_error


def register_media_tools(mcp: FastMCP):

    # ─── JELLYFIN ─────────────────────────────────────────────────────────────

    class JellyfinSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search term for movies, shows, music", min_length=1, max_length=200)
        media_type: Optional[str] = Field(default=None, description="Filter by type: 'Movie', 'Series', 'Audio', 'MusicAlbum'")
        limit: int = Field(default=10, description="Max results to return", ge=1, le=50)

    @mcp.tool(
        name="jellyfin_search",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def jellyfin_search(params: JellyfinSearchInput) -> str:
        """Search the Jellyfin media library for movies, shows, or music.

        Returns a list of matching items with title, type, year, and overview.
        """
        if not cfg.is_configured("jellyfin_url", "jellyfin_api_key"):
            return "Error: Jellyfin not configured. Set JELLYFIN_URL and JELLYFIN_API_KEY in .env"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                params_dict = {
                    "searchTerm": params.query,
                    "limit": params.limit,
                    "recursive": True,
                    "includeItemTypes": params.media_type or "Movie,Series,Audio,MusicAlbum",
                    "fields": "Overview,Genres,CommunityRating",
                }
                r = await client.get(
                    f"{cfg.jellyfin_url}/Items",
                    headers={"X-Emby-Token": cfg.jellyfin_api_key},
                    params=params_dict
                )
                r.raise_for_status()
                data = r.json()
                items = data.get("Items", [])
                if not items:
                    return f"No results found for '{params.query}'"
                result = f"## Jellyfin Search: '{params.query}'\n\n"
                for item in items:
                    result += f"**{item.get('Name', 'Unknown')}** ({item.get('ProductionYear', '?')})\n"
                    result += f"  Type: {item.get('Type')} | Rating: {item.get('CommunityRating', 'N/A')}\n"
                    overview = item.get('Overview', '')
                    if overview:
                        result += f"  {overview[:150]}{'...' if len(overview) > 150 else ''}\n"
                    result += f"  ID: `{item.get('Id')}`\n\n"
                return result
        except Exception as e:
            return _handle_error(e, "Jellyfin")

    class JellyfinRecentInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        limit: int = Field(default=10, description="Number of recent items", ge=1, le=50)
        media_type: Optional[str] = Field(default=None, description="Filter: 'Movie', 'Series', 'Audio'")

    @mcp.tool(
        name="jellyfin_recently_added",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def jellyfin_recently_added(params: JellyfinRecentInput) -> str:
        """Get recently added items in Jellyfin library."""
        if not cfg.is_configured("jellyfin_url", "jellyfin_api_key", "jellyfin_user_id"):
            return "Error: Jellyfin not configured. Set JELLYFIN_URL, JELLYFIN_API_KEY, JELLYFIN_USER_ID in .env"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{cfg.jellyfin_url}/Users/{cfg.jellyfin_user_id}/Items/Latest",
                    headers={"X-Emby-Token": cfg.jellyfin_api_key},
                    params={
                        "limit": params.limit,
                        "includeItemTypes": params.media_type or "Movie,Series,Audio",
                    }
                )
                r.raise_for_status()
                payload = r.json()
                # /Users/{id}/Items/Latest returns a bare list, but if Jellyfin
                # is misconfigured / behind a proxy it sometimes returns
                # {"Items": [...]}. Handle both shapes.
                if isinstance(payload, dict):
                    items = payload.get("Items") or payload.get("items") or []
                elif isinstance(payload, list):
                    items = payload
                else:
                    items = []
                if not items:
                    return "No recently added items found."
                result = "## Recently Added to Jellyfin\n\n"
                for item in items:
                    result += f"**{item.get('Name')}** ({item.get('ProductionYear', '?')}) — {item.get('Type')}\n"
                return result
        except Exception as e:
            return _handle_error(e, "Jellyfin")

    # ─── SONARR ───────────────────────────────────────────────────────────────

    class SonarrSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="TV show name to search for", min_length=1, max_length=200)

    @mcp.tool(
        name="sonarr_search_show",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def sonarr_search_show(params: SonarrSearchInput) -> str:
        """Search for a TV show in Sonarr's lookup (TVDB). Returns shows you can add."""
        if not cfg.is_configured("sonarr_url", "sonarr_api_key"):
            return "Error: Sonarr not configured."
        try:
            data = await arr_get(cfg.sonarr_url, cfg.sonarr_api_key, "series/lookup", {"term": params.query})
            if not data:
                return f"No shows found for '{params.query}'"
            result = f"## Sonarr Lookup: '{params.query}'\n\n"
            for show in data[:8]:
                result += f"**{show.get('title')}** ({show.get('year', '?')})\n"
                result += f"  Status: {show.get('status')} | Network: {show.get('network', 'N/A')}\n"
                result += f"  TVDB ID: `{show.get('tvdbId')}` | Seasons: {show.get('seasonCount', '?')}\n"
                overview = show.get('overview', '')
                if overview:
                    result += f"  {overview[:120]}...\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Sonarr")

    @mcp.tool(
        name="sonarr_list_series",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def sonarr_list_series() -> str:
        """List all TV series currently monitored in Sonarr."""
        if not cfg.is_configured("sonarr_url", "sonarr_api_key"):
            return "Error: Sonarr not configured."
        try:
            data = await arr_get(cfg.sonarr_url, cfg.sonarr_api_key, "series")
            if not data:
                return "No series in Sonarr."
            result = f"## Sonarr Series ({len(data)} total)\n\n"
            for show in sorted(data, key=lambda x: x.get('title', '')):
                monitored = "✓" if show.get('monitored') else "✗"
                result += f"{monitored} **{show.get('title')}** ({show.get('year', '?')}) — {show.get('status')}\n"
                stats = show.get('statistics', {})
                result += f"  Episodes: {stats.get('episodeFileCount', 0)}/{stats.get('totalEpisodeCount', 0)} | Size: {fmt_size(stats.get('sizeOnDisk', 0))}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Sonarr")

    class SonarrAddInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        tvdb_id: int = Field(..., description="TVDB ID from sonarr_search_show", ge=1)
        title: str = Field(..., description="Show title", min_length=1)
        quality_profile_id: int = Field(default=1, description="Quality profile ID (1=default)")
        monitored: bool = Field(default=True, description="Monitor for new episodes")
        search_on_add: bool = Field(default=True, description="Search for episodes immediately after adding")

    @mcp.tool(
        name="sonarr_add_series",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def sonarr_add_series(params: SonarrAddInput) -> str:
        """Add a TV series to Sonarr for monitoring and downloading."""
        if not cfg.is_configured("sonarr_url", "sonarr_api_key"):
            return "Error: Sonarr not configured."
        try:
            root_folders = await arr_get(cfg.sonarr_url, cfg.sonarr_api_key, "rootfolder")
            if not root_folders:
                return "Error: No root folders configured in Sonarr."
            root_path = root_folders[0]["path"]
            body = {
                "tvdbId": params.tvdb_id,
                "title": params.title,
                "qualityProfileId": params.quality_profile_id,
                "monitored": params.monitored,
                "rootFolderPath": root_path,
                "addOptions": {"searchForMissingEpisodes": params.search_on_add}
            }
            result = await arr_post(cfg.sonarr_url, cfg.sonarr_api_key, "series", body)
            return f"✓ Added '{result.get('title')}' to Sonarr. ID: {result.get('id')}"
        except Exception as e:
            return _handle_error(e, "Sonarr")

    @mcp.tool(
        name="sonarr_queue",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def sonarr_queue() -> str:
        """Get the current Sonarr download queue."""
        if not cfg.is_configured("sonarr_url", "sonarr_api_key"):
            return "Error: Sonarr not configured."
        try:
            data = await arr_get(cfg.sonarr_url, cfg.sonarr_api_key, "queue")
            records = data.get("records", [])
            if not records:
                return "Sonarr queue is empty."
            result = f"## Sonarr Queue ({len(records)} items)\n\n"
            for item in records:
                result += f"**{item.get('title', 'Unknown')}**\n"
                result += f"  Status: {item.get('status')} | Size: {fmt_size(item.get('size', 0))}\n"
                sizeleft = item.get('sizeleft', 0)
                result += f"  Remaining: {fmt_size(sizeleft)}\n\n"
            return result
        except Exception as e:
            return _handle_error(e, "Sonarr")

    @mcp.tool(
        name="sonarr_calendar",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def sonarr_calendar() -> str:
        """Get upcoming episode releases in the next 7 days from Sonarr."""
        if not cfg.is_configured("sonarr_url", "sonarr_api_key"):
            return "Error: Sonarr not configured."
        try:
            from datetime import datetime, timedelta, timezone
            # datetime.utcnow() is deprecated in 3.12 (and naive); always
            # produce a tz-aware ISO 8601 string with 'Z' suffix that Sonarr
            # parses as UTC.
            now = datetime.now(timezone.utc).replace(microsecond=0)
            start = now.isoformat().replace("+00:00", "Z")
            end = (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")
            data = await arr_get(cfg.sonarr_url, cfg.sonarr_api_key, "calendar", {"start": start, "end": end})
            if not data:
                return "No upcoming episodes in the next 7 days."
            result = "## Upcoming Episodes (next 7 days)\n\n"
            for ep in data:
                result += f"**{ep.get('series', {}).get('title', 'Unknown')}** — S{ep.get('seasonNumber', '?'):02d}E{ep.get('episodeNumber', '?'):02d}\n"
                result += f"  '{ep.get('title')}' | Airs: {ep.get('airDateUtc', 'Unknown')[:10]}\n\n"
            return result
        except Exception as e:
            return _handle_error(e, "Sonarr")

    @mcp.tool(
        name="sonarr_missing",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def sonarr_missing() -> str:
        """Get episodes that are wanted but not yet downloaded in Sonarr."""
        if not cfg.is_configured("sonarr_url", "sonarr_api_key"):
            return "Error: Sonarr not configured."
        try:
            data = await arr_get(cfg.sonarr_url, cfg.sonarr_api_key, "wanted/missing", {"pageSize": 20})
            records = data.get("records", [])
            if not records:
                return "No missing episodes — you're all caught up!"
            result = f"## Missing Episodes ({data.get('totalRecords', 0)} total, showing {len(records)})\n\n"
            for ep in records:
                result += f"**{ep.get('series', {}).get('title')}** — S{ep.get('seasonNumber', '?'):02d}E{ep.get('episodeNumber', '?'):02d}: '{ep.get('title')}'\n"
                result += f"  Aired: {ep.get('airDateUtc', 'Unknown')[:10]}\n\n"
            return result
        except Exception as e:
            return _handle_error(e, "Sonarr")

    # ─── RADARR ───────────────────────────────────────────────────────────────

    class RadarrSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Movie name to search for", min_length=1, max_length=200)

    @mcp.tool(
        name="radarr_search_movie",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def radarr_search_movie(params: RadarrSearchInput) -> str:
        """Search for a movie in Radarr's lookup (TMDB). Returns movies you can add."""
        if not cfg.is_configured("radarr_url", "radarr_api_key"):
            return "Error: Radarr not configured."
        try:
            data = await arr_get(cfg.radarr_url, cfg.radarr_api_key, "movie/lookup", {"term": params.query})
            if not data:
                return f"No movies found for '{params.query}'"
            result = f"## Radarr Lookup: '{params.query}'\n\n"
            for movie in data[:8]:
                result += f"**{movie.get('title')}** ({movie.get('year', '?')})\n"
                result += f"  Status: {movie.get('status')} | Runtime: {movie.get('runtime', '?')} min\n"
                result += f"  TMDB ID: `{movie.get('tmdbId')}` | IMDB: `{movie.get('imdbId', 'N/A')}`\n"
                overview = movie.get('overview', '')
                if overview:
                    result += f"  {overview[:120]}...\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "Radarr")

    @mcp.tool(
        name="radarr_list_movies",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def radarr_list_movies() -> str:
        """List all movies in Radarr library with download status."""
        if not cfg.is_configured("radarr_url", "radarr_api_key"):
            return "Error: Radarr not configured."
        try:
            data = await arr_get(cfg.radarr_url, cfg.radarr_api_key, "movie")
            if not data:
                return "No movies in Radarr."
            downloaded = [m for m in data if m.get('hasFile')]
            missing = [m for m in data if not m.get('hasFile') and m.get('monitored')]
            result = f"## Radarr Library ({len(data)} movies)\n\n"
            result += f"Downloaded: {len(downloaded)} | Missing: {len(missing)}\n\n"
            if missing:
                result += "### Missing (monitored)\n"
                for m in sorted(missing, key=lambda x: x.get('title', ''))[:20]:
                    result += f"  - **{m.get('title')}** ({m.get('year', '?')})\n"
            return result
        except Exception as e:
            return _handle_error(e, "Radarr")

    class RadarrAddInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        tmdb_id: int = Field(..., description="TMDB ID from radarr_search_movie", ge=1)
        title: str = Field(..., description="Movie title", min_length=1)
        quality_profile_id: int = Field(default=1, description="Quality profile ID")
        monitored: bool = Field(default=True, description="Monitor for better quality")
        search_on_add: bool = Field(default=True, description="Search immediately after adding")

    @mcp.tool(
        name="radarr_add_movie",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def radarr_add_movie(params: RadarrAddInput) -> str:
        """Add a movie to Radarr for downloading."""
        if not cfg.is_configured("radarr_url", "radarr_api_key"):
            return "Error: Radarr not configured."
        try:
            root_folders = await arr_get(cfg.radarr_url, cfg.radarr_api_key, "rootfolder")
            if not root_folders:
                return "Error: No root folders configured in Radarr."
            root_path = root_folders[0]["path"]
            body = {
                "tmdbId": params.tmdb_id,
                "title": params.title,
                "qualityProfileId": params.quality_profile_id,
                "monitored": params.monitored,
                "rootFolderPath": root_path,
                "addOptions": {"searchForMovie": params.search_on_add}
            }
            result = await arr_post(cfg.radarr_url, cfg.radarr_api_key, "movie", body)
            return f"✓ Added '{result.get('title')}' to Radarr. ID: {result.get('id')}"
        except Exception as e:
            return _handle_error(e, "Radarr")

    @mcp.tool(
        name="radarr_queue",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def radarr_queue() -> str:
        """Get the current Radarr download queue."""
        if not cfg.is_configured("radarr_url", "radarr_api_key"):
            return "Error: Radarr not configured."
        try:
            data = await arr_get(cfg.radarr_url, cfg.radarr_api_key, "queue")
            records = data.get("records", [])
            if not records:
                return "Radarr queue is empty."
            result = f"## Radarr Queue ({len(records)} items)\n\n"
            for item in records:
                result += f"**{item.get('title', 'Unknown')}**\n"
                result += f"  Status: {item.get('status')} | Size: {fmt_size(item.get('size', 0))} | Remaining: {fmt_size(item.get('sizeleft', 0))}\n\n"
            return result
        except Exception as e:
            return _handle_error(e, "Radarr")

    # ─── LIDARR ───────────────────────────────────────────────────────────────

    class LidarrSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Artist or album name to search", min_length=1, max_length=200)

    @mcp.tool(
        name="lidarr_search_artist",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def lidarr_search_artist(params: LidarrSearchInput) -> str:
        """Search for an artist or album in Lidarr's lookup."""
        if not cfg.is_configured("lidarr_url", "lidarr_api_key"):
            return "Error: Lidarr not configured."
        try:
            raw = await lidarr_get(cfg.lidarr_url, cfg.lidarr_api_key, "search", {"term": params.query})
            items: list = []
            if isinstance(raw, list):
                items = raw
            elif isinstance(raw, dict):
                items = raw.get("data") or raw.get("results") or raw.get("item") or []
                if isinstance(items, dict):
                    items = [items]

            result = f"## Lidarr Lookup: '{params.query}'\n\n"
            n = 0
            for entry in items:
                if n >= 8:
                    break
                row = entry.get("artist") if isinstance(entry, dict) and "artist" in entry else entry
                if not isinstance(row, dict):
                    continue
                name = row.get("artistName") or row.get("name") or entry.get("name")
                genres = row.get("genres") or []
                mb = row.get("foreignArtistId") or row.get("foreignId")
                result += f"**{name or '?'}**\n"
                result += f"  Genre: {', '.join(str(x) for x in genres[:3]) or 'N/A'}\n"
                result += f"  ID: `{mb}`\n\n"
                n += 1
            if n == 0:
                return f"No artists found for '{params.query}'"
            return result
        except Exception as e:
            return _handle_error(e, "Lidarr")

    @mcp.tool(
        name="lidarr_list_artists",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def lidarr_list_artists() -> str:
        """List all artists monitored in Lidarr."""
        if not cfg.is_configured("lidarr_url", "lidarr_api_key"):
            return "Error: Lidarr not configured."
        try:
            data = await lidarr_get(cfg.lidarr_url, cfg.lidarr_api_key, "artist")
            if not data:
                return "No artists in Lidarr."
            result = f"## Lidarr Artists ({len(data)} total)\n\n"
            for artist in sorted(data, key=lambda x: x.get('artistName', '')):
                monitored = "✓" if artist.get('monitored') else "✗"
                stats = artist.get('statistics', {})
                result += f"{monitored} **{artist.get('artistName')}** — {stats.get('albumCount', 0)} albums, {stats.get('trackFileCount', 0)} tracks\n"
            return result
        except Exception as e:
            return _handle_error(e, "Lidarr")

    @mcp.tool(
        name="lidarr_queue",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def lidarr_queue() -> str:
        """Get the current Lidarr download queue."""
        if not cfg.is_configured("lidarr_url", "lidarr_api_key"):
            return "Error: Lidarr not configured."
        try:
            data = await lidarr_get(cfg.lidarr_url, cfg.lidarr_api_key, "queue")
            records = data.get("records", [])
            if not records:
                return "Lidarr queue is empty."
            result = f"## Lidarr Queue ({len(records)} items)\n\n"
            for item in records:
                result += f"**{item.get('title', 'Unknown')}** — {item.get('status')} | {fmt_size(item.get('size', 0))}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Lidarr")

    # ─── JELLYSEERR ───────────────────────────────────────────────────────────

    class JellyseerrRequestInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        media_type: str = Field(..., description="Must be exactly 'movie' or 'tv' (lowercase)")
        media_id: int = Field(..., description="TMDB ID for movies, TVDB ID for TV shows", ge=1)
        seasons: Optional[list[int]] = Field(default=None, description="For TV: specific season numbers to request. Leave empty for all seasons.")

    @mcp.tool(
        name="jellyseerr_request",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def jellyseerr_request(params: JellyseerrRequestInput) -> str:
        """Request a movie or TV show via Jellyseerr.

        media_type must be exactly 'movie' or 'tv'. Anything else (e.g. 'Movie',
        'show', 'series') will be rejected by Jellyseerr with HTTP 400.
        """
        if not cfg.is_configured("jellyseerr_url", "jellyseerr_api_key"):
            return "Error: Jellyseerr not configured."
        mt = (params.media_type or "").strip().lower()
        if mt not in ("movie", "tv"):
            return f"Error: media_type must be 'movie' or 'tv' (got {params.media_type!r})."
        try:
            body = {"mediaType": mt, "mediaId": params.media_id}
            if mt == "tv" and params.seasons:
                body["seasons"] = params.seasons
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{cfg.jellyseerr_url}/api/v1/request",
                    headers={"X-Api-Key": cfg.jellyseerr_api_key},
                    json=body
                )
                r.raise_for_status()
                data = r.json()
                return f"✓ Request submitted. ID: {data.get('id')} | Status: {data.get('status')}"
        except Exception as e:
            return _handle_error(e, "Jellyseerr")

    @mcp.tool(
        name="jellyseerr_requests",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def jellyseerr_requests() -> str:
        """List recent media requests in Jellyseerr."""
        if not cfg.is_configured("jellyseerr_url", "jellyseerr_api_key"):
            return "Error: Jellyseerr not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"{cfg.jellyseerr_url}/api/v1/request",
                    headers={"X-Api-Key": cfg.jellyseerr_api_key},
                    params={"take": 20, "sort": "modified"}
                )
                r.raise_for_status()
                data = r.json()
                results = data.get("results", [])
                if not results:
                    return "No requests found."
                result = f"## Jellyseerr Requests ({data.get('pageInfo', {}).get('results', 0)} total)\n\n"
                status_map = {1: "Pending", 2: "Approved", 3: "Declined", 4: "Available", 5: "Processing"}
                for req in results:
                    media = req.get('media', {})
                    status = status_map.get(req.get('status'), 'Unknown')
                    result += f"**{media.get('mediaType', '?').upper()}** | Status: {status}\n"
                    result += f"  TMDB: {media.get('tmdbId')} | Requested: {req.get('createdAt', '')[:10]}\n\n"
                return result
        except Exception as e:
            return _handle_error(e, "Jellyseerr")

    # ─── QBITTORRENT ──────────────────────────────────────────────────────────

    @asynccontextmanager
    async def _qbt_client():
        base = cfg.qbittorrent_url.rstrip("/")
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            login = await client.post(
                f"{base}/api/v2/auth/login",
                data={"username": cfg.qbittorrent_username, "password": cfg.qbittorrent_password},
            )
            login.raise_for_status()
            yield client, base

    @mcp.tool(
        name="qbittorrent_list",
        annotations={"readOnlyHint": True, "destructiveHint": False}
    )
    async def qbittorrent_list() -> str:
        """List all active torrents in qBittorrent with progress and speed."""
        if not cfg.is_configured("qbittorrent_url", "qbittorrent_username", "qbittorrent_password"):
            return "Error: qBittorrent not configured. Set QBITTORRENT_URL, username, and password in .env"
        try:
            async with _qbt_client() as (client, base):
                r = await client.get(f"{base}/api/v2/torrents/info")
                r.raise_for_status()
                torrents = r.json()
                if not torrents:
                    return "No active torrents."
                result = f"## qBittorrent ({len(torrents)} torrents)\n\n"
                for t in torrents:
                    progress = t.get("progress", 0) * 100
                    result += f"**{t.get('name', 'Unknown')}**\n"
                    result += f"  State: {t.get('state')} | Progress: {progress:.1f}% | Size: {fmt_size(t.get('size', 0))}\n"
                    dl_speed = t.get("dlspeed", 0)
                    if dl_speed > 0:
                        result += f"  Speed: ↓{fmt_size(dl_speed)}/s\n"
                    result += "\n"
                return result
        except Exception as e:
            return _handle_error(e, "qBittorrent")

    class QbtTorrentInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        hash: str = Field(..., description="Torrent hash (from qbittorrent_list)", min_length=1)

    @mcp.tool(
        name="qbittorrent_pause",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def qbittorrent_pause(params: QbtTorrentInput) -> str:
        """Pause a torrent in qBittorrent by hash."""
        if not cfg.is_configured("qbittorrent_url", "qbittorrent_username", "qbittorrent_password"):
            return "Error: qBittorrent not configured. Set QBITTORRENT_URL, username, and password in .env"
        try:
            async with _qbt_client() as (client, base):
                r = await client.post(f"{base}/api/v2/torrents/pause", data={"hashes": params.hash})
                r.raise_for_status()
                return f"✓ Torrent paused: {params.hash}"
        except Exception as e:
            return _handle_error(e, "qBittorrent")

    @mcp.tool(
        name="qbittorrent_resume",
        annotations={"readOnlyHint": False, "destructiveHint": False}
    )
    async def qbittorrent_resume(params: QbtTorrentInput) -> str:
        """Resume a paused torrent in qBittorrent by hash."""
        if not cfg.is_configured("qbittorrent_url", "qbittorrent_username", "qbittorrent_password"):
            return "Error: qBittorrent not configured. Set QBITTORRENT_URL, username, and password in .env"
        try:
            async with _qbt_client() as (client, base):
                r = await client.post(f"{base}/api/v2/torrents/resume", data={"hashes": params.hash})
                r.raise_for_status()
                return f"✓ Torrent resumed: {params.hash}"
        except Exception as e:
            return _handle_error(e, "qBittorrent")

    @mcp.tool(
        name="qbittorrent_delete",
        annotations={"readOnlyHint": False, "destructiveHint": True}
    )
    async def qbittorrent_delete(params: QbtTorrentInput) -> str:
        """Delete a torrent from qBittorrent (keeps downloaded files)."""
        if not cfg.is_configured("qbittorrent_url", "qbittorrent_username", "qbittorrent_password"):
            return "Error: qBittorrent not configured. Set QBITTORRENT_URL, username, and password in .env"
        try:
            async with _qbt_client() as (client, base):
                r = await client.post(
                    f"{base}/api/v2/torrents/delete",
                    data={"hashes": params.hash, "deleteFiles": "false"},
                )
                r.raise_for_status()
                return f"✓ Torrent removed (files kept): {params.hash}"
        except Exception as e:
            return _handle_error(e, "qBittorrent")

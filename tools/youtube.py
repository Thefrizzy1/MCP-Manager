"""
tools/youtube.py — YouTube Data API v3 tools (real calls, API-key auth).

Search videos, look up a channel's stats, inspect a video, and list what's
trending. Every call hits https://www.googleapis.com/youtube/v3 with
YOUTUBE_API_KEY. Follows the service contract: real HTTP, structured output,
graceful "not configured" and error messages (never fake success).
"""
import httpx
from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT, _handle_error

API = "https://www.googleapis.com/youtube/v3"
_NOT_CONFIGURED = (
    "YouTube not configured. Set **YOUTUBE_API_KEY** in `.env` — create an API "
    "key in Google Cloud Console and enable **YouTube Data API v3**."
)


def _fmt_count(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v or "—")
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}".replace(".0", "")
    return str(n)


async def _get(path: str, params: dict) -> dict:
    params = {**params, "key": cfg.youtube_api_key}
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.get(f"{API}/{path}", params=params, headers={"User-Agent": "PlutusMCP/1.0"})
        r.raise_for_status()
        return r.json()


def register_youtube_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class SearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search terms", min_length=1, max_length=300)
        max_results: int = Field(default=5, description="Max results (1–25)", ge=1, le=25)

    @mcp.tool(name="youtube_search", annotations={"readOnlyHint": True})
    async def youtube_search(params: SearchInput) -> str:
        """Search YouTube videos by keyword. Returns titles, channels, and links."""
        if not cfg.youtube_api_key:
            return _NOT_CONFIGURED
        try:
            data = await _get("search", {
                "part": "snippet", "type": "video", "q": params.query, "maxResults": params.max_results,
            })
            items = data.get("items") or []
            if not items:
                return f"## YouTube search: '{params.query}'\n\nNo results."
            lines = [f"## YouTube search: '{params.query}'\n"]
            for it in items:
                sn = it.get("snippet", {})
                vid = it.get("id", {}).get("videoId", "")
                lines.append(
                    f"- **{sn.get('title', '')}** — {sn.get('channelTitle', '')}\n"
                    f"  https://youtu.be/{vid}  ({sn.get('publishedAt', '')[:10]})"
                )
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "YouTube search")

    class ChannelInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        channel: str = Field(..., description="Channel @handle, channel ID (UC…), or username", min_length=1)

    @mcp.tool(name="youtube_channel", annotations={"readOnlyHint": True})
    async def youtube_channel(params: ChannelInput) -> str:
        """Channel stats — subscribers, total views, video count — by @handle or ID."""
        if not cfg.youtube_api_key:
            return _NOT_CONFIGURED
        try:
            c = params.channel.strip()
            key = {"part": "snippet,statistics"}
            if c.startswith("@"):
                key["forHandle"] = c
            elif c.startswith("UC") and len(c) >= 20:
                key["id"] = c
            else:
                key["forHandle"] = "@" + c.lstrip("@")
            data = await _get("channels", key)
            items = data.get("items") or []
            if not items:
                return f"No channel found for '{c}'."
            it = items[0]
            sn, st = it.get("snippet", {}), it.get("statistics", {})
            desc = (sn.get("description") or "").strip().replace("\n", " ")
            return (
                f"## {sn.get('title', '')}\n\n"
                f"**Subscribers:** {_fmt_count(st.get('subscriberCount'))}\n"
                f"**Total views:** {_fmt_count(st.get('viewCount'))}\n"
                f"**Videos:** {_fmt_count(st.get('videoCount'))}\n"
                f"**Channel ID:** {it.get('id', '')}\n"
                f"**About:** {desc[:280]}"
            )
        except Exception as e:
            return _handle_error(e, "YouTube channel")

    class VideoInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        video_id: str = Field(..., description="Video ID (the v= part of a watch URL)", min_length=5, max_length=20)

    @mcp.tool(name="youtube_video", annotations={"readOnlyHint": True})
    async def youtube_video(params: VideoInput) -> str:
        """Video details — views, likes, comments, duration — by video ID."""
        if not cfg.youtube_api_key:
            return _NOT_CONFIGURED
        try:
            data = await _get("videos", {"part": "snippet,statistics,contentDetails", "id": params.video_id})
            items = data.get("items") or []
            if not items:
                return f"No video found for ID '{params.video_id}'."
            it = items[0]
            sn, st, cd = it.get("snippet", {}), it.get("statistics", {}), it.get("contentDetails", {})
            return (
                f"## {sn.get('title', '')}\n\n"
                f"**Channel:** {sn.get('channelTitle', '')}\n"
                f"**Published:** {sn.get('publishedAt', '')[:10]}\n"
                f"**Views:** {_fmt_count(st.get('viewCount'))}\n"
                f"**Likes:** {_fmt_count(st.get('likeCount'))}\n"
                f"**Comments:** {_fmt_count(st.get('commentCount'))}\n"
                f"**Duration:** {cd.get('duration', '')}\n"
                f"**Link:** https://youtu.be/{params.video_id}"
            )
        except Exception as e:
            return _handle_error(e, "YouTube video")

    class TrendingInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        region: str = Field(default="US", description="ISO 3166-1 region code (US, DE, GB…)", min_length=2, max_length=2)
        max_results: int = Field(default=10, description="Max results (1–25)", ge=1, le=25)

    @mcp.tool(name="youtube_trending", annotations={"readOnlyHint": True})
    async def youtube_trending(params: TrendingInput) -> str:
        """Most-popular videos right now for a region."""
        if not cfg.youtube_api_key:
            return _NOT_CONFIGURED
        try:
            data = await _get("videos", {
                "part": "snippet,statistics", "chart": "mostPopular",
                "regionCode": params.region.upper(), "maxResults": params.max_results,
            })
            items = data.get("items") or []
            if not items:
                return f"No trending videos for region '{params.region.upper()}'."
            lines = [f"## Trending on YouTube — {params.region.upper()}\n"]
            for it in items:
                sn, st = it.get("snippet", {}), it.get("statistics", {})
                lines.append(
                    f"- **{sn.get('title', '')}** — {sn.get('channelTitle', '')} "
                    f"({_fmt_count(st.get('viewCount'))} views)\n  https://youtu.be/{it.get('id', '')}"
                )
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "YouTube trending")

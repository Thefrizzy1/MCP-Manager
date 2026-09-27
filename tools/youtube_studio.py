"""
tools/youtube_studio.py — the numbers only the channel owner can see.

YouTube Analytics (watch time, retention, the search terms that found you,
traffic sources, audience), the Reporting API's thumbnail impressions and CTR,
and Google Search Console for any site you own. All three read through one
Google login (``core/google_oauth.py``) connected under Settings → Google
account; until then every tool answers with how to connect, not an error.

Values are YouTube's own. Where a tool shows a derived figure it is plain
arithmetic on those values and is labelled as such.
"""
import asyncio
from datetime import date, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from core import google_oauth as go
from core import youtube_reporting as yr
from core import youtube_scrape as ys
from tools.youtube import ApiError, _n, api_error, video_stats

_ROOT = Path(__file__).resolve().parents[1]
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
SEARCH_CONSOLE = "https://www.googleapis.com/webmasters/v3"

# How YouTube names its traffic sources in Studio, keyed by the API's code.
TRAFFIC_SOURCES = {
    "YT_SEARCH": "YouTube search", "RELATED_VIDEO": "Suggested videos",
    "BROWSE": "Browse features", "SUBSCRIBER": "Subscriptions / feeds",
    "EXT_URL": "External", "NO_LINK_OTHER": "Direct or unknown", "PLAYLIST": "Playlists",
    "YT_CHANNEL": "Channel pages", "NOTIFICATION": "Notifications", "SHORTS": "Shorts feed",
    "END_SCREEN": "End screens", "YT_OTHER_PAGE": "Other YouTube features",
    "ANNOTATION": "Cards & annotations", "ADVERTISING": "YouTube advertising",
    "HASHTAGS": "Hashtag pages", "YT_PLAYLIST_PAGE": "Playlist page",
    "SOUND_PAGE": "Sound pages", "LIVE_REDIRECT": "Live redirect",
    "VIDEO_REMIXES": "Remixes", "PRODUCT_PAGE": "Product pages",
    "CAMPAIGN_CARD": "Campaign cards", "IMMERSIVE_LIVE": "Immersive live",
}

# report -> query. "cap" is YouTube's own maxResults ceiling for that shape.
REPORTS: dict[str, dict] = {
    "overview": {"metrics": "views,engagedViews,estimatedMinutesWatched,averageViewDuration,"
                            "averageViewPercentage,subscribersGained,subscribersLost,likes,comments,shares"},
    "daily": {"dimensions": "day", "sort": "day",
              "metrics": "views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost"},
    "top_videos": {"dimensions": "video", "cap": 200, "no_video_filter": True,
                   "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
                              "likes,comments,subscribersGained"},
    "traffic_sources": {"dimensions": "insightTrafficSourceType", "sort": "-views",
                        "metrics": "views,estimatedMinutesWatched"},
    "search_terms": {"dimensions": "insightTrafficSourceDetail", "sort": "-views", "cap": 25,
                     "filters": "insightTrafficSourceType==YT_SEARCH",
                     "metrics": "views,estimatedMinutesWatched"},
    "suggested_from": {"dimensions": "insightTrafficSourceDetail", "sort": "-views", "cap": 25,
                       "filters": "insightTrafficSourceType==RELATED_VIDEO",
                       "metrics": "views,estimatedMinutesWatched"},
    "external_sites": {"dimensions": "insightTrafficSourceDetail", "sort": "-views", "cap": 25,
                       "filters": "insightTrafficSourceType==EXT_URL",
                       "metrics": "views,estimatedMinutesWatched"},
    "retention": {"dimensions": "elapsedVideoTimeRatio", "needs_video": True,
                  "metrics": "audienceWatchRatio,relativeRetentionPerformance"},
    "content_type": {"dimensions": "creatorContentType", "sort": "-views",
                     "metrics": "views,estimatedMinutesWatched,averageViewDuration,subscribersGained"},
    "subscribed_status": {"dimensions": "subscribedStatus",
                          "metrics": "views,estimatedMinutesWatched,averageViewDuration"},
    "geography": {"dimensions": "country", "sort": "-views", "cap": 250,
                  "metrics": "views,estimatedMinutesWatched,averageViewDuration"},
    "devices": {"dimensions": "deviceType", "sort": "-views", "metrics": "views,estimatedMinutesWatched"},
    "demographics": {"dimensions": "ageGroup,gender", "metrics": "viewerPercentage"},
    "revenue": {"metrics": "estimatedRevenue,grossRevenue,cpm,playbackBasedCpm,monetizedPlaybacks,adImpressions"},
}
_SORT = {"views": "-views", "watch_time": "-estimatedMinutesWatched", "subscribers": "-subscribersGained"}


def date_range(days: int, start: str = "", end: str = "") -> tuple[str, str]:
    e = date.fromisoformat(end) if end else date.today()
    s = date.fromisoformat(start) if start else e - timedelta(days=days - 1)
    if s > e:
        raise ValueError("start_date is after end_date")
    return s.isoformat(), e.isoformat()


def fmt_metric(name: str, v) -> str:
    if v is None:
        return "—"
    if name == "averageViewDuration":
        return ys.fmt_ts(v)
    if name == "estimatedMinutesWatched":
        return f"{v:,.0f} min ({v / 60:,.1f} h)"
    if name in ("averageViewPercentage", "viewerPercentage"):
        return f"{v:.1f}%"
    if name in ("audienceWatchRatio", "relativeRetentionPerformance"):
        return f"{v:.3f}"
    if name in ("estimatedRevenue", "grossRevenue", "cpm", "playbackBasedCpm"):
        return f"{v:,.2f}"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.2f}"
    return _n(v) if isinstance(v, (int, float)) else str(v)


async def _google(method: str, url: str, token: str, *, params=None, json=None) -> dict:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.request(method, url, params=params, json=json,
                            headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        raise api_error(r)
    return r.json()


def _explain(e: Exception) -> str:
    msg = str(e)
    if isinstance(e, ApiError) and e.status == 403 and ("has not been used" in msg or "disabled" in msg):
        return f"Error: {msg}\n\nEnable that API in the same Google Cloud project as the OAuth client."
    if isinstance(e, ApiError) and e.status == 403 and "insufficient" in msg.lower():
        return (f"Error: {msg}\n\nThe Google login is missing a permission — disconnect and "
                "connect again under Settings → Google account to grant it.")
    return f"Error: {msg[:500]}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]
    return "\n".join(out)


def register_youtube_studio_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class AnalyticsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        report: Literal[tuple(REPORTS)] = Field(default="overview", description=(
            "overview · daily · top_videos · traffic_sources · search_terms (what viewers typed "
            "before finding you) · suggested_from (videos that recommend yours) · external_sites · "
            "retention (needs video) · content_type (Shorts vs long-form vs live) · subscribed_status · "
            "geography · devices · demographics · revenue"))
        days: int = Field(default=28, description="Days back from end_date", ge=1, le=3650)
        start_date: str = Field(default="", description="YYYY-MM-DD (overrides days)", max_length=10)
        end_date: str = Field(default="", description="YYYY-MM-DD (default today)", max_length=10)
        video: str = Field(default="", description="Limit to one of your videos (ID or URL)", max_length=300)
        max_results: int = Field(default=10, description="Rows for ranked reports", ge=1, le=200)
        sort: Literal["views", "watch_time", "subscribers"] = Field(default="views", description="top_videos ranking")

    @mcp.tool(name="youtube_analytics", annotations={"readOnlyHint": True})
    async def youtube_analytics(params: AnalyticsInput) -> str:
        """Your own channel's YouTube Analytics — watch time, average view duration,
        retention curves, subscribers gained/lost, traffic sources, the YouTube search
        terms that brought viewers, Shorts vs long-form, geography, devices,
        demographics, revenue. Needs the Google account connected in Settings."""
        spec = REPORTS[params.report]
        try:
            start, end = date_range(params.days, params.start_date, params.end_date)
            vid = ys.parse_video_id(params.video) if params.video else ""
        except ValueError as e:
            return f"Error: {e}"
        if spec.get("needs_video") and not vid:
            return f"Error: the {params.report} report is per video — pass `video`."
        try:
            token = await go.access_token(_ROOT)
        except go.NotConnected as e:
            return str(e)
        q = {"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": spec["metrics"]}
        if spec.get("dimensions"):
            q["dimensions"] = spec["dimensions"]
        filters = [f"video=={vid}"] if vid and not spec.get("no_video_filter") else []
        if spec.get("filters"):
            filters.append(spec["filters"])
        if filters:
            q["filters"] = ";".join(filters)
        if params.report == "top_videos":
            q["sort"] = _SORT[params.sort]
        elif spec.get("sort"):
            q["sort"] = spec["sort"]
        if spec.get("cap"):
            q["maxResults"] = min(params.max_results, spec["cap"])
        try:
            data = await _google("GET", ANALYTICS, token, params=q)
        except Exception as e:
            return _explain(e)

        cols = [h["name"] for h in data.get("columnHeaders") or []]
        rows = data.get("rows") or []
        st = go.status(_ROOT)
        head = [f"## YouTube Analytics — {params.report} · {st['channel_title'] or 'your channel'}",
                f"{start} → {end}" + (f" · video {vid}" if vid else "")
                + " · _YouTube's analytics usually trail real time by 2–3 days._\n"]
        if not rows:
            return "\n".join(head + ["No data for this range."])

        if params.report == "demographics":
            rows.sort(key=lambda r: r[-1] or 0, reverse=True)
        titles: dict[str, str] = {}
        lookup = [r[0] for r in rows] if params.report in ("top_videos", "suggested_from") else []
        if vid:
            lookup.append(vid)
        if lookup:
            try:
                titles = {k: v["title"] for k, v in (await video_stats(lookup, token=token)).items()}
            except Exception:
                titles = {}

        if params.report == "retention":
            return "\n".join(head + [_retention(rows, cols, titles.get(vid, vid))])

        if params.report == "overview" or params.report == "revenue":
            lines = [f"**{c}:** {fmt_metric(c, v)}" for c, v in zip(cols, rows[0])]
            if params.report == "overview" and "subscribersGained" in cols:
                g, lost = rows[0][cols.index("subscribersGained")], rows[0][cols.index("subscribersLost")]
                lines.append(f"**net subscribers (gained − lost):** {g - lost:+,}")
            if params.report == "revenue":
                lines.append("\n_Amounts in USD, as the API reports them._")
            return "\n".join(head + lines)

        shown = rows[: params.max_results] if params.report not in ("daily",) else rows
        out_rows = []
        for r in shown:
            cells = []
            for c, v in zip(cols, r):
                if c == "insightTrafficSourceType":
                    cells.append(f"{TRAFFIC_SOURCES.get(v, v)} ({v})")
                elif c in ("video",) or (c == "insightTrafficSourceDetail" and params.report == "suggested_from"):
                    cells.append(f"{titles.get(v, '')} ({v})" if titles.get(v) else v)
                else:
                    cells.append(fmt_metric(c, v))
            out_rows.append(cells)
        return "\n".join(head + [_table(cols, out_rows)])

    def _retention(rows, cols, title) -> str:
        i_ratio, i_watch = cols.index("elapsedVideoTimeRatio"), cols.index("audienceWatchRatio")
        i_rel = cols.index("relativeRetentionPerformance")
        pick = [r for r in rows if round(r[i_ratio] * 100) % 5 == 0] or rows
        body = _table(["Position", "audienceWatchRatio", "relativeRetentionPerformance"],
                      [[f"{r[i_ratio] * 100:.0f}%", f"{r[i_watch]:.3f}", f"{r[i_rel]:.3f}"] for r in pick])
        return (f"**{title}** — every 5% of the video\n\n{body}\n\n"
                "_audienceWatchRatio: share of viewers watching at that point (above 1.0 = rewatched). "
                "relativeRetentionPerformance: against YouTube videos of similar length — 0.5 is the "
                "middle, higher is better. Definitions are YouTube's._")

    class ReachInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        days: int = Field(default=28, description="Days back (reports are kept 60 days)", ge=1, le=60)
        video: str = Field(default="", description="One video (ID or URL) — day-by-day instead of per-video", max_length=300)
        max_results: int = Field(default=15, description="Videos to list", ge=1, le=200)

    @mcp.tool(name="youtube_reach", annotations={"readOnlyHint": True})
    async def youtube_reach(params: ReachInput) -> str:
        """Your videos' thumbnail impressions and impressions click-through rate (CTR),
        per video or day by day, from YouTube's daily reach reports. Needs the Google
        account connected; the first reports arrive ~48 h after connecting."""
        try:
            vid = ys.parse_video_id(params.video) if params.video else ""
            token = await go.access_token(_ROOT)
        except ValueError as e:
            return f"Error: {e}"
        except go.NotConnected as e:
            return str(e)
        try:
            job = await yr.find_reach_job(token)
        except Exception as e:
            return _explain(e)
        if not job:
            return ("No impressions report is set up for this channel yet. Press **Set up "
                    "impressions reports** under Settings → Google account (it registers a daily "
                    "report with YouTube; the first files arrive about 48 hours later).")
        since = (date.today() - timedelta(days=params.days)).isoformat() + "T00:00:00Z"
        try:
            reports = await yr.list_reports(token, job["id"], since)
        except Exception as e:
            return _explain(e)
        if not reports:
            return (f"The impressions report was set up {str(job.get('createTime', ''))[:10]}; YouTube has "
                    "not delivered any files for this window yet. The first ones arrive about 48 hours "
                    "after setup, with the 30 days before it backfilled.")
        sem = asyncio.Semaphore(5)

        async def fetch(rep):
            async with sem:
                return yr.parse_reach_csv(await yr.download(token, rep["downloadUrl"]))
        try:
            parts = await asyncio.gather(*(fetch(r) for r in reports))
        except Exception as e:
            return _explain(e)
        rows = [r for p in parts for r in p if not vid or r["video_id"] == vid]
        agg = yr.aggregate(rows)
        first, last = reports[0].get("startTime", "")[:10], reports[-1].get("endTime", "")[:10]
        tot = agg["total"]
        head = [f"## Impressions & CTR — {first} → {last} ({len(reports)} daily reports)",
                f"**Impressions:** {_n(tot['impressions'])} · **CTR\\*:** "
                + (f"{tot['ctr']:.4g}" if tot["ctr"] is not None else "—"), ""]
        foot = ("\n_\\*`video_thumbnail_impressions_ctr` exactly as YouTube's report delivers it, "
                "weighted by impressions across days. Compare one video with Studio to read its unit._")
        if vid:
            body = _table(["Date", "Impressions", "CTR*"],
                          [[d, _n(v["impressions"]), f"{v['ctr']:.4g}" if v["ctr"] is not None else "—"]
                           for d, v in agg["days"].items()])
            return "\n".join(head + [f"Video {vid}", body, foot])
        ranked = sorted(agg["videos"].items(), key=lambda kv: kv[1]["impressions"], reverse=True)
        ranked = ranked[: params.max_results]
        try:
            titles = {k: v["title"] for k, v in (await video_stats([k for k, _ in ranked], token=token)).items()}
        except Exception:
            titles = {}
        body = _table(["Video", "Impressions", "CTR*", "ID"],
                      [[titles.get(k, "—"), _n(v["impressions"]),
                        f"{v['ctr']:.4g}" if v["ctr"] is not None else "—", k] for k, v in ranked])
        return "\n".join(head + [body, foot])

    class ConsoleInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        site: str = Field(default="", description="Property, e.g. https://example.com/ or sc-domain:example.com. Empty = list your properties", max_length=300)
        dimension: Literal["query", "page", "country", "device", "date", "searchAppearance"] = Field(default="query", description="Group rows by")
        days: int = Field(default=28, description="Days back from end_date", ge=1, le=480)
        start_date: str = Field(default="", description="YYYY-MM-DD (overrides days)", max_length=10)
        end_date: str = Field(default="", description="YYYY-MM-DD (default today)", max_length=10)
        contains: str = Field(default="", description="Only queries containing this text", max_length=200)
        search_type: Literal["web", "image", "video", "news", "discover", "googleNews"] = Field(default="web", description="Which Google surface")
        max_results: int = Field(default=25, description="Rows", ge=1, le=1000)

    @mcp.tool(name="search_console_query", annotations={"readOnlyHint": True})
    async def search_console_query(params: ConsoleInput) -> str:
        """Google Search Console for your own sites: the queries, pages, countries and
        devices that brought clicks, with impressions, CTR and average position.
        Without `site` it lists your properties. Needs the Google account connected."""
        try:
            token = await go.access_token(_ROOT)
        except go.NotConnected as e:
            return str(e)
        site = params.site
        if not site:
            try:
                sites = (await _google("GET", f"{SEARCH_CONSOLE}/sites", token)).get("siteEntry") or []
            except Exception as e:
                return _explain(e)
            usable = [s for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"]
            if len(usable) != 1:
                if not sites:
                    return "This Google account has no Search Console properties."
                return "## Your Search Console properties\n" + "\n".join(
                    f"- `{s['siteUrl']}` ({s.get('permissionLevel', '')})" for s in sites) + \
                    "\n\nPass one as `site`."
            site = usable[0]["siteUrl"]
        try:
            start, end = date_range(params.days, params.start_date, params.end_date)
        except ValueError as e:
            return f"Error: {e}"
        body = {"startDate": start, "endDate": end, "dimensions": [params.dimension],
                "rowLimit": params.max_results, "type": params.search_type}
        if params.contains:
            body["dimensionFilterGroups"] = [{"filters": [
                {"dimension": "query", "operator": "contains", "expression": params.contains}]}]
        try:
            data = await _google("POST", f"{SEARCH_CONSOLE}/sites/{quote(site, safe='')}/searchAnalytics/query",
                                 token, json=body)
            totals = await _google("POST", f"{SEARCH_CONSOLE}/sites/{quote(site, safe='')}/searchAnalytics/query",
                                   token, json={k: v for k, v in body.items() if k != "dimensions"})
        except Exception as e:
            return _explain(e)
        rows = data.get("rows") or []
        t = (totals.get("rows") or [{}])[0]
        head = [f"## Search Console — {site} · {params.search_type} · by {params.dimension}",
                f"{start} → {end}" + (f" · queries containing '{params.contains}'" if params.contains else ""),
                f"**Totals:** {_n(t.get('clicks'))} clicks · {_n(t.get('impressions'))} impressions · "
                f"CTR {t['ctr'] * 100:.2f}% · avg position {t['position']:.1f}" if t else "**Totals:** —", ""]
        if not rows:
            return "\n".join(head + ["No rows for this range."])
        return "\n".join(head + [_table(
            [params.dimension, "Clicks", "Impressions", "CTR", "Avg position"],
            [[r["keys"][0], _n(r.get("clicks")), _n(r.get("impressions")),
              f"{r.get('ctr', 0) * 100:.2f}%", f"{r.get('position', 0):.1f}"] for r in rows])])

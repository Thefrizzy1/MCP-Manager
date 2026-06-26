"""
tools/utilities.py — Utility tools
Covers: weather, maps/distance, web fetch, web search, context/datetime
"""

import json
import urllib.parse
from pathlib import Path

import httpx
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT, _handle_error

_DATA = Path(__file__).resolve().parents[1] / "data"
_WEATHER_PREF = _DATA / "weather_location.txt"


def _effective_weather_city() -> str:
    if _WEATHER_PREF.exists():
        s = _WEATHER_PREF.read_text(encoding="utf-8").strip()
        if s:
            return s
    return cfg.weather_default_location or "Hamburg"


def register_utility_tools(mcp: FastMCP):

    # ─── CONTEXT / DATETIME ───────────────────────────────────────────────────

    @mcp.tool(name="get_context", annotations={"readOnlyHint": True})
    async def get_context() -> str:
        """Get current date, time, day of week, and Hamburg weather.

        Call this first when the user asks about anything time-sensitive
        (scheduling, 'tomorrow', 'next week', 'what day is it', etc).
        Returns timezone-aware datetime for Europe/Berlin.
        """
        try:
            from zoneinfo import ZoneInfo
            berlin = ZoneInfo("Europe/Berlin")
            now = datetime.now(berlin)

            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            month_names = ["January", "February", "March", "April", "May", "June",
                          "July", "August", "September", "October", "November", "December"]

            result = "## Current Context\n\n"
            result += f"**Date:** {day_names[now.weekday()]}, {now.day} {month_names[now.month-1]} {now.year}\n"
            result += f"**Time:** {now.strftime('%H:%M')} (Europe/Berlin)\n"
            result += f"**ISO:** {now.isoformat()}\n\n"

            # Quick date helpers
            from datetime import timedelta
            tomorrow = now + timedelta(days=1)
            result += f"**Tomorrow:** {day_names[tomorrow.weekday()]}, {tomorrow.day} {month_names[tomorrow.month-1]}\n"

            # Find next Monday
            days_until_monday = (7 - now.weekday()) % 7 or 7
            next_monday = now + timedelta(days=days_until_monday)
            result += f"**Next Monday:** {next_monday.day} {month_names[next_monday.month-1]}\n\n"

            # Quick weather
            try:
                city = urllib.parse.quote(_effective_weather_city())
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                    wr = await client.get(f"https://wttr.in/{city}?format=%c+%t,+feels+%f,+%h+humidity,+%w+wind")
                result += f"**Weather ({_effective_weather_city()}):** {wr.text.strip()}\n"
            except Exception:
                result += "**Weather:** unavailable\n"

            return result
        except Exception as e:
            return f"Error getting context: {e}"

    # ─── WEATHER ──────────────────────────────────────────────────────────────

    class WeatherInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        location: Optional[str] = Field(
            default=None,
            description="City — omit to use remembered default (WEATHER_DEFAULT_LOCATION / data file)",
            max_length=100,
        )

    class WeatherRememberInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        city: str = Field(..., description="e.g. Hamburg, Berlin", min_length=1, max_length=100)

    @mcp.tool(name="weather_remember_city", annotations={"readOnlyHint": False})
    async def weather_remember_city(params: WeatherRememberInput) -> str:
        """Remember preferred weather city for get_context / weather_* when location is omitted. Stored in data/weather_location.txt"""
        try:
            _DATA.mkdir(parents=True, exist_ok=True)
            _WEATHER_PREF.write_text(params.city.strip(), encoding="utf-8")
            return f"✓ Preferred weather city set to `{params.city.strip()}`."
        except Exception as e:
            return f"Error saving preference: {e}"

    @mcp.tool(name="weather_current", annotations={"readOnlyHint": True})
    async def weather_current(params: WeatherInput) -> str:
        """Current weather; location defaults to WEATHER_DEFAULT_LOCATION or remembered city."""
        try:
            loc = (params.location or "").strip() or _effective_weather_city()
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"https://wttr.in/{urllib.parse.quote(loc)}",
                    params={"format": "j1"},
                    headers={"Accept": "application/json"}
                )
                r.raise_for_status()
                data = r.json()

            current = data["current_condition"][0]
            area = data.get("nearest_area", [{}])[0]
            city = area.get("areaName", [{}])[0].get("value", loc)

            result = f"## Weather: {city}\n\n"
            result += f"**Temperature:** {current.get('temp_C')}°C (feels like {current.get('FeelsLikeC')}°C)\n"
            result += f"**Condition:** {current.get('weatherDesc', [{}])[0].get('value', 'Unknown')}\n"
            result += f"**Humidity:** {current.get('humidity')}%\n"
            result += f"**Wind:** {current.get('windspeedKmph')} km/h {current.get('winddir16Point')}\n"
            result += f"**Visibility:** {current.get('visibility')} km\n"
            result += f"**UV Index:** {current.get('uvIndex')}\n"
            return result
        except Exception as e:
            return _handle_error(e, "Weather")

    @mcp.tool(name="weather_forecast", annotations={"readOnlyHint": True})
    async def weather_forecast(params: WeatherInput) -> str:
        """3-day forecast; defaults to WEATHER_DEFAULT_LOCATION or remembered city."""
        try:
            loc = (params.location or "").strip() or _effective_weather_city()
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    f"https://wttr.in/{urllib.parse.quote(loc)}",
                    params={"format": "j1"},
                    headers={"Accept": "application/json"}
                )
                r.raise_for_status()
                data = r.json()

            area = data.get("nearest_area", [{}])[0]
            city = area.get("areaName", [{}])[0].get("value", loc)
            weather = data.get("weather", [])

            result = f"## 3-Day Forecast: {city}\n\n"
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

            for day in weather:
                date_str = day.get("date", "")
                try:
                    from datetime import date as date_type
                    d = date_type.fromisoformat(date_str)
                    day_label = day_names[d.weekday()]
                except Exception:
                    day_label = date_str

                result += f"### {day_label} ({date_str})\n"
                result += f"  🌡 {day.get('mintempC')}°C – {day.get('maxtempC')}°C\n"

                hourly = day.get("hourly", [])
                for h in hourly:
                    time = int(h.get("time", 0)) // 100
                    if time in [8, 12, 18]:
                        desc = h.get("weatherDesc", [{}])[0].get("value", "")
                        result += f"  {time:02d}:00 — {h.get('tempC')}°C {desc}, {h.get('chanceofrain')}% rain\n"
                result += "\n"

            return result
        except Exception as e:
            return _handle_error(e, "Weather forecast")

    # ─── MAPS / DISTANCE ──────────────────────────────────────────────────────

    class MapsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        origin: str = Field(..., description="Starting address or place name", min_length=1, max_length=300)
        destination: str = Field(..., description="Destination address or place name", min_length=1, max_length=300)
        mode: str = Field(default="driving-car", description="Transport mode: 'driving-car', 'foot-walking', 'cycling-regular', 'public-transport'")

    @mcp.tool(name="maps_distance", annotations={"readOnlyHint": True})
    async def maps_distance(params: MapsInput) -> str:
        """Get travel distance and estimated time between two locations.

        Uses OpenRouteService (free, no API key needed for basic queries) + Nominatim geocoding.
        Examples: origin='Hospitalstraße 62 Hamburg', destination='Hamburg Hauptbahnhof'
        """
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                # Geocode origin
                orig_r = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": params.origin, "format": "json", "limit": 1},
                    headers={"User-Agent": "PlutusHotelabMCP/1.0"}
                )
                orig_data = orig_r.json()
                if not orig_data:
                    return f"Error: Could not find location '{params.origin}'"
                orig_lon = float(orig_data[0]["lon"])
                orig_lat = float(orig_data[0]["lat"])
                orig_name = orig_data[0].get("display_name", params.origin)

                # Geocode destination
                dest_r = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": params.destination, "format": "json", "limit": 1},
                    headers={"User-Agent": "PlutusHotelabMCP/1.0"}
                )
                dest_data = dest_r.json()
                if not dest_data:
                    return f"Error: Could not find location '{params.destination}'"
                dest_lon = float(dest_data[0]["lon"])
                dest_lat = float(dest_data[0]["lat"])
                dest_name = dest_data[0].get("display_name", params.destination)

                # Get route from OSRM (free, no key needed)
                route_r = await client.get(
                    f"http://router.project-osrm.org/route/v1/{params.mode.replace('driving-car', 'driving').replace('foot-walking', 'foot').replace('cycling-regular', 'bike')}/{orig_lon},{orig_lat};{dest_lon},{dest_lat}",
                    params={"overview": "false", "steps": "false"}
                )
                route_data = route_r.json()

            routes = route_data.get("routes", [])
            if not routes:
                return "No route found between these locations."

            route = routes[0]
            distance_km = route.get("distance", 0) / 1000
            duration_min = route.get("duration", 0) / 60

            mode_labels = {
                "driving-car": "🚗 Driving",
                "foot-walking": "🚶 Walking",
                "cycling-regular": "🚴 Cycling",
                "public-transport": "🚌 Transit"
            }
            mode_label = mode_labels.get(params.mode, params.mode)

            result = f"## Route: {mode_label}\n\n"
            result += f"**From:** {orig_name[:80]}\n"
            result += f"**To:** {dest_name[:80]}\n\n"
            result += f"**Distance:** {distance_km:.1f} km\n"
            if duration_min < 60:
                result += f"**Duration:** ~{int(duration_min)} minutes\n"
            else:
                hours = int(duration_min // 60)
                mins = int(duration_min % 60)
                result += f"**Duration:** ~{hours}h {mins}min\n"

            return result
        except Exception as e:
            return _handle_error(e, "Maps")

    # ─── WEB SEARCH ───────────────────────────────────────────────────────────

    class WebSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search query", min_length=1, max_length=500)
        limit: int = Field(default=5, description="Number of results", ge=1, le=20)

    @mcp.tool(name="web_search", annotations={"readOnlyHint": True})
    async def web_search(params: WebSearchInput) -> str:
        """Search the web using DuckDuckGo. No API key required.

        Good for: current events, quick facts, Hamburg local info, prices, opening hours.
        """
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(
                    "https://api.duckduckgo.com/",
                    params={
                        "q": params.query,
                        "format": "json",
                        "no_redirect": "1",
                        "no_html": "1",
                        "skip_disambig": "1"
                    },
                    headers={"User-Agent": "PlutusHotelabMCP/1.0"}
                )
                r.raise_for_status()
                data = r.json()

            result = f"## Web Search: '{params.query}'\n\n"

            # Abstract (instant answer)
            abstract = data.get("Abstract", "")
            if abstract:
                result += f"**Answer:** {abstract}\n"
                source = data.get("AbstractSource", "")
                if source:
                    result += f"Source: {source} — {data.get('AbstractURL', '')}\n"
                result += "\n"

            # Related topics
            topics = data.get("RelatedTopics", [])
            if topics:
                result += "**Related:**\n"
                count = 0
                for topic in topics:
                    if count >= params.limit:
                        break
                    if isinstance(topic, dict) and topic.get("Text"):
                        result += f"- {topic['Text'][:200]}\n"
                        url = topic.get("FirstURL", "")
                        if url:
                            result += f"  {url}\n"
                        count += 1

            if not abstract and not topics:
                result += f"No results found. Try a different search term.\n"
                result += f"Direct search: https://duckduckgo.com/?q={params.query.replace(' ', '+')}\n"

            return result
        except Exception as e:
            return _handle_error(e, "Web search")

    class WebFetchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        url: str = Field(..., description="URL to fetch and read", min_length=1)
        max_chars: int = Field(default=3000, description="Max characters to return", ge=100, le=10000)

    @mcp.tool(name="web_fetch", annotations={"readOnlyHint": True})
    async def web_fetch(params: WebFetchInput) -> str:
        """Fetch a web page and return its readable text content.

        Good for reading articles, checking prices, opening hours, menus.
        Strips HTML and returns clean text.
        """
        import asyncio as _asyncio
        from core.ssrf_guard import screen_url
        blocked = await _asyncio.to_thread(screen_url, params.url)
        if blocked:
            return f"Error: {blocked}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; PlutusHotelabMCP/1.0)"}
            ) as client:
                # Stream so we can stop early if the response is huge — without
                # this a 100 MB PDF or video URL would buffer entirely into
                # memory before we truncate at max_chars.
                # 4× max_chars ≈ enough for HTML overhead vs. extracted text.
                cap_bytes = max(64 * 1024, params.max_chars * 4)
                async with client.stream("GET", params.url) as r:
                    r.raise_for_status()
                    content_type = r.headers.get("content-type", "")
                    chunks: list[bytes] = []
                    received = 0
                    async for chunk in r.aiter_bytes():
                        chunks.append(chunk)
                        received += len(chunk)
                        if received >= cap_bytes:
                            break
                    body = b"".join(chunks)
                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
                text_full = body.decode(charset, errors="replace")

                if "json" in content_type:
                    return f"## JSON from {params.url}\n\n```json\n{text_full[:params.max_chars]}\n```"

                # Strip HTML tags, then decode ALL entities via the stdlib
                # (&#39;, &quot;, &mdash;, numeric/hex — not just the four we
                # used to special-case).
                import re
                import html as _html
                text = text_full
                text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = _html.unescape(text)
                text = re.sub(r'\s+', ' ', text).strip()
                text = text[:params.max_chars]

            return f"## Content from {params.url}\n\n{text}"
        except Exception as e:
            return _handle_error(e, f"Web fetch ({params.url})")

    # ─── WIKIPEDIA ────────────────────────────────────────────────────────────

    class WikipediaSummaryInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        title: str = Field(..., description="Article title (exact or close match)", min_length=1, max_length=300)
        lang: str = Field(default="en", description="Wiki language code, e.g. en, de", max_length=12)

    @mcp.tool(name="wikipedia_summary", annotations={"readOnlyHint": True})
    async def wikipedia_summary(params: WikipediaSummaryInput) -> str:
        """First section extract from Wikipedia (MediaWiki API). No API key."""
        lang = (params.lang or "en").strip() or "en"
        title = params.title.strip()
        api = f"https://{lang}.wikipedia.org/w/api.php"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                r = await client.get(
                    api,
                    params={
                        "action": "query",
                        "format": "json",
                        "prop": "extracts",
                        "exintro": "1",
                        "explaintext": "1",
                        "titles": title,
                    },
                    headers={"User-Agent": "PlutusMCP/1.0 (utilities; contact: local)"},
                )
                r.raise_for_status()
                data = r.json()
            pages = (data.get("query") or {}).get("pages") or {}
            if not pages:
                return f"No Wikipedia data for `{title}` ({lang})."
            page = next(iter(pages.values()))
            if page.get("missing"):
                return f"Wikipedia has no article titled `{title}` in `{lang}`."
            extract = (page.get("extract") or "").strip()
            pg_title = page.get("title") or title
            # Wikipedia URLs use underscores for spaces; everything else needs
            # percent-encoding so non-ASCII titles ('Café', 'München') resolve.
            url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(pg_title.replace(' ', '_'), safe='')}"
            if not extract:
                return f"## {pg_title}\n\n(no extract) · {url}"
            return f"## {pg_title}\n\n{extract}\n\nSource: {url}"
        except Exception as e:
            return _handle_error(e, "Wikipedia")

    # ─── CURRENCY (Frankfurter, ECB-based, no key) ───────────────────────────

    class CurrencyConvertInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        amount: float = Field(..., description="Amount to convert", gt=0)
        from_currency: str = Field(..., description="ISO 4217, e.g. EUR", min_length=3, max_length=3)
        to_currency: str = Field(..., description="ISO 4217, e.g. USD", min_length=3, max_length=3)

    class CurrencyRatesInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        base: str = Field(default="EUR", description="Base ISO currency", min_length=3, max_length=3)

    @mcp.tool(name="currency_convert", annotations={"readOnlyHint": True})
    async def currency_convert(params: CurrencyConvertInput) -> str:
        """Convert amount using Frankfurter (ECB). No API key."""
        a = params.amount
        f = params.from_currency.upper()
        t = params.to_currency.upper()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                r = await client.get(
                    "https://api.frankfurter.app/latest",
                    params={"amount": a, "from": f, "to": t},
                    headers={"User-Agent": "PlutusMCP/1.0"},
                )
                r.raise_for_status()
                data = r.json()
            rates = data.get("rates") or {}
            out = rates.get(t)
            if out is None:
                return f"No rate for {f} → {t}. Response: {json.dumps(data)[:400]}"
            date = data.get("date") or "?"
            return f"## FX · Frankfurter ({date})\n\n**{a} {f}** ≈ **{out} {t}**\n"
        except Exception as e:
            return _handle_error(e, "Currency convert")

    @mcp.tool(name="currency_rates", annotations={"readOnlyHint": True})
    async def currency_rates(params: CurrencyRatesInput) -> str:
        """Latest ECB reference rates with chosen base currency (Frankfurter). No API key."""
        base = params.base.upper()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                r = await client.get(
                    "https://api.frankfurter.app/latest",
                    params={"from": base},
                    headers={"User-Agent": "PlutusMCP/1.0"},
                )
                r.raise_for_status()
                data = r.json()
            rates = data.get("rates") or {}
            date = data.get("date") or "?"
            lines = [f"## FX rates · base {base} ({date})\n"]
            for cur, val in sorted(rates.items())[:40]:
                lines.append(f"- **{cur}**: {val}")
            if len(rates) > 40:
                lines.append(f"\n… +{len(rates) - 40} more")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "Currency rates")

    # ─── GOOGLE PROGRAMMABLE SEARCH ─────────────────────────────────────────

    class GoogleSearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search query", min_length=1, max_length=500)
        num: int = Field(default=5, description="Max results (1–10)", ge=1, le=10)

    @mcp.tool(name="google_search", annotations={"readOnlyHint": True})
    async def google_search(params: GoogleSearchInput) -> str:
        """Web search via Google Custom Search JSON API. Requires GOOGLE_API_KEY and GOOGLE_CSE_ID."""
        if not cfg.google_api_key or not cfg.google_cse_id:
            return (
                "Google Programmable Search not configured. "
                "Set **GOOGLE_API_KEY** and **GOOGLE_CSE_ID** in `.env` "
                "(Google Cloud Console + Programmable Search Engine)."
            )
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                r = await client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={
                        "key": cfg.google_api_key,
                        "cx": cfg.google_cse_id,
                        "q": params.query,
                        "num": params.num,
                    },
                    headers={"User-Agent": "PlutusMCP/1.0"},
                )
                r.raise_for_status()
                data = r.json()
            items = data.get("items") or []
            if not items:
                meta = data.get("searchInformation", {})
                tot = meta.get("totalResults", "0")
                return f"## Google search: '{params.query}'\n\nNo results (total reported: {tot})."
            lines = [f"## Google search: '{params.query}'\n"]
            for it in items:
                title = it.get("title", "")
                link = it.get("link", "")
                snip = (it.get("snippet") or "").replace("\n", " ")
                lines.append(f"- **{title}**\n  {link}\n  _{snip}_\n")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "Google search")

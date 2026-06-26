"""
Bulk read-only public HTTP API tools (no API keys unless noted).
Dashboard rows: PUBLIC_SERVICES_DASHBOARD — merged in core.service_registry.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP

from client import TIMEOUT, _handle_error

_UA = {"User-Agent": "PlutusMCP/2.0 (public bulk; local homelab)"}


async def _get_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers=_UA) as client:
        r = await client.get(url, params=params or {})
        r.raise_for_status()
        return r.json()


async def _get_text(url: str, *, params: dict[str, Any] | None = None, limit: int = 8000) -> str:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers=_UA) as client:
        r = await client.get(url, params=params or {})
        r.raise_for_status()
        t = r.text.strip()
        return t if len(t) <= limit else t[:limit] + "…"


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Q(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., min_length=1, max_length=400)


class QOpt(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(default="", max_length=400)


class LatLon(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class RestRegion(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    region: str = Field(default="Europe", max_length=80)


class RestName(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(default="Germany", max_length=120)


class IdNum(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(default=1, ge=1, le=999_999_999)


class PokemonName(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(default="pikachu", max_length=80)


class BinanceSym(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    symbol: str = Field(default="BTCUSDT", max_length=20)


class PostalCountry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    country_code: str = Field(default="de", max_length=2)
    postal_code: str = Field(default="10115", max_length=12)


class MetQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    q: str = Field(default="sunflower", max_length=120)


class TriviaOpts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: int = Field(default=3, ge=1, le=10)
    difficulty: str = Field(default="easy", max_length=12)


class NationalizeName(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(default="john", max_length=80)


def register_public_apis_bulk(mcp: FastMCP) -> None:
    # ─── Network / HTTP ───────────────────────────────────────────────

    @mcp.tool(name="pub_unix_timestamp", annotations={"readOnlyHint": True})
    async def pub_unix_timestamp(params: Empty) -> str:
        """Current Unix timestamp (server local clock)."""
        return f"## Unix time\n\n`{int(time.time())}` UTC-ish clock"

    @mcp.tool(name="pub_httpbin_ip", annotations={"readOnlyHint": True})
    async def pub_httpbin_ip(params: Empty) -> str:
        """Origin IP via httpbin."""
        try:
            d = await _get_json("https://httpbin.org/ip")
            return f"## Your IP (httpbin)\n\n```json\n{json.dumps(d, indent=2)}\n```"
        except Exception as e:
            return _handle_error(e, "httpbin ip")

    @mcp.tool(name="pub_httpbin_uuid", annotations={"readOnlyHint": True})
    async def pub_httpbin_uuid(params: Empty) -> str:
        """Random UUID4 from httpbin."""
        try:
            d = await _get_json("https://httpbin.org/uuid")
            return f"## UUID\n\n{d.get('uuid', d)}"
        except Exception as e:
            return _handle_error(e, "httpbin uuid")

    @mcp.tool(name="pub_ipify", annotations={"readOnlyHint": True})
    async def pub_ipify(params: Empty) -> str:
        """Public IPv4/IPv6 via ipify."""
        try:
            d = await _get_json("https://api.ipify.org?format=json")
            return f"## ipify\n\n**IP:** `{d.get('ip', d)}`"
        except Exception as e:
            return _handle_error(e, "ipify")

    @mcp.tool(name="pub_ip_api_lookup", annotations={"readOnlyHint": True})
    async def pub_ip_api_lookup(params: QOpt) -> str:
        """Geo lookup for IP (ip-api.com free tier). Omit query to use caller detection."""
        try:
            q = (params.query or "").strip()
            path = q if q else ""
            d = await _get_json(f"http://ip-api.com/json/{path}", params={"fields": "status,message,country,regionName,city,lat,lon,isp,query"})
            return f"## ip-api\n\n```json\n{json.dumps(d, indent=2)}\n```"
        except Exception as e:
            return _handle_error(e, "ip-api")

    @mcp.tool(name="pub_dns_resolve", annotations={"readOnlyHint": True})
    async def pub_dns_resolve(params: Q) -> str:
        """DNS A records via Google DNS-over-HTTPS JSON."""
        try:
            host = params.query.strip().rstrip(".")
            d = await _get_json("https://dns.google/resolve", params={"name": host, "type": "A"})
            return f"## DNS `{host}`\n\n```json\n{json.dumps(d, indent=2)[:6000]}\n```"
        except Exception as e:
            return _handle_error(e, "DNS")

    # ─── Time / geography ───────────────────────────────────────────

    @mcp.tool(name="pub_worldtime_timezone", annotations={"readOnlyHint": True})
    async def pub_worldtime_timezone(params: Q) -> str:
        """Current time for an IANA zone e.g. Europe/Berlin."""
        try:
            z = params.query.strip().replace(" ", "_")
            d = await _get_json(f"https://worldtimeapi.org/api/timezone/{z}")
            return f"## WorldTime `{z}`\n\n```json\n{json.dumps(d, indent=2)[:4000]}\n```"
        except Exception as e:
            return _handle_error(e, "worldtime")

    @mcp.tool(name="pub_worldtime_ip", annotations={"readOnlyHint": True})
    async def pub_worldtime_ip(params: Empty) -> str:
        """WorldTimeAPI client-ip endpoint."""
        try:
            d = await _get_json("https://worldtimeapi.org/api/ip")
            return f"## WorldTime (IP)\n\n```json\n{json.dumps(d, indent=2)[:4000]}\n```"
        except Exception as e:
            return _handle_error(e, "worldtime ip")

    @mcp.tool(name="pub_restcountries_region", annotations={"readOnlyHint": True})
    async def pub_restcountries_region(params: RestRegion) -> str:
        """Countries in a region (REST Countries)."""
        try:
            d = await _get_json(f"https://restcountries.com/v3.1/region/{params.region}")
            names = [c.get("name", {}).get("common", "?") for c in (d if isinstance(d, list) else [])[:40]]
            return f"## Region `{params.region}`\n\n" + "\n".join(f"- {n}" for n in names) + (f"\n… ({len(d)} total)" if isinstance(d, list) and len(d) > 40 else "")
        except Exception as e:
            return _handle_error(e, "restcountries")

    @mcp.tool(name="pub_restcountries_name", annotations={"readOnlyHint": True})
    async def pub_restcountries_name(params: RestName) -> str:
        """Search country by common name."""
        try:
            d = await _get_json(f"https://restcountries.com/v3.1/name/{params.name}")
            return f"## Country `{params.name}`\n\n```json\n{json.dumps(d, indent=2)[:8000]}\n```"
        except Exception as e:
            return _handle_error(e, "restcountries")

    @mcp.tool(name="pub_zippopotam", annotations={"readOnlyHint": True})
    async def pub_zippopotam(params: PostalCountry) -> str:
        """Postal / ZIP lookup (Zippopotam.us)."""
        try:
            cc = params.country_code.lower()
            pc = params.postal_code.strip()
            d = await _get_json(f"https://api.zippopotam.us/{cc}/{pc}")
            return f"## Postal `{cc}/{pc}`\n\n```json\n{json.dumps(d, indent=2)[:4000]}\n```"
        except Exception as e:
            return _handle_error(e, "zippopotam")

    @mcp.tool(name="pub_nominatim_search", annotations={"readOnlyHint": True})
    async def pub_nominatim_search(params: Q) -> str:
        """OpenStreetMap search (Nominatim) — be polite, low volume."""
        try:
            d = await _get_json(
                "https://nominatim.openstreetmap.org/search",
                params={"q": params.query, "format": "json", "limit": 5},
            )
            return f"## Nominatim `{params.query}`\n\n```json\n{json.dumps(d, indent=2)[:6000]}\n```"
        except Exception as e:
            return _handle_error(e, "nominatim")

    @mcp.tool(name="pub_open_meteo_forecast", annotations={"readOnlyHint": True})
    async def pub_open_meteo_forecast(params: LatLon) -> str:
        """7-day weather forecast (Open-Meteo, no key)."""
        try:
            d = await _get_json(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": params.latitude,
                    "longitude": params.longitude,
                    "current": "temperature_2m,relative_humidity_2m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                    "timezone": "auto",
                },
            )
            return f"## Open-Meteo\n\n```json\n{json.dumps(d, indent=2)[:8000]}\n```"
        except Exception as e:
            return _handle_error(e, "open-meteo")

    # ─── Finance ──────────────────────────────────────────────────────

    @mcp.tool(name="pub_coingecko_price", annotations={"readOnlyHint": True})
    async def pub_coingecko_price(params: Empty) -> str:
        """Sample crypto prices vs USD (CoinGecko public)."""
        try:
            d = await _get_json(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd"},
            )
            return f"## CoinGecko\n\n```json\n{json.dumps(d, indent=2)}\n```"
        except Exception as e:
            return _handle_error(e, "coingecko")

    @mcp.tool(name="pub_binance_ticker", annotations={"readOnlyHint": True})
    async def pub_binance_ticker(params: BinanceSym) -> str:
        """Binance public ticker price."""
        try:
            d = await _get_json("https://api.binance.com/api/v3/ticker/price", params={"symbol": params.symbol.upper()})
            return f"## Binance `{params.symbol.upper()}`\n\n```json\n{json.dumps(d, indent=2)}\n```"
        except Exception as e:
            return _handle_error(e, "binance")

    @mcp.tool(name="pub_coincap_assets", annotations={"readOnlyHint": True})
    async def pub_coincap_assets(params: Empty) -> str:
        """Top CoinCap assets page (sample)."""
        try:
            d = await _get_json("https://api.coincap.io/v2/assets", params={"limit": 15})
            rows = [f"- **{a.get('name')}** ({a.get('symbol')}): ${float(a.get('priceUsd', 0)):,.4f}" for a in d.get("data", [])]
            return "## CoinCap (top 15)\n\n" + "\n".join(rows)
        except Exception as e:
            return _handle_error(e, "coincap")

    # ─── Fun / names ──────────────────────────────────────────────────

    @mcp.tool(name="pub_quotable_random", annotations={"readOnlyHint": True})
    async def pub_quotable_random(params: Empty) -> str:
        """Random quote (Quotable)."""
        try:
            d = await _get_json("https://api.quotable.io/random")
            return f"## Quote\n\n**{d.get('content')}**\n— _{d.get('author')}_"
        except Exception as e:
            return _handle_error(e, "quotable")

    @mcp.tool(name="pub_zenquotes_today", annotations={"readOnlyHint": True})
    async def pub_zenquotes_today(params: Empty) -> str:
        """Daily quotes (ZenQuotes)."""
        try:
            d = await _get_json("https://zenquotes.io/api/today")
            if isinstance(d, list) and d:
                x = d[0]
                return f"## ZenQuote\n\n**{x.get('q')}**\n— _{x.get('a')}_"
            return str(d)
        except Exception as e:
            return _handle_error(e, "zenquotes")

    @mcp.tool(name="pub_chuck_joke", annotations={"readOnlyHint": True})
    async def pub_chuck_joke(params: Empty) -> str:
        """Random Chuck Norris joke."""
        try:
            d = await _get_json("https://api.chucknorris.io/jokes/random")
            return f"## Chuck Norris\n\n{d.get('value', d)}"
        except Exception as e:
            return _handle_error(e, "chucknorris")

    @mcp.tool(name="pub_joke_any", annotations={"readOnlyHint": True})
    async def pub_joke_any(params: Empty) -> str:
        """Random joke (v2.jokeapi.dev safe mode)."""
        try:
            d = await _get_json("https://v2.jokeapi.dev/joke/Any?safe-mode")
            if d.get("type") == "single":
                return f"## Joke\n\n{d.get('joke')}"
            return f"## Joke\n\n**{d.get('setup')}**\n{d.get('delivery')}"
        except Exception as e:
            return _handle_error(e, "jokeapi")

    @mcp.tool(name="pub_cat_fact", annotations={"readOnlyHint": True})
    async def pub_cat_fact(params: Empty) -> str:
        """Random cat fact."""
        try:
            d = await _get_json("https://catfact.ninja/fact")
            return f"## Cat fact\n\n{d.get('fact', d)}"
        except Exception as e:
            return _handle_error(e, "catfact")

    @mcp.tool(name="pub_dog_image", annotations={"readOnlyHint": True})
    async def pub_dog_image(params: Empty) -> str:
        """Random dog image URL."""
        try:
            d = await _get_json("https://dog.ceo/api/breeds/image/random")
            return f"## Dog\n\n{d.get('message', d)}"
        except Exception as e:
            return _handle_error(e, "dog.ceo")

    @mcp.tool(name="pub_agify_name", annotations={"readOnlyHint": True})
    async def pub_agify_name(params: RestName) -> str:
        """Predict age from first name (Agify)."""
        try:
            d = await _get_json("https://api.agify.io", params={"name": params.name.split()[0]})
            return f"## Agify `{params.name}`\n\n```json\n{json.dumps(d, indent=2)}\n```"
        except Exception as e:
            return _handle_error(e, "agify")

    @mcp.tool(name="pub_genderize_name", annotations={"readOnlyHint": True})
    async def pub_genderize_name(params: RestName) -> str:
        """Predict gender from first name (Genderize)."""
        try:
            d = await _get_json("https://api.genderize.io", params={"name": params.name.split()[0]})
            return f"## Genderize\n\n```json\n{json.dumps(d, indent=2)}\n```"
        except Exception as e:
            return _handle_error(e, "genderize")

    @mcp.tool(name="pub_random_user", annotations={"readOnlyHint": True})
    async def pub_random_user(params: Empty) -> str:
        """Random profile (randomuser.me)."""
        try:
            d = await _get_json("https://randomuser.me/api/?nat=us,de,gb")
            return f"## Random user\n\n```json\n{json.dumps(d, indent=2)[:6000]}\n```"
        except Exception as e:
            return _handle_error(e, "randomuser")

    # ─── Universities / education ─────────────────────────────────────

    @mcp.tool(name="pub_univ_search", annotations={"readOnlyHint": True})
    async def pub_univ_search(params: Q) -> str:
        """Search universities (Hipo labs)."""
        try:
            d = await _get_json("http://universities.hipolabs.com/search", params={"name": params.query})
            lines = [f"- **{x.get('name')}** — {', '.join((x.get('domains') or [])[:2])}" for x in (d if isinstance(d, list) else [])[:25]]
            return f"## Universities `{params.query}`\n\n" + "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "universities")

    @mcp.tool(name="pub_openlibrary_search", annotations={"readOnlyHint": True})
    async def pub_openlibrary_search(params: Q) -> str:
        """Search books (Open Library)."""
        try:
            d = await _get_json("https://openlibrary.org/search.json", params={"q": params.query, "limit": 10})
            docs = d.get("docs", [])[:10]
            lines = [f"- **{x.get('title')}** ({x.get('first_publish_year', '?')})" for x in docs]
            return f"## Open Library `{params.query}`\n\n" + "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "openlibrary")

    # ─── Games / trivia / cards ───────────────────────────────────────

    @mcp.tool(name="pub_deck_new", annotations={"readOnlyHint": True})
    async def pub_deck_new(params: Empty) -> str:
        """New shuffled deck (deckofcards)."""
        try:
            d = await _get_json("https://deckofcardsapi.com/api/deck/new/shuffle/?deck_count=1")
            return f"## Deck\n\n**deck_id:** `{d.get('deck_id')}` · remaining: {d.get('remaining')}"
        except Exception as e:
            return _handle_error(e, "deckofcards")

    @mcp.tool(name="pub_deck_draw", annotations={"readOnlyHint": True})
    async def pub_deck_draw(params: IdNum) -> str:
        """Draw cards — uses deck_id 1 trick: draw from new deck inline."""
        try:
            deck = await _get_json("https://deckofcardsapi.com/api/deck/new/shuffle/")
            did = deck.get("deck_id")
            d = await _get_json(f"https://deckofcardsapi.com/api/deck/{did}/draw/", params={"count": min(params.id, 10)})
            cards = [f"{c.get('value')} of {c.get('suit')}" for c in d.get("cards", [])]
            return "## Draw\n\n" + "\n".join(f"- {c}" for c in cards)
        except Exception as e:
            return _handle_error(e, "deckofcards")

    @mcp.tool(name="pub_pokemon", annotations={"readOnlyHint": True})
    async def pub_pokemon(params: PokemonName) -> str:
        """PokÃ©mon species summary (PokeAPI)."""
        try:
            d = await _get_json(f"https://pokeapi.co/api/v2/pokemon-species/{params.name.lower()}")
            names = [x.get("name") for x in d.get("names", []) if x.get("language", {}).get("name") == "en"]
            en_name = names[0] if names else params.name
            text = (d.get("flavor_text_entries") or [{}])[0].get("flavor_text", "").replace("\n", " ")
            return f"## PokÃ©mon `{en_name}`\n\n{text[:1200]}"
        except Exception as e:
            return _handle_error(e, "pokeapi")

    @mcp.tool(name="pub_swapi_person", annotations={"readOnlyHint": True})
    async def pub_swapi_person(params: IdNum) -> str:
        """Star Wars person by SWAPI id."""
        try:
            d = await _get_json(f"https://swapi.dev/api/people/{params.id}/")
            return f"## SWAPI person {params.id}\n\n```json\n{json.dumps(d, indent=2)[:4000]}\n```"
        except Exception as e:
            return _handle_error(e, "swapi")

    @mcp.tool(name="pub_rick_morty_character", annotations={"readOnlyHint": True})
    async def pub_rick_morty_character(params: IdNum) -> str:
        """Rick and Morty character."""
        try:
            d = await _get_json(f"https://rickandmortyapi.com/api/character/{params.id}")
            return f"## {d.get('name')}\n\nStatus: {d.get('status')} · Species: {d.get('species')}\n{d.get('image')}"
        except Exception as e:
            return _handle_error(e, "rickandmorty")

    @mcp.tool(name="pub_breaking_bad_quote", annotations={"readOnlyHint": True})
    async def pub_breaking_bad_quote(params: Empty) -> str:
        """Random Breaking Bad quote."""
        try:
            d = await _get_json("https://api.breakingbadquotes.xyz/v1/quotes")
            if isinstance(d, list) and d:
                x = d[0]
                return f"## Breaking Bad\n\n**{x.get('quote')}**\n— _{x.get('author')}_"
            return str(d)
        except Exception as e:
            return _handle_error(e, "breakingbad")

    @mcp.tool(name="pub_numbers_trivia", annotations={"readOnlyHint": True})
    async def pub_numbers_trivia(params: IdNum) -> str:
        """Number trivia (Numbers API)."""
        try:
            t = await _get_text(f"http://numbersapi.com/{params.id}/trivia")
            return f"## Trivia ({params.id})\n\n{t}"
        except Exception as e:
            return _handle_error(e, "numbersapi")

    @mcp.tool(name="pub_numbers_year", annotations={"readOnlyHint": True})
    async def pub_numbers_year(params: IdNum) -> str:
        """Year fact (Numbers API)."""
        try:
            y = min(max(params.id, 1), 2026)
            t = await _get_text(f"http://numbersapi.com/{y}/year")
            return f"## Year {y}\n\n{t}"
        except Exception as e:
            return _handle_error(e, "numbersapi")

    @mcp.tool(name="pub_opentrivia_questions", annotations={"readOnlyHint": True})
    async def pub_opentrivia_questions(params: TriviaOpts) -> str:
        """Open Trivia DB multiple-choice questions."""
        try:
            d = await _get_json(
                "https://opentdb.com/api.php",
                params={"amount": params.amount, "difficulty": params.difficulty, "type": "multiple"},
            )
            results = d.get("results", [])
            lines = []
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r.get('question')}  \n   Correct: **{r.get('correct_answer')}**")
            return "## Open Trivia\n\n" + "\n\n".join(lines)
        except Exception as e:
            return _handle_error(e, "opentrivia")

    # ─── Space ────────────────────────────────────────────────────────

    @mcp.tool(name="pub_iss_location", annotations={"readOnlyHint": True})
    async def pub_iss_location(params: Empty) -> str:
        """ISS current lat/lon (Open Notify)."""
        try:
            d = await _get_json("http://api.open-notify.org/iss-now.json")
            return f"## ISS\n\n```json\n{json.dumps(d, indent=2)}\n```"
        except Exception as e:
            return _handle_error(e, "open-notify")

    @mcp.tool(name="pub_people_in_space", annotations={"readOnlyHint": True})
    async def pub_people_in_space(params: Empty) -> str:
        """Humans currently in space."""
        try:
            d = await _get_json("http://api.open-notify.org/astros.json")
            people = d.get("people", [])
            lines = [f"- **{p.get('name')}** ({p.get('craft')})" for p in people[:20]]
            return f"## People in space ({d.get('number', len(people))})\n\n" + "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "open-notify")

    @mcp.tool(name="pub_nasa_apod", annotations={"readOnlyHint": True})
    async def pub_nasa_apod(params: Empty) -> str:
        """NASA Astronomy Picture of the Day (DEMO_KEY)."""
        try:
            d = await _get_json("https://api.nasa.gov/planetary/apod", params={"api_key": "DEMO_KEY"})
            return f"## APOD — {d.get('title')}\n\n{d.get('date')} · {d.get('media_type')}\n\n{d.get('url')}\n\n{d.get('explanation', '')[:2000]}"
        except Exception as e:
            return _handle_error(e, "NASA APOD")

    @mcp.tool(name="pub_spaceflight_news", annotations={"readOnlyHint": True})
    async def pub_spaceflight_news(params: Empty) -> str:
        """Latest spaceflight headlines (Spaceflight News API)."""
        try:
            d = await _get_json("https://api.spaceflightnewsapi.net/v4/articles/", params={"limit": 8})
            lines = [f"- [{x.get('title')}]({x.get('url')})" for x in d.get("results", [])]
            return "## Spaceflight news\n\n" + "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "spaceflight news")

    # ─── Dev misc ─────────────────────────────────────────────────────

    @mcp.tool(name="pub_github_zen", annotations={"readOnlyHint": True})
    async def pub_github_zen(params: Empty) -> str:
        """GitHub Zen aphorism."""
        try:
            t = await _get_text("https://api.github.com/zen")
            return f"## GitHub Zen\n\n{t}"
        except Exception as e:
            return _handle_error(e, "github zen")

    @mcp.tool(name="pub_xkcd_current", annotations={"readOnlyHint": True})
    async def pub_xkcd_current(params: Empty) -> str:
        """Current xkcd comic metadata."""
        try:
            d = await _get_json("https://xkcd.com/info.0.json")
            return f"## xkcd #{d.get('num')} — {d.get('title')}\n\n{d.get('img')}\n\n_{d.get('alt')}_"
        except Exception as e:
            return _handle_error(e, "xkcd")

    # ─── Museums ──────────────────────────────────────────────────────

    @mcp.tool(name="pub_met_search", annotations={"readOnlyHint": True})
    async def pub_met_search(params: MetQuery) -> str:
        """Metropolitan Museum collection search."""
        try:
            d = await _get_json("https://collectionapi.metmuseum.org/public/collection/v1/search", params={"q": params.q})
            ids = (d.get("objectIDs") or [])[:10]
            lines = []
            for oid in ids:
                try:
                    obj = await _get_json(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}")
                    lines.append(f"- **{obj.get('title')}** ({obj.get('objectDate', '?')}) — {obj.get('primaryImageSmall') or 'no image'}")
                except Exception:
                    lines.append(f"- id {oid}")
            return f"## Met Museum `{params.q}`\n\n" + "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "Met Museum")

    @mcp.tool(name="pub_artic_artworks", annotations={"readOnlyHint": True})
    async def pub_artic_artworks(params: QOpt) -> str:
        """Art Institute of Chicago artworks search."""
        try:
            qq = (params.query or "monet").strip()
            d = await _get_json(
                "https://api.artic.edu/api/v1/artworks/search",
                params={"q": qq, "limit": 8},
            )
            lines = []
            for x in d.get("data", [])[:8]:
                lines.append(f"- **{x.get('title')}** — id `{x.get('id')}`")
            return f"## ArtIC `{qq}`\n\n" + "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "ArtIC")

    # ─── TV ───────────────────────────────────────────────────────────

    @mcp.tool(name="pub_tvmaze_search", annotations={"readOnlyHint": True})
    async def pub_tvmaze_search(params: Q) -> str:
        """TV show search (TVMaze)."""
        try:
            d = await _get_json("https://api.tvmaze.com/search/shows", params={"q": params.query})
            lines = []
            for x in (d if isinstance(d, list) else [])[:12]:
                sh = x.get("show") or {}
                lines.append(f"- **{sh.get('name')}** ({sh.get('premiered', '?')}) score {x.get('score', '?')}")
            return f"## TVMaze `{params.query}`\n\n" + "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "tvmaze")

    @mcp.tool(name="pub_er_api_latest", annotations={"readOnlyHint": True})
    async def pub_er_api_latest(params: Empty) -> str:
        """FX latest rates vs USD (open.er-api.com)."""
        try:
            d = await _get_json("https://open.er-api.com/v6/latest/USD")
            rates = (d.get("rates") or {})
            sample = list(rates.items())[:18]
            lines = "\n".join(f"- **{c}**: {v}" for c, v in sample)
            return f"## ER API (USD base)\n\n{lines}\n… ({len(rates)} currencies)"
        except Exception as e:
            return _handle_error(e, "er-api")

    @mcp.tool(name="pub_uuid_v4_local", annotations={"readOnlyHint": True})
    async def pub_uuid_v4_local(params: Empty) -> str:
        """Generate a UUID v4 locally (offline)."""
        return f"## UUID v4\n\n`{uuid.uuid4()}`"

    @mcp.tool(name="pub_anime_random", annotations={"readOnlyHint": True})
    async def pub_anime_random(params: Empty) -> str:
        """Random anime title + synopsis (Jikan)."""
        try:
            d = await _get_json("https://api.jikan.moe/v4/random/anime")
            a = d.get("data") or {}
            return f"## {a.get('title')}\n\nScore: {a.get('score')} · Episodes: {a.get('episodes')}\n\n{(a.get('synopsis') or '')[:1200]}"
        except Exception as e:
            return _handle_error(e, "jikan")

    @mcp.tool(name="pub_kanye_quote", annotations={"readOnlyHint": True})
    async def pub_kanye_quote(params: Empty) -> str:
        """Random Kanye quote (kanye.rest)."""
        try:
            d = await _get_json("https://api.kanye.rest")
            return f"## Kanye\n\n{d.get('quote', d)}"
        except Exception as e:
            return _handle_error(e, "kanye.rest")

    @mcp.tool(name="pub_advice_slip", annotations={"readOnlyHint": True})
    async def pub_advice_slip(params: Empty) -> str:
        """Random advice slip."""
        try:
            d = await _get_json("https://api.adviceslip.com/advice")
            slip = (d.get("slip") or {})
            return f"## Advice #{slip.get('id')}\n\n{slip.get('advice')}"
        except Exception as e:
            return _handle_error(e, "adviceslip")

    @mcp.tool(name="pub_animechan_quote", annotations={"readOnlyHint": True})
    async def pub_animechan_quote(params: Empty) -> str:
        """Random anime quote (animechan.xyz)."""
        try:
            d = await _get_json("https://animechan.xyz/api/random")
            return f"## Anime quote\n\n**{d.get('quote', d)}**\n— _{d.get('character')}_ ({d.get('anime')})"
        except Exception as e:
            return _handle_error(e, "animechan")

    @mcp.tool(name="pub_bored_activity", annotations={"readOnlyHint": True})
    async def pub_bored_activity(params: Empty) -> str:
        """Random activity suggestion (Bored API — App Brewery mirror; the
        original boredapi.com was shut down)."""
        try:
            d = await _get_json("https://bored-api.appbrewery.com/random")
            return (
                f"## {d.get('activity', 'Activity')}\n\n"
                f"Type: `{d.get('type')}` · Participants: {d.get('participants')} · "
                f"Price: {d.get('price')}"
            )
        except Exception as e:
            return _handle_error(e, "boredapi")

    @mcp.tool(name="pub_nationalize_name", annotations={"readOnlyHint": True})
    async def pub_nationalize_name(params: NationalizeName) -> str:
        """Nationality prediction for a first name (nationalize.io)."""
        try:
            d = await _get_json("https://api.nationalize.io", params={"name": params.name})
            c = d.get("country") or []
            lines = [f"## Nationalize · `{params.name}`\n"]
            for row in c[:8]:
                lines.append(f"- **{row.get('country_id')}** · {float(row.get('probability', 0)) * 100:.1f}%")
            return "\n".join(lines) if len(lines) > 1 else str(d)
        except Exception as e:
            return _handle_error(e, "nationalize")

    @mcp.tool(name="pub_cloudflare_trace", annotations={"readOnlyHint": True})
    async def pub_cloudflare_trace(params: Empty) -> str:
        """Cloudflare edge trace (IP, colo, HTTP/3) from cdn-cgi trace."""
        try:
            t = await _get_text("https://1.1.1.1/cdn-cgi/trace", limit=6000)
            return f"## Cloudflare trace\n\n```\n{t}\n```"
        except Exception as e:
            return _handle_error(e, "cloudflare trace")

    @mcp.tool(name="pub_shibe_image", annotations={"readOnlyHint": True})
    async def pub_shibe_image(params: Empty) -> str:
        """Random Shiba Inu image URL (shibe.online)."""
        try:
            d = await _get_json("https://shibe.online/api/shibes?count=1")
            u = d[0] if isinstance(d, list) and d else str(d)
            return f"## Shibe\n\n`{u}`\n\n![]({u})"
        except Exception as e:
            return _handle_error(e, "shibe.online")

    @mcp.tool(name="pub_blockchain_btc_ticker", annotations={"readOnlyHint": True})
    async def pub_blockchain_btc_ticker(params: Empty) -> str:
        """BTC spot vs major fiats (blockchain.info ticker)."""
        try:
            d = await _get_json("https://blockchain.info/ticker")
            lines = ["## Blockchain.info ticker\n"]
            for sym, row in list(d.items())[:12]:
                if isinstance(row, dict) and "last" in row:
                    lines.append(f"- **{sym}** last `{row.get('last')}`")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "blockchain.info")

    @mcp.tool(name="pub_official_joke", annotations={"readOnlyHint": True})
    async def pub_official_joke(params: Empty) -> str:
        """Random joke (official-joke-api)."""
        try:
            d = await _get_json("https://official-joke-api.appspot.com/random_joke")
            return f"## {d.get('type', 'joke').title()}\n\n**{d.get('setup', '')}**\n\n_{d.get('punchline', '')}_"
        except Exception as e:
            return _handle_error(e, "official-joke-api")

    @mcp.tool(name="pub_ipwho", annotations={"readOnlyHint": True})
    async def pub_ipwho(params: Empty) -> str:
        """Geo + ASN for caller IP (ipwho.is)."""
        try:
            d = await _get_json("https://ipwho.is/")
            if not d.get("success", True):
                return f"Error: {d}"
            return (
                f"## ipwho.is\n\n**IP:** `{d.get('ip')}` · **{d.get('country')}** ({d.get('country_code')})\n\n"
                f"City: {d.get('city')} · Region: {d.get('region')}\n"
                f"ISP: {d.get('connection', {}).get('isp', '')}"
            )
        except Exception as e:
            return _handle_error(e, "ipwho.is")


# Defaults for health/smoke batches & tester
PUBLIC_TOOL_DEFAULTS: dict[str, dict] = {
    "pub_restcountries_region": {"region": "Europe"},
    "pub_restcountries_name": {"name": "Germany"},
    "pub_zippopotam": {"country_code": "de", "postal_code": "10115"},
    "pub_nominatim_search": {"query": "Hamburg"},
    "pub_open_meteo_forecast": {"latitude": 53.55, "longitude": 9.99},
    "pub_ip_api_lookup": {"query": ""},
    "pub_worldtime_timezone": {"query": "Europe/Berlin"},
    "pub_agify_name": {"name": "Alex"},
    "pub_genderize_name": {"name": "Alex"},
    "pub_univ_search": {"query": "Hamburg"},
    "pub_openlibrary_search": {"query": "foundation asimov"},
    "pub_swapi_person": {"id": 1},
    "pub_rick_morty_character": {"id": 1},
    "pub_numbers_trivia": {"id": 42},
    "pub_numbers_year": {"id": 1969},
    "pub_deck_draw": {"id": 3},
    "pub_pokemon": {"name": "pikachu"},
    "pub_binance_ticker": {"symbol": "BTCUSDT"},
    "pub_dns_resolve": {"query": "google.com"},
    "pub_opentrivia_questions": {"amount": 2, "difficulty": "easy"},
    "pub_met_search": {"q": "sunflower"},
    "pub_artic_artworks": {"query": "monet"},
    "pub_tvmaze_search": {"query": "breaking bad"},
    "pub_bored_activity": {},
    "pub_nationalize_name": {"name": "maria"},
    "pub_cloudflare_trace": {},
    "pub_shibe_image": {},
    "pub_blockchain_btc_ticker": {},
    "pub_official_joke": {},
    "pub_ipwho": {},
}


def _tool_row(name: str, label: str, params: list[tuple[str, str, str]]) -> dict:
    return {"name": name, "label": label, "params": params}


def _public_service(row: tuple) -> dict:
    sid, label, tag, desc, tools = row
    return {
        "id": sid, "label": label, "icon": "", "tag": tag, "section": "public", "desc": desc,
        "config_keys": [], "health_url": None, "health_headers": lambda: {}, "configured_keys": (),
        "tools": [_tool_row(*tool) for tool in tools],
    }


_PUBLIC_SERVICE_ROWS = [
    ("pub_network", "Public · Network & DNS", "utilities", "IPs, DNS-over-HTTPS, httpbin helpers — no keys", [("pub_unix_timestamp", "Unix timestamp", []), ("pub_httpbin_ip", "Your IP (httpbin)", []), ("pub_httpbin_uuid", "Random UUID", []), ("pub_ipify", "IP (ipify)", []), ("pub_ip_api_lookup", "Geo IP lookup", [("query", "IP or blank", "text")]), ("pub_dns_resolve", "DNS A lookup", [("query", "hostname", "text")]), ("pub_uuid_v4_local", "UUID v4 (local)", [])]),
    ("pub_geo_time", "Public · Time & places", "utilities", "WorldTime, REST Countries, postal codes, geocode, weather", [("pub_worldtime_timezone", "Time by IANA zone", [("query", "Europe/Berlin", "text")]), ("pub_worldtime_ip", "Time for your IP", []), ("pub_restcountries_region", "Countries in region", [("region", "Europe", "text")]), ("pub_restcountries_name", "Country by name", [("name", "Germany", "text")]), ("pub_zippopotam", "Postal lookup", [("country_code", "de", "text"), ("postal_code", "10115", "text")]), ("pub_nominatim_search", "OSM search", [("query", "city", "text")]), ("pub_open_meteo_forecast", "Weather forecast", [("latitude", "53.55", "number"), ("longitude", "9.99", "number")])]),
    ("pub_finance_crypto", "Public · Crypto tickers", "finance", "CoinGecko, Binance, CoinCap — rate limits apply", [("pub_coingecko_price", "BTC/ETH/SOL prices", []), ("pub_binance_ticker", "Binance ticker", [("symbol", "BTCUSDT", "text")]), ("pub_coincap_assets", "CoinCap top assets", []), ("pub_er_api_latest", "FX vs USD (ER API)", [])]),
    ("pub_fun", "Public · Quotes & fun", "personal", "Quotes, jokes, animals, demographics toys", [("pub_quotable_random", "Random quote", []), ("pub_zenquotes_today", "ZenQuotes today", []), ("pub_chuck_joke", "Chuck Norris joke", []), ("pub_joke_any", "Random joke", []), ("pub_cat_fact", "Cat fact", []), ("pub_dog_image", "Dog photo URL", []), ("pub_agify_name", "Age from name", [("name", "Alex", "text")]), ("pub_genderize_name", "Gender guess", [("name", "Alex", "text")]), ("pub_random_user", "Random user JSON", []), ("pub_anime_random", "Random anime (Jikan)", []), ("pub_kanye_quote", "Kanye quote", []), ("pub_advice_slip", "Advice slip", []), ("pub_animechan_quote", "Anime quote", [])]),
    ("pub_education", "Public · Universities & books", "reference", "Universities API + Open Library", [("pub_univ_search", "University search", [("query", "Hamburg", "text")]), ("pub_openlibrary_search", "Book search", [("query", "Asimov", "text")])]),
    ("pub_games", "Public · Games & trivia", "media", "Deck of cards, Pokemon, SWAPI, Rick & Morty, trivia", [("pub_deck_new", "New shuffled deck", []), ("pub_deck_draw", "Draw cards (auto deck)", [("id", "3", "number")]), ("pub_pokemon", "Pokemon species", [("name", "pikachu", "text")]), ("pub_swapi_person", "SWAPI person", [("id", "1", "number")]), ("pub_rick_morty_character", "Rm character", [("id", "1", "number")]), ("pub_breaking_bad_quote", "Breaking Bad quote", []), ("pub_numbers_trivia", "Number trivia", [("id", "42", "number")]), ("pub_numbers_year", "Year fact", [("id", "1969", "number")]), ("pub_opentrivia_questions", "Trivia MCQ", [("amount", "3", "number"), ("difficulty", "easy", "text")])]),
    ("pub_space", "Public · Space & NASA", "science", "ISS, people in space, APOD, headlines", [("pub_iss_location", "ISS position", []), ("pub_people_in_space", "Astronauts list", []), ("pub_nasa_apod", "NASA APOD", []), ("pub_spaceflight_news", "Space news", [])]),
    ("pub_dev_culture", "Public · Dev & culture", "utilities", "GitHub zen, xkcd, Met & ArtIC search", [("pub_github_zen", "GitHub Zen", []), ("pub_xkcd_current", "Current xkcd", []), ("pub_met_search", "Met Museum search", [("q", "sunflower", "text")]), ("pub_artic_artworks", "Art Institute search", [("query", "monet", "text")]), ("pub_tvmaze_search", "TV show search", [("query", "breaking bad", "text")])]),
    ("pub_catalog_misc", "Public · More free APIs", "utilities", "Curated from public-apis/public-apis (no keys): boredapi, nationalize, Cloudflare trace, shibe, BTC ticker, jokes, ipwho", [("pub_bored_activity", "Bored? activity idea", []), ("pub_nationalize_name", "Nationalize a name", [("name", "maria", "text")]), ("pub_cloudflare_trace", "Cloudflare edge trace", []), ("pub_shibe_image", "Random shibe image", []), ("pub_blockchain_btc_ticker", "BTC fiat ticker", []), ("pub_official_joke", "Random joke", []), ("pub_ipwho", "Your IP + geo (ipwho.is)", [])]),
]

PUBLIC_SERVICES_DASHBOARD: list[dict] = [_public_service(row) for row in _PUBLIC_SERVICE_ROWS]
def _build_catalog() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for svc in PUBLIC_SERVICES_DASHBOARD:
        for t in svc.get("tools", []):
            rows.append((t["name"], t["label"], svc.get("tag", "utilities")))
    return rows


PUBLIC_CATALOG_META: list[tuple[str, str, str]] = _build_catalog()


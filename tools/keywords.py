"""
tools/keywords.py — how often people search for something.

Search Console (``search_console_query``) answers "which searches already show
*my* site". This answers the other question — how many people search for a
phrase at all — and that number exists in exactly one place: Google Ads' Keyword
Planner. Two ways to reach it:

- **Google Ads API** (``KeywordPlanIdeaService``). Free, Google's own numbers,
  but gated: a developer token from a Google Ads *manager* account with **Basic**
  access (Explorer access, which Google grants on application, explicitly
  excludes keyword planning), the id of an Ads account, and the Plutus Google
  login, which asks for the AdWords scope once a developer token is set.
- **DataForSEO** — resells the same Keyword Planner data, pay-as-you-go, no
  Google approval. A login and API password from app.dataforseo.com.

Either returns average monthly searches, the month-by-month series, competition
and the top-of-page bid range. These are *Google Search* volumes: Google
publishes no search volume for YouTube itself — youtube_keywords (autocomplete)
and youtube_analytics' search_terms report are the YouTube-side signals.

**Google Trends** (``trends_compare``) is the free answer to "how does interest
in X compare with Y": a 0–100 index, relative to the highest point in the
comparison, for up to five terms — and, unlike Keyword Planner, it can be
restricted to *YouTube* searches. No counts, no key. Google offers no open
Trends API, so this reads the endpoints trends.google.com's own page calls; they
answer a normal client but rate-limit bursts (429), hence one request at a time,
spaced, with patient retries on a fresh cookie.

Values are shown as the source returns them. Google may round volumes into
buckets for Ads accounts with little or no spend; that is Google's rounding,
not ours.
"""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Literal

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from client import _handle_error
from config import cfg

_ROOT = Path(__file__).resolve().parents[1]
ADS_HOST = "https://googleads.googleapis.com"
DATAFORSEO = "https://api.dataforseo.com/v3/keywords_data/google_ads"
NL = "\n"

# Google's geo-target ids for countries are 2000 + the ISO 3166 numeric code.
COUNTRIES: dict[str, int] = {
    "US": 840, "GB": 826, "UK": 826, "DE": 276, "AT": 40, "CH": 756, "NL": 528, "BE": 56,
    "FR": 250, "ES": 724, "IT": 380, "PT": 620, "PL": 616, "SE": 752, "NO": 578, "DK": 208,
    "FI": 246, "IE": 372, "CA": 124, "AU": 36, "NZ": 554, "IN": 356, "JP": 392, "KR": 410,
    "CN": 156, "TW": 158, "HK": 344, "SG": 702, "BR": 76, "MX": 484, "AR": 32, "ZA": 710,
    "TR": 792, "RU": 643, "UA": 804, "CZ": 203, "GR": 300, "IL": 376, "AE": 784, "SA": 682,
}
# Google Ads language constants.
LANGUAGES: dict[str, int] = {
    "en": 1000, "de": 1001, "fr": 1002, "es": 1003, "it": 1004, "ja": 1005, "da": 1009,
    "nl": 1010, "fi": 1011, "ko": 1012, "no": 1013, "pt": 1014, "sv": 1015, "zh": 1017,
    "ar": 1019, "pl": 1030, "ru": 1031, "tr": 1037,
}
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
          "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]

NOT_CONFIGURED = (
    "Search volume is not configured. Two options:\n"
    "1. **Google Ads API** (free, Google's own numbers): in a Google Ads *manager* "
    "account open Admin → API Center, get a developer token and apply for **Basic "
    "access** (Explorer access does not include Keyword Planner). Enable the "
    "*Google Ads API* in the same Google Cloud project as Plutus's OAuth client. "
    "Add GOOGLE_ADS_DEVELOPER_TOKEN and GOOGLE_ADS_CUSTOMER_ID (+ "
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID = the manager's id) in Settings → Keyword volume, "
    "then reconnect Settings → Google account to grant the AdWords permission.\n"
    "2. **DataForSEO** (same data, pay per request, no approval): add "
    "DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD from app.dataforseo.com.")


def _digits(v: str) -> str:
    return re.sub(r"\D", "", v or "")


def google_ads_ready() -> bool:
    return bool(cfg.google_ads_developer_token and _digits(cfg.google_ads_customer_id))


def dataforseo_ready() -> bool:
    return bool(cfg.dataforseo_login and cfg.dataforseo_password)


def backend_status() -> dict:
    """For reach_doctor and the tool: which backend 'auto' would use."""
    if google_ads_ready():
        return {"backend": "Google Ads API (Keyword Planner)", "note": "needs Basic-access developer token"}
    if dataforseo_ready():
        return {"backend": "DataForSEO", "note": "pay per request"}
    return {"backend": "", "note": "set up Google Ads API or DataForSEO — see keyword_volume"}


def parse_keywords(raw: str, cap: int) -> list[str]:
    seen, out = set(), []
    for k in re.split(r"[,\n;]", raw or ""):
        k = re.sub(r"\s+", " ", k).strip().lower()
        if k and k not in seen and len(k) <= 80:
            seen.add(k)
            out.append(k)
    return out[:cap]


def geo_id(country: str) -> int | None:
    c = (country or "").strip().upper()
    if not c or c in ("WORLD", "WORLDWIDE", "ALL"):
        return None
    if c.isdigit():
        return int(c)
    if c not in COUNTRIES:
        raise ValueError(f"unknown country {country!r} — use a 2-letter code like DE or US, "
                         "a Google geo-target id, or empty for worldwide")
    return 2000 + COUNTRIES[c]


def language_id(lang: str) -> int:
    l = (lang or "en").strip().lower()
    if l.isdigit():
        return int(l)
    if l not in LANGUAGES:
        raise ValueError(f"unknown language {lang!r} — one of {', '.join(LANGUAGES)} or a Google language id")
    return LANGUAGES[l]


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def rows_from_google(results: list, metrics_key: str) -> list[dict]:
    out = []
    for r in results or []:
        m = r.get(metrics_key) or {}
        monthly = [(int(x.get("year") or 0), MONTHS.index(x["month"]) + 1 if x.get("month") in MONTHS else 0,
                    _int(x.get("monthlySearches"))) for x in m.get("monthlySearchVolumes") or []]
        low, high = _int(m.get("lowTopOfPageBidMicros")), _int(m.get("highTopOfPageBidMicros"))
        out.append({"keyword": r.get("text", ""), "avg": _int(m.get("avgMonthlySearches")),
                    "competition": (m.get("competition") or "").replace("UNSPECIFIED", ""),
                    "competition_index": _int(m.get("competitionIndex")),
                    "bid_low": low / 1e6 if low is not None else None,
                    "bid_high": high / 1e6 if high is not None else None,
                    "currency": "account currency", "monthly": sorted(monthly)})
    return out


def rows_from_dataforseo(items: list) -> list[dict]:
    out = []
    for it in items or []:
        monthly = [(int(x.get("year") or 0), int(x.get("month") or 0), _int(x.get("search_volume")))
                   for x in it.get("monthly_searches") or []]
        out.append({"keyword": it.get("keyword", ""), "avg": _int(it.get("search_volume")),
                    "competition": it.get("competition") or "",
                    "competition_index": _int(it.get("competition_index")),
                    "bid_low": it.get("low_top_of_page_bid"), "bid_high": it.get("high_top_of_page_bid"),
                    "currency": "USD", "monthly": sorted(monthly)})
    return out


def render(rows: list[dict], *, title: str, source: str, show_monthly: bool) -> str:
    def n(v):
        return f"{v:,}" if isinstance(v, int) else "—"

    def bid(r):
        if r["bid_low"] is None and r["bid_high"] is None:
            return "—"
        f = lambda x: "—" if x is None else f"{x:,.2f}"
        return f"{f(r['bid_low'])}–{f(r['bid_high'])}"

    lines = [f"## {title}", f"_Source: {source}. Bids in {rows[0]['currency'] if rows else ''}._", "",
             "| Keyword | Avg monthly searches | Competition | Index | Top-of-page bid |",
             "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['keyword'].replace('|', '/')} | {n(r['avg'])} | {r['competition'] or '—'} | "
                     f"{n(r['competition_index'])} | {bid(r)} |")
    if show_monthly:
        for r in rows:
            series = [f"{y}-{m:02d}: {n(v)}" for y, m, v in r["monthly"][-12:] if y and m]
            if series:
                lines += ["", f"**{r['keyword']}** by month: " + " · ".join(series)]
    return NL.join(lines)


async def _google_ads(path: str, body: dict) -> dict:
    from core import google_oauth as go
    token = await go.access_token(_ROOT)
    headers = {"Authorization": f"Bearer {token}",
               "developer-token": cfg.google_ads_developer_token.strip()}
    login = _digits(cfg.google_ads_login_customer_id)
    if login:
        headers["login-customer-id"] = login
    version = (cfg.google_ads_api_version or "v25").strip()
    cid = _digits(cfg.google_ads_customer_id)
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{ADS_HOST}/{version}/customers/{cid}:{path}", json=body, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(google_ads_error(r))
    return r.json()


def google_ads_error(r: httpx.Response) -> str:
    """The Ads API's nested error, said plainly, with the fix for the usual ones."""
    try:
        err = r.json()
        err = err[0] if isinstance(err, list) else err
        e = err.get("error") or {}
        msg = e.get("message") or ""
        codes = []
        for d in e.get("details") or []:
            for x in d.get("errors") or []:
                codes += [f"{k}.{v}" for k, v in (x.get("errorCode") or {}).items()]
                msg = x.get("message") or msg
    except ValueError:
        msg, codes = r.text[:300], []
    text = f"Google Ads API HTTP {r.status_code}: {msg} {' '.join(codes)}".strip()
    joined = " ".join(codes) + " " + msg
    if "DEVELOPER_TOKEN_NOT_APPROVED" in joined or "only approved for use with test accounts" in joined:
        text += "\n\nThe developer token has Test access. Keyword Planner needs **Basic** access — apply in the manager account's API Center."
    elif "SERVICE_ACCESS_DENIED" in joined or "Explorer" in joined:
        text += "\n\nExplorer access excludes Keyword Planner — apply for **Basic** access in the API Center."
    elif "USER_PERMISSION_DENIED" in joined:
        text += ("\n\nThe Google account you connected cannot see that Ads account. If it is reached "
                 "through a manager account, set GOOGLE_ADS_LOGIN_CUSTOMER_ID to the manager's id.")
    elif r.status_code == 403 and ("insufficient" in joined.lower() or "scope" in joined.lower()):
        text += "\n\nThe Google login lacks the AdWords permission — reconnect under Settings → Google account."
    elif r.status_code == 403 and ("has not been used" in joined or "disabled" in joined):
        text += "\n\nEnable the Google Ads API in the same Google Cloud project as the OAuth client."
    return text


async def _dataforseo(path: str, task: dict) -> list:
    async with httpx.AsyncClient(timeout=120, auth=(cfg.dataforseo_login, cfg.dataforseo_password)) as c:
        r = await c.post(f"{DATAFORSEO}/{path}/live", json=[task])
    r.raise_for_status()
    body = r.json()
    t = (body.get("tasks") or [{}])[0]
    if t.get("status_code") != 20000:
        raise RuntimeError(f"DataForSEO: {t.get('status_message') or body.get('status_message')}")
    return t.get("result") or []


# ── Google Trends ────────────────────────────────────────────────────────────

TRENDS = "https://trends.google.com/trends"
TRENDS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
TIMEFRAMES = {"7d": "now 7-d", "1m": "today 1-m", "3m": "today 3-m", "12m": "today 12-m",
              "5y": "today 5-y", "all": "all"}
# Trends' own name for each search surface ("froogle" is Shopping, historically).
PROPERTIES = {"web": "", "youtube": "youtube", "images": "images", "news": "news", "shopping": "froogle"}
_TRENDS_GATE = asyncio.Lock()
_TRENDS_STATE: dict = {"cookies": None, "at": 0.0, "last": 0.0}
_TRENDS_MIN_INTERVAL = 1.5


def trends_json(text: str):
    """Trends prefixes every answer with an XSSI guard — )]}' and, on some
    endpoints, a comma. The JSON starts at the first brace."""
    i = text.find("{")
    if i < 0:
        raise ValueError("Google Trends returned no JSON")
    return json.loads(text[i:])


def trends_explore_request(terms: list[str], timeframe: str, geo: str, prop: str) -> dict:
    return {"comparisonItem": [{"keyword": t, "geo": geo, "time": timeframe} for t in terms],
            "category": 0, "property": prop}


def sample_rows(points: list, n: int = 14) -> list:
    """At most ``n`` evenly spaced points, always keeping the first and the last."""
    if len(points) <= n:
        return list(points)
    step = (len(points) - 1) / (n - 1)
    return [points[round(i * step)] for i in range(n)]


async def _trends_get(path: str, params: dict) -> dict:
    """One Trends request, spaced from the last; a 429 is retried twice (5 s, then
    15 s) on a fresh cookie before giving up."""
    async with _TRENDS_GATE:
        for backoff in (5, 15, 0):
            wait = _TRENDS_MIN_INTERVAL - (time.time() - _TRENDS_STATE["last"])
            if wait > 0:
                await asyncio.sleep(wait)
            headers = {"User-Agent": TRENDS_UA, "Accept-Language": "en-US,en;q=0.9"}
            async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers,
                                         cookies=_TRENDS_STATE["cookies"] or {}) as c:
                if not _TRENDS_STATE["cookies"] or time.time() - _TRENDS_STATE["at"] > 1800:
                    # The page itself often answers 429, but it still sets the NID
                    # cookie the API endpoints want.
                    await c.get(f"{TRENDS}/explore", params={"q": "x"})
                    _TRENDS_STATE.update(cookies=dict(c.cookies.items()), at=time.time())
                r = await c.get(f"{TRENDS}/api/{path}", params={"hl": "en-US", "tz": "0", **params})
            _TRENDS_STATE["last"] = time.time()
            if r.status_code == 429 and backoff:
                _TRENDS_STATE["cookies"] = None
                await asyncio.sleep(backoff)
                continue
            if r.status_code == 429:
                raise RuntimeError("Google Trends is rate-limiting this address (429). Try again in a few minutes.")
            r.raise_for_status()
            return trends_json(r.text)
    raise RuntimeError("Google Trends did not answer.")


def render_trends(terms: list[str], data: dict, *, label: str) -> str:
    timeline = data.get("timelineData") or []
    if not timeline:
        return f"Google Trends has no data for {', '.join(terms)} ({label})."
    avgs = data.get("averages") or []
    lines = [f"## Google Trends — {label}",
             "_0–100 = interest relative to the highest point in this comparison, not search counts._", ""]
    if avgs:
        lines += ["| Term | Average |", "|---|---|"]
        lines += [f"| {t} | {a} |" for t, a in zip(terms, avgs)]
        lines.append("")
    lines += ["| Period | " + " | ".join(terms) + " |", "|---|" + "---|" * len(terms)]
    for p in sample_rows(timeline):
        when = p.get("formattedAxisTime") or p.get("formattedTime") or ""
        vals = p.get("formattedValue") or [str(v) for v in p.get("value") or []]
        lines.append(f"| {when}{' (partial)' if p.get('isPartial') else ''} | " + " | ".join(vals) + " |")
    if len(timeline) > 14:
        lines += ["", f"_{len(timeline)} data points in this range; 14 evenly spaced ones shown._"]
    return NL.join(lines)


def render_related(term: str, data: dict) -> str:
    lists = (data.get("default") or {}).get("rankedList") or []
    out = []
    for title, lst in zip(("Top", "Rising"), lists[:2]):
        items = (lst.get("rankedKeyword") or [])[:10]
        if items:
            out.append(f"**{title}:** " + " · ".join(
                f"{k.get('query')} ({k.get('formattedValue')})" for k in items))
    return NL.join([f"### Related searches for '{term}'", *(out or ["none"])])


def register_keyword_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class VolumeInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        keywords: str = Field(..., min_length=1, max_length=4000,
                              description="Comma- or newline-separated keywords (≤50; ≤10 as idea seeds)")
        ideas: bool = Field(default=False, description="Also suggest related keywords with their volumes")
        country: str = Field(default="", max_length=12,
                             description="2-letter code (DE, US, …) or empty for worldwide")
        language: str = Field(default="en", max_length=8, description="en, de, fr, … ")
        limit: int = Field(default=30, ge=1, le=200, description="Max idea rows")
        backend: Literal["auto", "google_ads", "dataforseo"] = Field(default="auto")

    @mcp.tool(name="keyword_volume", annotations={"readOnlyHint": True, "openWorldHint": True})
    async def keyword_volume(params: VolumeInput) -> str:
        """Google search volume for keywords (Keyword Planner data): average monthly
        searches, the month-by-month series, competition and ad bid range — and
        related keyword ideas. Google Search, not YouTube."""
        backend = params.backend
        if backend == "auto":
            backend = "google_ads" if google_ads_ready() else "dataforseo" if dataforseo_ready() else ""
        if not backend or (backend == "google_ads" and not google_ads_ready()) \
                or (backend == "dataforseo" and not dataforseo_ready()):
            return NOT_CONFIGURED
        kws = parse_keywords(params.keywords, 10 if params.ideas else 50)
        if not kws:
            return "Error: no keywords given."
        try:
            geo, lang = geo_id(params.country), language_id(params.language)
        except ValueError as e:
            return f"Error: {e}"
        where = (params.country.upper() or "worldwide") + f" · {params.language}"

        try:
            if backend == "google_ads":
                base = {"language": f"languageConstants/{lang}", "keywordPlanNetwork": "GOOGLE_SEARCH"}
                if geo:
                    base["geoTargetConstants"] = [f"geoTargetConstants/{geo}"]
                if params.ideas:
                    data = await _google_ads("generateKeywordIdeas",
                                             {**base, "keywordSeed": {"keywords": kws},
                                              "includeAdultKeywords": False, "pageSize": params.limit})
                    rows = rows_from_google(data.get("results"), "keywordIdeaMetrics")
                else:
                    data = await _google_ads("generateKeywordHistoricalMetrics", {**base, "keywords": kws})
                    rows = rows_from_google(data.get("results"), "keywordMetrics")
                source = "Google Ads Keyword Planner (Google Search)"
            else:
                task = {"keywords": kws, "language_code": params.language.lower()}
                if geo:
                    task["location_code"] = geo
                if params.ideas:
                    task["limit"] = params.limit
                items = await _dataforseo("keywords_for_keywords" if params.ideas else "search_volume", task)
                rows = rows_from_dataforseo(items)
                source = "DataForSEO (Google Ads data, Google Search)"
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            from core import google_oauth as go
            if isinstance(e, go.NotConnected):
                return str(e)
            return _handle_error(e, "Google Ads API" if backend == "google_ads" else "DataForSEO")

        if not rows:
            return f"No volume data for {', '.join(kws)} ({where})."
        if params.ideas:
            rows.sort(key=lambda r: r["avg"] or 0, reverse=True)
            rows = rows[: params.limit]
        title = ("Keyword ideas from " if params.ideas else "Search volume: ") + ", ".join(kws[:5]) \
            + ("…" if len(kws) > 5 else "") + f" — {where}"
        return render(rows, title=title, source=source, show_monthly=len(rows) <= 5)

    class TrendsInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        keywords: str = Field(..., min_length=1, max_length=400,
                              description="1–5 terms to compare, comma-separated")
        timeframe: Literal["7d", "1m", "3m", "12m", "5y", "all"] = Field(default="12m")
        country: str = Field(default="", max_length=2, description="2-letter code (DE, US…) or empty = worldwide")
        search_type: Literal["web", "youtube", "images", "news", "shopping"] = Field(
            default="web", description="youtube = YouTube searches only")
        related: bool = Field(default=False, description="Also list top and rising related searches per term")

    @mcp.tool(name="trends_compare", annotations={"readOnlyHint": True, "openWorldHint": True})
    async def trends_compare(params: TrendsInput) -> str:
        """Compare interest in up to 5 terms over time with Google Trends (0–100
        relative index, free, no key) — on Google web search or YouTube search.
        Optional top/rising related searches."""
        terms = [t.strip() for t in params.keywords.split(",") if t.strip()][:5]
        if not terms:
            return "Error: no keywords given."
        geo = params.country.upper()
        if geo and not re.fullmatch(r"[A-Z]{2}", geo):
            return "Error: country must be a 2-letter code like DE or US."
        req = trends_explore_request(terms, TIMEFRAMES[params.timeframe], geo, PROPERTIES[params.search_type])
        try:
            explore = await _trends_get("explore", {"req": json.dumps(req)})
            widgets = explore.get("widgets") or []
            ts = next((w for w in widgets if w.get("id") == "TIMESERIES"), None)
            if ts is None:
                return "Error: Google Trends returned no timeline for that request."
            series = await _trends_get("widgetdata/multiline",
                                       {"req": json.dumps(ts["request"]), "token": ts["token"]})
            related = []
            if params.related:
                for i, term in enumerate(terms):
                    w = next((w for w in widgets if w.get("id") in (f"RELATED_QUERIES_{i}", "RELATED_QUERIES")
                              and (w.get("id") != "RELATED_QUERIES" or len(terms) == 1)), None)
                    if w:
                        rel = await _trends_get("widgetdata/relatedsearches",
                                                {"req": json.dumps(w["request"]), "token": w["token"]})
                        related.append(render_related(term, rel))
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            return _handle_error(e, "Google Trends")
        label = f"{params.search_type} search · {geo or 'worldwide'} · {params.timeframe}"
        out = render_trends(terms, series.get("default") or {}, label=label)
        return (NL + NL).join([out, *related])

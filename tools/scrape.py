"""
tools/scrape.py — Firecrawl: the pages plain HTTP cannot read.

``web_fetch`` (tools/utilities.py) does the honest thing with a URL: fetch it,
strip the tags, return the text. That works for articles and documentation served
as HTML, and returns almost nothing for the growing number of sites that render
their content in the browser — a React docs site, a dashboard, anything behind a
cookie wall or an anti-bot check. The text is not in the HTML, so no amount of
tag-stripping will find it.

Firecrawl runs a real browser and hands back Markdown. Four capabilities, in
rough order of how often research wants them:

- **scrape** one page, rendered, as Markdown
- **map** a site's URLs without fetching each one — the cheap way to find the
  pages worth reading
- **crawl** a section of a site and return every page's content
- **search** the web and get the results already scraped

Configuration is a key (``FIRECRAWL_API_KEY``) and, optionally, a base URL —
Firecrawl is open source, so ``FIRECRAWL_API_URL`` can point at a self-hosted
instance instead of api.firecrawl.dev.

The target URL is screened by core.ssrf_guard exactly as web_fetch screens its
own. Firecrawl normally fetches from its own infrastructure, where our LAN is
unreachable — but a *self-hosted* instance sits inside the LAN, and there the
model's URL would otherwise be a way to reach 169.254.169.254 or an admin panel
through a service we control.
"""
import asyncio

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from client import _handle_error
from config import cfg

DEFAULT_API = "https://api.firecrawl.dev"

# A crawl is asynchronous: Firecrawl returns a job id and works in the background.
# A tool call cannot wait forever, so it polls to a ceiling and then hands back
# whatever finished plus the job id — partial results beat a timeout.
CRAWL_POLL_SECONDS = 3.0
CRAWL_MAX_WAIT = 120.0

NEEDS_KEY = ("Error: this needs a Firecrawl key. Add FIRECRAWL_API_KEY in "
             "Settings → Firecrawl (get one at https://firecrawl.dev), or point "
             "FIRECRAWL_API_URL at a self-hosted instance.")


def firecrawl_base() -> str:
    return (getattr(cfg, "firecrawl_api_url", "") or DEFAULT_API).rstrip("/")


def firecrawl_key() -> str:
    return (getattr(cfg, "firecrawl_api_key", "") or "").strip()


def firecrawl_configured() -> bool:
    """A key, or a self-hosted instance that may not need one."""
    return bool(firecrawl_key()) or firecrawl_base() != DEFAULT_API


def _headers() -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": "PlutusMCP/1.0"}
    key = firecrawl_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


async def _screen(url: str) -> str:
    from core.ssrf_guard import screen_url
    return await asyncio.to_thread(screen_url, url) or ""


async def _call(method: str, path: str, payload: dict | None = None,
                *, timeout: float = 120.0) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        r = await client.request(method, f"{firecrawl_base()}{path}",
                                 json=payload, headers=_headers())
        r.raise_for_status()
        return r.json() if r.content else {}


def _explain(e: Exception, what: str) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (401, 403):
            return ("Error: Firecrawl rejected the key (%d). Check FIRECRAWL_API_KEY "
                    "in Settings → Firecrawl." % code)
        if code == 402:
            return "Error: Firecrawl credits exhausted — top up or wait for the reset."
        if code == 429:
            return "Error: Firecrawl rate limit reached. Try again shortly."
        if code == 408 or code == 504:
            return f"Error: Firecrawl timed out rendering {what}. Try a specific page."
        return f"Error: Firecrawl returned {code}: {e.response.text[:200]}"
    return _handle_error(e, "Firecrawl")


def _page_markdown(doc: dict) -> tuple[str, str, str]:
    """(title, url, markdown) from a Firecrawl document, v1 or v2 shaped."""
    if not isinstance(doc, dict):
        return "", "", ""
    meta = doc.get("metadata") or {}
    title = meta.get("title") or meta.get("ogTitle") or ""
    url = meta.get("sourceURL") or meta.get("url") or doc.get("url") or ""
    body = doc.get("markdown") or doc.get("content") or doc.get("html") or ""
    return str(title), str(url), str(body)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n… [{len(text) - limit} more characters]"


def register_scrape_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class ScrapeInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        url: str = Field(..., description="Page to read", min_length=4, max_length=2000)
        max_chars: int = Field(default=15000, ge=500, le=100_000)
        main_content_only: bool = Field(
            default=True, description="Strip nav, footers and sidebars")
        include_links: bool = Field(default=False, description="Also list the page's links")

    @mcp.tool(name="firecrawl_scrape", annotations={"readOnlyHint": True})
    async def firecrawl_scrape(params: ScrapeInput) -> str:
        """Read a web page as Markdown, with JavaScript rendered.

        Use this when web_fetch returns little or nothing — single-page apps,
        React documentation, anything that builds its content in the browser.
        """
        if not firecrawl_configured():
            return NEEDS_KEY
        blocked = await _screen(params.url)
        if blocked:
            return f"Error: {blocked}"
        formats = ["markdown"] + (["links"] if params.include_links else [])
        try:
            body = await _call("POST", "/v2/scrape", {
                "url": params.url, "formats": formats,
                "onlyMainContent": params.main_content_only})
        except Exception as e:
            return _explain(e, params.url)
        doc = body.get("data") if isinstance(body.get("data"), dict) else body
        title, url, markdown = _page_markdown(doc)
        if not markdown:
            return (f"Firecrawl reached {params.url} but found no readable content. "
                    "The page may be an image, a download, or blocked.")
        out = [f"## {title or params.url}", ""]
        if url:
            out.append(f"_{url}_\n")
        out.append(_clip(markdown, params.max_chars))
        links = (doc or {}).get("links") or []
        if params.include_links and links:
            out.append("\n### Links\n" + "\n".join(f"- {x}" for x in links[:100]))
        return "\n".join(out)

    class MapInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        url: str = Field(..., description="Site to map, e.g. https://docs.example.com",
                         min_length=4, max_length=2000)
        search: str = Field(default="", description="Only URLs matching this term")
        limit: int = Field(default=100, ge=1, le=1000)

    @mcp.tool(name="firecrawl_map", annotations={"readOnlyHint": True})
    async def firecrawl_map(params: MapInput) -> str:
        """List a site's URLs without fetching each page.

        The cheap first step for research: map the site, pick the handful of pages
        worth reading, then scrape only those.
        """
        if not firecrawl_configured():
            return NEEDS_KEY
        blocked = await _screen(params.url)
        if blocked:
            return f"Error: {blocked}"
        payload: dict = {"url": params.url, "limit": params.limit}
        if params.search:
            payload["search"] = params.search
        try:
            body = await _call("POST", "/v2/map", payload, timeout=90.0)
        except Exception as e:
            return _explain(e, params.url)
        raw = body.get("links") or (body.get("data") or {}).get("links") or []
        rows = []
        for item in raw[:params.limit]:
            if isinstance(item, str):
                rows.append((item, ""))
            elif isinstance(item, dict):
                rows.append((item.get("url") or "", item.get("title") or ""))
        rows = [r for r in rows if r[0]]
        if not rows:
            return f"Firecrawl found no URLs under {params.url}."
        lines = [f"## {len(rows)} URLs under {params.url}", ""]
        for url, title in rows:
            lines.append(f"- {url}" + (f"\n  {title}" if title else ""))
        return "\n".join(lines)

    class CrawlInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        url: str = Field(..., description="Where to start", min_length=4, max_length=2000)
        limit: int = Field(default=10, ge=1, le=100, description="Max pages")
        max_depth: int = Field(default=2, ge=1, le=5)
        max_chars_per_page: int = Field(default=4000, ge=200, le=20_000)

    @mcp.tool(name="firecrawl_crawl", annotations={"readOnlyHint": True})
    async def firecrawl_crawl(params: CrawlInput) -> str:
        """Crawl a section of a site and return each page as Markdown.

        Slower and more expensive than scraping one page — map the site first
        unless you genuinely want everything under a path.
        """
        if not firecrawl_configured():
            return NEEDS_KEY
        blocked = await _screen(params.url)
        if blocked:
            return f"Error: {blocked}"
        try:
            started = await _call("POST", "/v2/crawl", {
                "url": params.url, "limit": params.limit,
                "maxDiscoveryDepth": params.max_depth,
                "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True}})
        except Exception as e:
            return _explain(e, params.url)

        job = started.get("id") or started.get("jobId") or ""
        if not job:
            # Some deployments answer synchronously; take the data if it is there.
            docs = started.get("data") or []
            return _render_crawl(params.url, docs, params.max_chars_per_page, "")
        # A wall-clock deadline, not an accumulated counter: adding the poll
        # interval to a total never terminates if that interval is ever zero, and
        # it drifts from real time whenever a poll itself is slow.
        deadline = asyncio.get_running_loop().time() + CRAWL_MAX_WAIT
        status: dict = {}
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(CRAWL_POLL_SECONDS)
            try:
                status = await _call("GET", f"/v2/crawl/{job}", timeout=60.0)
            except Exception as e:
                return _explain(e, params.url)
            if str(status.get("status") or "").lower() in ("completed", "failed", "cancelled"):
                break
        docs = status.get("data") or []
        unfinished = "" if str(status.get("status") or "").lower() == "completed" else job
        return _render_crawl(params.url, docs, params.max_chars_per_page, unfinished)

    def _render_crawl(root: str, docs: list, per_page: int, unfinished: str) -> str:
        pages = [d for d in (docs or []) if isinstance(d, dict)]
        if not pages:
            if unfinished:
                return (f"Crawl of {root} is still running (job `{unfinished}`) and has "
                        "returned nothing yet. Try a smaller limit or depth.")
            return f"Firecrawl found no pages under {root}."
        lines = [f"## Crawled {len(pages)} pages under {root}", ""]
        for doc in pages:
            title, url, markdown = _page_markdown(doc)
            lines.append(f"### {title or url or 'Untitled'}")
            if url:
                lines.append(f"_{url}_")
            lines.append("")
            lines.append(_clip(markdown, per_page))
            lines.append("")
        if unfinished:
            lines.append(f"_Still running — this is a partial result (job `{unfinished}`)._")
        return "\n".join(lines)

    class SearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="What to search for", min_length=1, max_length=400)
        limit: int = Field(default=5, ge=1, le=20)
        scrape: bool = Field(default=True, description="Also return each result's content")
        max_chars_per_result: int = Field(default=3000, ge=200, le=20_000)

    @mcp.tool(name="firecrawl_search", annotations={"readOnlyHint": True})
    async def firecrawl_search(params: SearchInput) -> str:
        """Search the web and get the results already read.

        One call instead of search-then-fetch-each, and the content comes back
        rendered, so JavaScript-heavy results are readable too.
        """
        if not firecrawl_configured():
            return NEEDS_KEY
        payload: dict = {"query": params.query, "limit": params.limit}
        if params.scrape:
            payload["scrapeOptions"] = {"formats": ["markdown"], "onlyMainContent": True}
        try:
            body = await _call("POST", "/v2/search", payload)
        except Exception as e:
            return _explain(e, params.query)
        data = body.get("data")
        # v2 groups by source ({"web": [...]}), v1 returns a flat list.
        if isinstance(data, dict):
            results = data.get("web") or data.get("results") or []
        else:
            results = data or []
        results = [r for r in results if isinstance(r, dict)]
        if not results:
            return f"No results for '{params.query}'."
        lines = [f"## Search: '{params.query}'", ""]
        for r in results:
            title = r.get("title") or r.get("url") or ""
            url = r.get("url") or ""
            lines.append(f"### {title}")
            if url:
                lines.append(f"_{url}_")
            snippet = r.get("description") or r.get("snippet") or ""
            if snippet:
                lines.append(f"\n{snippet}")
            markdown = r.get("markdown") or ""
            if markdown:
                lines.append("\n" + _clip(markdown, params.max_chars_per_result))
            lines.append("")
        return "\n".join(lines)

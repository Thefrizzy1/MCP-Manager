"""
tools/huggingface.py — Hugging Face Hub model research (real calls, public API).

Discover models, see what's trending, and inspect a model's metadata. Hits the
public https://huggingface.co/api — no key required; an optional HF_TOKEN raises
rate limits and allows private repos. Read-only. Follows the service contract:
real HTTP, structured output, graceful errors (never fake success).
"""
import httpx
from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import TIMEOUT, _handle_error

API = "https://huggingface.co/api"


def _fmt_count(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v or "—")
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if n >= size:
            return f"{n / size:.1f}{unit}".replace(".0", "")
    return str(n)


async def _get(path: str, params: dict) -> object:
    headers = {"User-Agent": "PlutusMCP/1.0"}
    token = (getattr(cfg, "hf_token", "") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.get(f"{API}/{path}", params=params, headers=headers)
        r.raise_for_status()
        return r.json()


def _model_line(m: dict) -> str:
    mid = m.get("id") or m.get("modelId") or ""
    task = m.get("pipeline_tag") or ""
    dl = _fmt_count(m.get("downloads"))
    likes = _fmt_count(m.get("likes"))
    tag = f" · {task}" if task else ""
    return f"- **{mid}**{tag} — ↓{dl} · ♥{likes}\n  https://huggingface.co/{mid}"


def register_huggingface_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    class SearchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        query: str = Field(..., description="Search terms (name, task, author)", min_length=1, max_length=200)
        sort: str = Field(default="downloads", description="downloads | likes | trending | lastModified")
        limit: int = Field(default=10, description="Max results (1–30)", ge=1, le=30)

    @mcp.tool(name="huggingface_search_models", annotations={"readOnlyHint": True})
    async def huggingface_search_models(params: SearchInput) -> str:
        """Search the Hugging Face Hub for models by keyword, sorted by downloads/likes."""
        try:
            sort = {"trending": "trendingScore"}.get(params.sort, params.sort)
            data = await _get("models", {
                "search": params.query, "sort": sort, "direction": "-1",
                "limit": params.limit, "full": "false",
            })
            items = data if isinstance(data, list) else []
            if not items:
                return f"## Hugging Face models: '{params.query}'\n\nNo models found."
            lines = [f"## Hugging Face models: '{params.query}'\n"] + [_model_line(m) for m in items]
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e, "Hugging Face search")

    class TrendingInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        task: str = Field(default="", description="Optional pipeline tag filter, e.g. text-to-image", max_length=60)
        limit: int = Field(default=10, description="Max results (1–30)", ge=1, le=30)

    @mcp.tool(name="huggingface_trending_models", annotations={"readOnlyHint": True})
    async def huggingface_trending_models(params: TrendingInput) -> str:
        """The models trending on Hugging Face right now (optionally filtered by task)."""
        try:
            q = {"sort": "trendingScore", "direction": "-1", "limit": params.limit, "full": "false"}
            if params.task.strip():
                q["pipeline_tag"] = params.task.strip()
            data = await _get("models", q)
            items = data if isinstance(data, list) else []
            if not items:
                return "## Trending on Hugging Face\n\nNothing returned."
            head = "## Trending on Hugging Face" + (f" — {params.task}" if params.task.strip() else "")
            return "\n".join([head + "\n"] + [_model_line(m) for m in items])
        except Exception as e:
            return _handle_error(e, "Hugging Face trending")

    class ModelInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        model_id: str = Field(..., description="Full model id, e.g. black-forest-labs/FLUX.1-dev", min_length=1, max_length=200)

    @mcp.tool(name="huggingface_model_info", annotations={"readOnlyHint": True})
    async def huggingface_model_info(params: ModelInput) -> str:
        """Metadata for one model — task, downloads, likes, license, tags, files."""
        try:
            m = await _get(f"models/{params.model_id.strip()}", {})
            if not isinstance(m, dict) or not (m.get("id") or m.get("modelId")):
                return f"No model found for '{params.model_id}'."
            card = m.get("cardData") or {}
            tags = [t for t in (m.get("tags") or []) if not t.startswith(("arxiv:", "license:"))][:10]
            files = [s.get("rfilename", "") for s in (m.get("siblings") or [])]
            weights = [f for f in files if f.endswith((".safetensors", ".gguf", ".bin", ".ckpt"))][:6]
            return (
                f"## {m.get('id') or m.get('modelId')}\n\n"
                f"**Task:** {m.get('pipeline_tag') or '—'}\n"
                f"**Downloads:** {_fmt_count(m.get('downloads'))}\n"
                f"**Likes:** {_fmt_count(m.get('likes'))}\n"
                f"**License:** {card.get('license') or '—'}\n"
                f"**Library:** {m.get('library_name') or '—'}\n"
                f"**Tags:** {', '.join(tags) or '—'}\n"
                f"**Weight files:** {', '.join(weights) or '—'}\n"
                f"**Link:** https://huggingface.co/{m.get('id') or m.get('modelId')}"
            )
        except Exception as e:
            return _handle_error(e, "Hugging Face model info")

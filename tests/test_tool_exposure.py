"""Tool-exposure slicer — proves it actually shrinks the served MCP manifest.

The slicer's whole job is token optimisation: disabling tool categories must
remove those tool schemas from the served /mcp instance so the manifest (real
prompt tokens) gets smaller — without ever touching what a tool does. This locks
that in so the slicer can't silently stop reducing tokens.
"""
from __future__ import annotations

from core.profiles import ALWAYS_EXPOSED, TOOL_CATEGORIES, infer_tool_categories
from core import tool_exposure as te
from ui.runtime import all_tool_names, build_mcp, mcp

NAMES = all_tool_names()
MGR = mcp._tool_manager


def _heavy_categories(n: int = 3) -> list[str]:
    """The n categories with the most tools, so disabling them clearly cuts the manifest."""
    counts: dict[str, int] = {c: 0 for c in TOOL_CATEGORIES}
    for name in NAMES:
        for c in infer_tool_categories(name):
            counts[c] += 1
    return [c for c, k in sorted(counts.items(), key=lambda kv: kv[1], reverse=True) if k > 0][:n]


def test_nothing_disabled_reuses_full_surface(tmp_path):
    # None signals "serve the prebuilt full instance" — no filtering overhead.
    assert te.resolve_exposed(tmp_path, NAMES) is None


def test_disabling_categories_shrinks_the_set(tmp_path):
    te.save_exposure(tmp_path, _heavy_categories())
    exposed = te.resolve_exposed(tmp_path, NAMES)
    assert exposed is not None
    assert exposed < set(NAMES), "expected a strict subset after disabling categories"
    assert ALWAYS_EXPOSED <= exposed, "meta tools must always stay exposed"


def test_report_quantifies_token_saving(tmp_path):
    te.save_exposure(tmp_path, _heavy_categories())
    rep = te.exposure_report(tmp_path, MGR)
    assert rep["exposed_tools"] < rep["total_tools"]
    assert rep["exposed_tokens_est"] < rep["total_tokens_est"]
    assert rep["tokens_saved_est"] > 0
    assert 0 < rep["percent_saved"] <= 100


def test_build_mcp_actually_serves_fewer_tools(tmp_path):
    te.save_exposure(tmp_path, _heavy_categories())
    exposed = te.resolve_exposed(tmp_path, NAMES)
    sub = build_mcp("plutus-sliced", exposed)
    assert len(sub._tool_manager.list_tools()) < len(NAMES)


def test_invalid_category_is_ignored(tmp_path):
    te.save_exposure(tmp_path, ["not_a_real_category", "also_bogus"])
    # nothing valid disabled -> full surface
    assert te.resolve_exposed(tmp_path, NAMES) is None


def test_disabling_everything_still_keeps_meta(tmp_path):
    te.save_exposure(tmp_path, list(TOOL_CATEGORIES))
    exposed = te.resolve_exposed(tmp_path, NAMES)
    assert exposed is not None
    assert ALWAYS_EXPOSED <= exposed, "the slicer must never strip its own meta tools"

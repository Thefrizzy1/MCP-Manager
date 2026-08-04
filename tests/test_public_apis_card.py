"""Public APIs: one card, per-API switches that really shrink the MCP manifest.

Nine near-identical "Public · …" cards crowded the Connections page and none of
them had anything to configure. They are now one card whose Configure lists every
free API individually — and switching one off has to remove it from the served
/mcp instance, not merely hide a tile, or the tool count and token cost never move.
"""
from __future__ import annotations

from core import tool_exposure as TE
from tools import public_apis_bulk as P


# ── the card ─────────────────────────────────────────────────────────────────

def test_public_apis_are_one_card_not_nine():
    assert len(P.PUBLIC_SERVICES_DASHBOARD) == 1
    card = P.PUBLIC_SERVICES_DASHBOARD[0]
    assert card["id"] == "public_apis"
    assert card["manager"] == "public_apis"
    # Every tool from every old group is still reachable through it.
    assert len(card["tools"]) == len(P.public_tool_names()) >= 50


def test_groups_survive_as_subheadings():
    groups = P.public_api_groups()
    assert len(groups) == 9
    assert {g["id"] for g in groups} >= {"pub_network", "pub_geo_time", "pub_finance_crypto"}
    # No tool is lost or duplicated between the groups and the flat list.
    from itertools import chain
    grouped = list(chain.from_iterable(g["tools"] for g in groups))
    assert sorted(grouped) == sorted(P.public_tool_names())


def test_catalog_metadata_keeps_per_group_tags():
    """Built from the groups, not the merged card — otherwise every public tool
    would collapse to the 'utilities' tag."""
    tags = {tag for _n, _l, tag in P.PUBLIC_CATALOG_META}
    assert len(tags) > 1, tags
    assert "finance" in tags


# ── per-tool exposure ────────────────────────────────────────────────────────

def test_disabling_a_public_api_removes_it_from_the_served_surface(tmp_path):
    names = P.public_tool_names()
    victim, keeper = names[0], names[1]

    TE.save_exposure(tmp_path, disabled_tools=[victim])
    exposed = TE.resolve_exposed(tmp_path, names)

    assert exposed is not None, "something is disabled, so the full instance must not be reused"
    assert victim not in exposed
    assert keeper in exposed


def test_nothing_disabled_still_returns_none(tmp_path):
    """None lets build_mcp_asgi_app reuse the prebuilt full instance."""
    assert TE.resolve_exposed(tmp_path, P.public_tool_names()) is None


def test_a_per_tool_switch_is_finer_grained_than_the_category_slicer(tmp_path):
    """Why per-tool switches are needed at all: the category slicer is all-or-
    nothing, so turning off one public API via categories would take every tool
    sharing that category with it. The switch must remove exactly one."""
    from core.profiles import infer_tool_categories

    names = P.public_tool_names()
    victim = next(n for n in names if infer_tool_categories(n))
    cats = infer_tool_categories(victim)
    siblings = [n for n in names if n != victim and infer_tool_categories(n) & cats]
    assert siblings, "expected the victim to share a category with other tools"

    # Category route: the sibling is collateral damage.
    assert TE.is_tool_exposed(victim, cats, set()) is False
    assert TE.is_tool_exposed(siblings[0], cats, set()) is False

    # Per-tool route: only the victim goes.
    assert TE.is_tool_exposed(victim, set(), {victim}) is False
    assert TE.is_tool_exposed(siblings[0], set(), {victim}) is True


def test_meta_tools_cannot_be_switched_off(tmp_path):
    """Disabling the slicer itself would remove the means of re-enabling anything."""
    from core.profiles import ALWAYS_EXPOSED

    meta = sorted(ALWAYS_EXPOSED)[0]
    assert TE.is_tool_exposed(meta, set(), {meta}) is True


def test_categories_and_per_tool_switches_do_not_clobber_each_other(tmp_path):
    TE.save_exposure(tmp_path, disabled_tools=["pub_xkcd_current"])
    TE.save_exposure(tmp_path, ["media"])                      # category-only save
    ex = TE.load_exposure(tmp_path)
    assert ex["disabled_categories"] == ["media"]
    assert ex["disabled_tools"] == ["pub_xkcd_current"], "per-tool switches were wiped"

    TE.save_exposure(tmp_path, disabled_tools=[])              # tools-only save
    ex = TE.load_exposure(tmp_path)
    assert ex["disabled_categories"] == ["media"], "categories were wiped"
    assert ex["disabled_tools"] == []


def test_garbage_tool_names_are_rejected_on_load(tmp_path):
    (tmp_path / "data").mkdir()
    TE.exposure_path(tmp_path).write_text(
        '{"disabled_tools": ["ok_name", "../../etc/passwd", "Bad Name", 42, ""]}',
        encoding="utf-8")
    assert TE.load_exposure(tmp_path)["disabled_tools"] == ["ok_name"]


def test_exposure_report_counts_per_tool_savings(tmp_path):
    class _T:
        def __init__(self, name):
            self.name = name
            self.description = "d" * 100
            self.parameters = {"type": "object"}

    names = P.public_tool_names()[:6]

    class _Mgr:
        def list_tools(self):
            return [_T(n) for n in names]

    before = TE.exposure_report(tmp_path, _Mgr())
    TE.save_exposure(tmp_path, disabled_tools=names[:3])
    after = TE.exposure_report(tmp_path, _Mgr())

    assert before["tokens_saved_est"] == 0
    assert after["tokens_saved_est"] > 0
    assert after["exposed_tools"] == before["exposed_tools"] - 3
    assert after["disabled_tools"] == sorted(names[:3])


def test_wikipedia_identifies_itself_the_way_wikimedia_requires():
    """Wikimedia enforces its robot policy on the User-Agent: without a contact
    URL the API answers 403. Verified live — the old
    "PlutusMCP/1.0 (utilities; contact: local)" got 403, the repo URL got 200 —
    so this is what stood between wikipedia_summary and working at all."""
    from tools.utilities import WIKIMEDIA_UA

    assert "http" in WIKIMEDIA_UA, "the UA must carry a contact URL"
    assert "contact: local" not in WIKIMEDIA_UA

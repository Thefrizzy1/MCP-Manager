"""Named kinds of agent: folder, tool slice, capability defaults.

The interesting part is precedence. A preset supplies defaults; it must never
widen what the caller explicitly asked for, or ticking "read-only" would be
silently undone by whichever preset happened to be selected.
"""
from __future__ import annotations

import time

import pytest

from core import agent_presets as P


def test_placeholders_resolve_against_a_fixed_date():
    when = time.mktime(time.strptime("2026-08-02", "%Y-%m-%d"))
    assert P.resolve_folder("research/weekly/{year}-W{week}", when) == "research/weekly/2026-W31"
    assert P.resolve_folder("analysis/{year}-{month}", when) == "analysis/2026-08"
    assert P.resolve_folder("x/{quarter}", when) == "x/Q3"
    assert P.resolve_folder("x/{date}", when) == "x/2026-08-02"


def test_an_unknown_placeholder_is_left_alone_not_raised():
    """A typo in a preset should give an odd folder name, not a failed run."""
    assert P.resolve_folder("x/{nonsense}") == "x/{nonsense}"


def test_empty_template_stays_empty():
    assert P.resolve_folder("") == ""


def test_unknown_preset_falls_back_to_the_unrestricted_default():
    assert P.get_preset("nope")["label"] == P.PRESETS[P.DEFAULT_PRESET]["label"]
    assert P.get_preset(None)["services"] is None
    assert P.preamble("nope") == ""


def test_general_adds_nothing_to_the_prompt():
    assert P.preamble("general") == ""
    assert P.get_preset("general")["services"] is None, (
        "None means 'do not narrow'; [] would mean 'no connections at all'")


def test_the_preamble_stays_short(tmp_path):
    """The whole point is that it is four lines. A standing instruction long
    enough to compete with the task is one the model stops following."""
    for name in P.preset_names():
        block = P.preamble(name, root=tmp_path)
        assert len(block) < 700, f"{name} preamble is {len(block)} chars"


def test_the_preamble_names_the_folder_and_the_tool(tmp_path):
    block = P.preamble("weekly_research", root=tmp_path)
    assert P.resolve_folder(P.PRESETS["weekly_research"]["folder"]) in block
    assert "library_write_file" in block
    assert "db_write_note" in block, "the always-available fallback must be named"


def test_a_read_only_preset_says_so_up_front(tmp_path):
    """Otherwise it is discovered by a failed call, which wastes a turn and
    reads to the model like a broken tool."""
    block = P.preamble("data_analyst", root=tmp_path)
    assert "read-only" in block
    assert P.preamble("weekly_research", root=tmp_path).count("read-only") == 0


def test_preamble_creates_the_folder(tmp_path):
    P.preamble("weekly_research", root=tmp_path)
    folder = P.resolve_folder(P.PRESETS["weekly_research"]["folder"])
    from core.library import resolve_in_library

    assert resolve_in_library(folder, tmp_path).is_dir()


def test_every_preset_names_real_connections():
    """A preset pointing at a connection id that does not exist would silently
    grant nothing — the ACL denies by service id."""
    from ui.runtime import _services_live

    known = {s["id"] for s in _services_live()}
    for name, spec in P.PRESETS.items():
        for svc in spec["services"] or []:
            assert svc in known, f"preset {name!r} names unknown connection {svc!r}"


def test_public_presets_resolve_folders_for_the_wizard():
    rows = {r["id"]: r for r in P.public_presets()}
    assert set(rows) == set(P.preset_names())
    assert "{" not in rows["weekly_research"]["folder"]


# ── precedence ───────────────────────────────────────────────────────────────

def test_preset_supplies_connections_only_when_none_were_chosen():
    from ui.runtime import apply_preset

    _, services, _, _ = apply_preset("go", "weekly_research", None, True, False)
    assert services == P.PRESETS["weekly_research"]["services"]

    # An explicit choice wins — including a deliberately narrow one.
    _, services, _, _ = apply_preset("go", "weekly_research", ["nextcloud"], True, False)
    assert services == ["nextcloud"]


@pytest.mark.parametrize("preset,asked,expected", [
    # A read-only preset cannot be talked into writing by a stale tick box.
    ("data_analyst", True, False),
    # And ticking read-only wins over a preset that allows writes.
    ("weekly_research", False, False),
    ("weekly_research", True, True),
])
def test_capabilities_are_anded_never_widened(preset, asked, expected):
    from ui.runtime import apply_preset

    _, _, allow_write, _ = apply_preset("go", preset, None, asked, False)
    assert allow_write is expected


def test_the_block_is_prepended_not_substituted():
    from ui.runtime import apply_preset

    prompt, _, _, _ = apply_preset("Find me three sources.", "weekly_research",
                                   None, True, False)
    assert prompt.endswith("Find me three sources.")
    assert "Where your work goes" in prompt


def test_no_preset_changes_nothing():
    from ui.runtime import apply_preset

    args = ("go", "", ["nextcloud"], True, True)
    assert apply_preset(*args) == ("go", ["nextcloud"], True, True)


def test_a_preset_actually_shrinks_the_tool_manifest():
    """The connection list is the tool slice — that is what makes a preset
    cheaper, not just narrower."""
    from ui.runtime import (_agent_capability_disallow, _agent_service_disallow,
                            apply_preset)

    _, services, w, pub = apply_preset("go", "data_analyst", None, True, False)
    denied = set(_agent_service_disallow(services)) | set(_agent_capability_disallow(w, pub))
    assert len(denied) > 100, f"only {len(denied)} tools withheld — the slice is not biting"

"""Every registered tool must declare all four annotation hints (workstream B4).

This makes it impossible to add an unannotated tool later: the annotation
completer (core/tool_annotations.py) fills any gap at registration, and this
test fails if some path around it ever leaves a hint None.
"""
from __future__ import annotations

from core.tool_annotations import REQUIRED_HINTS
from ui.runtime import mcp


def _tools():
    return mcp._tool_manager.list_tools()


def test_every_tool_declares_all_four_hints():
    missing = []
    for t in _tools():
        a = t.annotations
        for h in REQUIRED_HINTS:
            if a is None or getattr(a, h, None) is None:
                missing.append(f"{t.name}.{h}")
    assert not missing, "tools missing annotation hints:\n  " + "\n  ".join(missing)


def test_all_hint_values_are_booleans():
    for t in _tools():
        a = t.annotations
        for h in REQUIRED_HINTS:
            assert isinstance(getattr(a, h), bool), f"{t.name}.{h} is not a bool"


def test_read_only_tools_are_not_destructive():
    for t in _tools():
        a = t.annotations
        if a.readOnlyHint:
            assert a.destructiveHint is False, f"{t.name} is read-only but marked destructive"

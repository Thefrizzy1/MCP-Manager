"""Shared status heuristics for legacy text-returning tools."""

from __future__ import annotations


def text_looks_successful(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    if low.startswith("error:"):
        return False
    # Scan only the head, not the whole body. A working tool's *content* — a web
    # page, a Reddit thread, a weather report, a wiki summary — can legitimately
    # contain words like "traceback" or "typeerror", and scanning the entire
    # output flipped those healthy results to FAIL. That prose-grepping was the
    # main source of smoke-test flakiness (see docs/ARCHITECTURE_AUDIT.md §4).
    # Real tool errors are error-shaped and land at the very start: "Error: …",
    # "Traceback (most recent call last):", "N validation error(s) for …".
    head = low.split("\n", 1)[0]
    return not any(
        marker in head
        for marker in (
            "validation error",
            "field required",
            "unexpected field",
            "missing field",
            "traceback",
            "typeerror",
            "valueerror",
        )
    )

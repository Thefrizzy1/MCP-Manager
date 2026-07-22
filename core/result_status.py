"""Shared status heuristics for legacy text-returning tools."""

from __future__ import annotations


def text_looks_successful(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    if low.startswith("error:"):
        return False
    return not any(
        marker in low
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

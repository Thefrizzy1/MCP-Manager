"""Saved playbooks exposed as MCP prompts.

Each playbook (core/agent_tasks.py, data/agent_tasks.json) becomes a prompt, so
it appears as a slash-command in every connected client. Rendering is pure string
substitution of the ``{{PLACEHOLDER}}`` tokens in the playbook body — no model
call. ``{{LIBRARY}}``, ``{{DATE}}`` and ``{{OUTPUT_HINT}}`` default to the
configured research destination / today; a client may override any placeholder by
passing it as a prompt argument.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from mcp.server.fastmcp.prompts.base import Prompt, PromptArgument

from core import agent_runner, agent_tasks

_ROOT = Path(__file__).resolve().parents[1]
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _placeholders(body: str) -> list[str]:
    """Ordered, unique ``{{TOKEN}}`` names in the body."""
    seen: list[str] = []
    for m in _PLACEHOLDER_RE.finditer(body):
        tok = m.group(1)
        if tok not in seen:
            seen.append(tok)
    return seen


def _render(body: str, overrides: dict) -> str:
    acfg = agent_runner.load_agent_config(_ROOT)
    lib, hint = agent_runner.resolve_library(acfg)
    ctx = {"LIBRARY": lib, "DATE": time.strftime("%Y-%m-%d"), "OUTPUT_HINT": hint}
    for k, v in (overrides or {}).items():
        if v is not None:
            ctx[str(k).upper()] = str(v)
    return _PLACEHOLDER_RE.sub(lambda m: ctx.get(m.group(1), ""), body)


def _make_fn(body: str):
    def _fn(**kwargs) -> str:
        return _render(body, kwargs)
    return _fn


def register_prompt_tools(mcp, *, allow: "set[str] | None" = None) -> None:
    """Register each saved playbook as an MCP prompt.

    ``allow`` follows the profile model: playbook ids aren't tool names, so a
    filtered profile (``allow`` is a set) gets no prompts; the full surface
    (``allow`` is None) gets them all.
    """
    for pb in agent_tasks.load_tasks(_ROOT):
        pid = str(pb.get("id") or "").strip()
        if not pid:
            continue
        if allow is not None and pid not in allow:
            continue
        body = str(pb.get("prompt") or "")
        args = [
            PromptArgument(name=ph.lower(), description=f"Overrides {{{{{ph}}}}} in the playbook.", required=False)
            for ph in _placeholders(body)
        ]
        mcp.add_prompt(Prompt(
            name=pid,
            description=(str(pb.get("description") or pb.get("name") or pid))[:200],
            arguments=args,
            fn=_make_fn(body),
        ))

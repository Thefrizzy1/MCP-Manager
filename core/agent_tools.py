"""Plutus's MCP tools, reshaped for a model that has no MCP support.

Claude Code and Codex speak MCP, so they get the tool surface verbatim (via
``--mcp-config`` and the stdio bridge respectively). Gemini does not speak MCP at
all — it has *function calling*, which is the same idea with a different wire
format and a stricter schema dialect. This module is the translation.

Two jobs:

1. **Schema conversion.** Plutus's tool schemas come from Pydantic, so they are
   full JSON Schema: ``$ref`` into ``$defs``, ``anyOf`` with a null branch for
   optionals, ``default``/``title``/``minimum`` annotations. Gemini accepts a
   small OpenAPI subset and rejects the request outright on anything else — one
   unresolved ``$ref`` fails every tool call in the run, so this is not
   best-effort cleanup, it is a hard requirement.

2. **Scope.** The launch wizard's connection picker produces a disallow list; the
   same list decides which functions are declared. An agent must not be *told*
   about a tool it is not allowed to call — offering it and then refusing is a
   worse experience than never mentioning it.
"""
from __future__ import annotations

from typing import Any

MCP_PREFIX = "mcp__plutus__"

# Gemini rejects a request carrying too many declarations, and every declaration
# is tokens on every turn of the loop. The full surface is ~209 tools / ~27k
# tokens, so an unscoped run would spend most of its budget describing tools it
# never calls. Narrowing connections in the launch wizard is the real fix; this
# is the backstop that keeps the request valid.
MAX_DECLARATIONS = 128

# JSON Schema keywords Gemini's dialect does not accept. Dropped rather than
# translated: they are annotations, and losing them costs nothing a model needs.
_DROP = frozenset({
    "$schema", "$defs", "$ref", "$comment", "title", "default", "examples",
    "additionalProperties", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern",
    "minProperties", "maxProperties", "uniqueItems", "allOf", "not",
    "definitions", "discriminator", "readOnly", "writeOnly", "deprecated",
})
_KEEP = ("description", "enum", "format")
_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}


def allowed_tool_names(all_names: list[str], disallowed: list[str] | None) -> list[str]:
    """The tools this run may use, from the run's disallow list.

    ``disallowed`` arrives prefixed (``mcp__plutus__sonarr_queue``) because that
    is the form Claude Code's ``--disallowedTools`` takes; strip it so the same
    list can gate a provider that has never heard of that convention.
    """
    denied = {n[len(MCP_PREFIX):] if n.startswith(MCP_PREFIX) else n
              for n in (disallowed or [])}
    return [n for n in all_names if n not in denied]


def to_gemini_schema(schema: Any, defs: dict | None = None, depth: int = 0) -> dict:
    """One JSON Schema into Gemini's OpenAPI subset.

    ``$ref`` is resolved against ``$defs`` because Gemini has no notion of it,
    and every Plutus tool wraps its arguments in a ``$ref``-ed model — so without
    this, no tool would be callable at all.
    """
    if not isinstance(schema, dict):
        return {"type": "string"}
    defs = defs if defs is not None else (schema.get("$defs") or {})
    if depth > 12:
        # Self-referential models exist; a bounded fallback beats a stack overflow.
        return {"type": "object", "description": "nested value"}

    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        target = defs.get(name)
        if not isinstance(target, dict):
            return {"type": "object"}
        merged = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
        return to_gemini_schema(merged, defs, depth + 1)

    # Optionals arrive as anyOf[X, null]. Gemini expresses that as nullable on X.
    branches = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(branches, list) and branches:
        real = [b for b in branches
                if not (isinstance(b, dict) and b.get("type") == "null")]
        nullable = len(real) != len(branches)
        picked = to_gemini_schema(real[0], defs, depth + 1) if real else {"type": "string"}
        for k in _KEEP:
            if k in schema and k not in picked:
                picked[k] = schema[k]
        if nullable:
            picked["nullable"] = True
        return picked

    out: dict[str, Any] = {}
    t = schema.get("type")
    if isinstance(t, list):                 # ["string", "null"]
        real = [x for x in t if x != "null"]
        if "null" in t:
            out["nullable"] = True
        t = real[0] if real else "string"
    if isinstance(t, str) and t in _TYPES and t != "null":
        out["type"] = t

    for k in _KEEP:
        v = schema.get(k)
        if v not in (None, [], {}):
            out[k] = v
    # `format` is only safe for the handful Gemini documents; anything else (uri,
    # uuid, binary…) makes it reject the whole declaration.
    if out.get("format") not in (None, "date-time", "date", "time", "enum",
                                 "int32", "int64", "float", "double"):
        out.pop("format", None)

    props = schema.get("properties")
    if isinstance(props, dict):
        out["type"] = "object"
        out["properties"] = {k: to_gemini_schema(v, defs, depth + 1)
                             for k, v in props.items() if isinstance(k, str)}
        req = [r for r in (schema.get("required") or []) if isinstance(r, str)]
        if req:
            out["required"] = req

    items = schema.get("items")
    if isinstance(items, dict):
        out["type"] = "array"
        out["items"] = to_gemini_schema(items, defs, depth + 1)

    if "enum" in out and "type" not in out:
        out["type"] = "string"
    if "type" not in out:
        out["type"] = "object" if "properties" in out else "string"
    # An object with no declared properties is rejected; describe it as free-form
    # rather than sending something invalid.
    if out["type"] == "object" and not out.get("properties"):
        out.pop("required", None)
        out["properties"] = {}
    for k in _DROP:
        out.pop(k, None)
    return out


def gemini_declarations(tools: list[dict], disallowed: list[str] | None = None,
                        *, limit: int = MAX_DECLARATIONS) -> tuple[list[dict], int]:
    """(function declarations, how many were dropped by the cap).

    ``tools`` is an MCP ``tools/list`` payload, so what a Gemini run can reach is
    by construction the same surface Claude reaches — no second registry to drift.
    """
    allowed = set(allowed_tool_names([t.get("name", "") for t in tools], disallowed))
    picked = sorted((t for t in tools if t.get("name") in allowed),
                    key=lambda t: t.get("name", ""))
    dropped = max(0, len(picked) - limit)
    decls = []
    for t in picked[:limit]:
        decls.append({
            "name": t["name"],
            "description": (t.get("description") or t["name"])[:1024],
            "parameters": to_gemini_schema(t.get("inputSchema") or {}),
        })
    return decls, dropped

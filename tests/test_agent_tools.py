"""Plutus's MCP tool schemas, reshaped for Gemini's function calling.

This is not cosmetic cleanup. Gemini validates the whole request: one leftover
``$ref`` or ``additionalProperties`` anywhere in any declaration and the call is
rejected, so *every* tool in the run fails, not just that one. Plutus's schemas
come from Pydantic and are full JSON Schema, so the conversion has to be total.
"""
from __future__ import annotations

from core import agent_tools as AT

# Every keyword Gemini's dialect accepts. Anything else must be gone.
GEMINI_KEYWORDS = {"type", "description", "enum", "format", "nullable",
                   "properties", "required", "items"}


def keywords_outside_the_subset(schema, path="") -> list[str]:
    """Walk a converted schema, skipping property *names* (which are free-form)."""
    bad: list[str] = []

    def walk(node, path, in_props=False):
        if isinstance(node, dict):
            for k, v in node.items():
                if in_props:
                    walk(v, f"{path}.{k}")
                    continue
                if k not in GEMINI_KEYWORDS:
                    bad.append(f"{path}.{k}")
                walk(v, f"{path}.{k}", in_props=(k == "properties"))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(schema, path)
    return bad


# ── the conversions that matter ──────────────────────────────────────────────

def test_a_ref_into_defs_is_inlined():
    """Every Plutus tool wraps its arguments in a $ref-ed Pydantic model, so
    without this no tool would be callable at all."""
    out = AT.to_gemini_schema({
        "$defs": {"Args": {"type": "object", "title": "Args",
                           "properties": {"query": {"type": "string"}},
                           "required": ["query"]}},
        "type": "object",
        "properties": {"params": {"$ref": "#/$defs/Args"}},
        "required": ["params"],
    })
    assert out["properties"]["params"]["properties"]["query"]["type"] == "string"
    assert out["properties"]["params"]["required"] == ["query"]
    assert keywords_outside_the_subset(out) == []


def test_an_optional_becomes_nullable_not_anyof():
    """Pydantic writes Optional[str] as anyOf[string, null]; Gemini has no anyOf
    in this position but does have nullable."""
    out = AT.to_gemini_schema({
        "type": "object",
        "properties": {"media_type": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "default": None, "description": "Filter by type", "title": "Media Type"}},
    })
    field = out["properties"]["media_type"]
    assert field == {"type": "string", "description": "Filter by type", "nullable": True}


def test_annotations_gemini_rejects_are_dropped():
    out = AT.to_gemini_schema({
        "type": "integer", "title": "Limit", "default": 10,
        "minimum": 1, "maximum": 50, "description": "Max results",
    })
    assert out == {"type": "integer", "description": "Max results"}


def test_an_unusable_format_is_dropped_but_a_known_one_stays():
    assert "format" not in AT.to_gemini_schema({"type": "string", "format": "uri"})
    assert AT.to_gemini_schema({"type": "string", "format": "date-time"})["format"] == "date-time"


def test_a_self_referential_schema_terminates():
    node = {"type": "object", "properties": {}}
    node["properties"]["child"] = node          # a cycle, not just deep nesting
    out = AT.to_gemini_schema(node)
    assert keywords_outside_the_subset(out) == []


def test_an_object_with_no_properties_still_declares_some():
    """Gemini rejects an object schema with no properties key."""
    out = AT.to_gemini_schema({"type": "object"})
    assert out == {"type": "object", "properties": {}}


def test_arrays_keep_their_item_type():
    out = AT.to_gemini_schema({"type": "array", "items": {"type": "string"}})
    assert out == {"type": "array", "items": {"type": "string"}}


# ── scope ────────────────────────────────────────────────────────────────────

def test_the_disallow_list_is_honoured_with_or_without_its_prefix():
    """It arrives prefixed because that is what Claude's --disallowedTools takes;
    a provider that never heard of that convention still has to obey it."""
    names = ["a", "b", "c"]
    assert AT.allowed_tool_names(names, ["mcp__plutus__b"]) == ["a", "c"]
    assert AT.allowed_tool_names(names, ["c"]) == ["a", "b"]
    assert AT.allowed_tool_names(names, None) == names


def test_a_denied_tool_is_never_declared():
    tools = [{"name": "keep", "description": "k", "inputSchema": {"type": "object"}},
             {"name": "drop", "description": "d", "inputSchema": {"type": "object"}}]
    decls, dropped = AT.gemini_declarations(tools, ["mcp__plutus__drop"])
    assert [d["name"] for d in decls] == ["keep"] and dropped == 0


def test_the_declaration_cap_reports_what_it_left_out():
    """Silently truncating would leave an agent unable to call a tool it was told
    about — or worse, told about nothing and never saying why."""
    tools = [{"name": f"t{i:03d}", "description": "x", "inputSchema": {"type": "object"}}
             for i in range(200)]
    decls, dropped = AT.gemini_declarations(tools, None, limit=10)
    assert len(decls) == 10 and dropped == 190
    assert [d["name"] for d in decls] == [f"t{i:03d}" for i in range(10)], "stable order"


def test_a_declaration_always_has_a_description():
    """An unnamed function is unusable; fall back to the tool's own name."""
    decls, _ = AT.gemini_declarations(
        [{"name": "solo", "inputSchema": {"type": "object"}}], None)
    assert decls[0]["description"] == "solo"


# ── the whole real surface ───────────────────────────────────────────────────

def test_every_registered_plutus_tool_converts_cleanly():
    """The one test that would actually have caught this class of bug.

    Handcrafted cases only prove the rules I thought of. Plutus registers ~209
    tools from a dozen service modules, and it takes exactly one of them to grow
    a schema shape the converter mishandles for *every* Gemini tool call in the
    product to start failing. So convert the real registry, and assert the result
    is inside Gemini's dialect.
    """
    from ui.runtime import tools as registry

    live = []
    for tool in registry.list_tools():
        schema = getattr(tool, "parameters", None)
        if isinstance(schema, dict):
            live.append({"name": tool.name, "description": tool.description or "",
                         "inputSchema": schema})
    assert len(live) > 50, f"expected the full tool surface, saw {len(live)}"

    decls, _ = AT.gemini_declarations(live, None, limit=10_000)
    assert len(decls) == len(live)

    offenders: list[str] = []
    for d in decls:
        offenders += keywords_outside_the_subset(d["parameters"], d["name"])
    assert offenders == [], f"schema keywords Gemini rejects: {offenders[:8]}"

    # A declaration with no top-level type is rejected outright.
    assert all(d["parameters"].get("type") == "object" for d in decls)

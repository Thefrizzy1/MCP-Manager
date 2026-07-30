"""OpenRouter: one key, a few hundred models, and a free router.

Two things make it different from Gemini and are therefore what these tests are
about:

1. **A different wire format.** OpenAI-compatible ``/chat/completions`` with
   ``messages``, ``tools``, and ``tool_calls`` carrying ids that must be echoed
   back. The agent loop is shared, so if the dialect gets this wrong every tool
   call in every OpenRouter run fails.

2. **Models that differ in what they can do.** Plenty of them cannot take tools
   at all, and sending declarations to one is an error rather than a graceful
   ignore — so capabilities come from the live catalog, per model.
"""
from __future__ import annotations

import json

import pytest

from core import agent_runner as AR
from core import agent_tools as AT
from core import ai_providers as AP
from core.api_dialects import dialect_for

OPENAI = dialect_for("openai")

# Trimmed from a real https://openrouter.ai/api/v1/models response.
CATALOG = {"data": [
    {"id": "openrouter/free", "name": "Free router",
     "pricing": {"prompt": "0", "completion": "0"},
     "supported_parameters": ["tools", "tool_choice", "reasoning", "structured_outputs"],
     "architecture": {"input_modalities": ["text"]}},
    {"id": "cohere/north-mini-code:free", "name": "North Mini Code",
     "pricing": {"prompt": "0", "completion": "0"},
     "supported_parameters": ["tools", "reasoning"],
     "architecture": {"input_modalities": ["text"]}},
    {"id": "vendor/chat-only", "name": "Chat Only",
     "pricing": {"prompt": "0.000001", "completion": "0.000002"},
     "supported_parameters": ["max_tokens", "temperature"],
     "architecture": {"input_modalities": ["text", "image"]}},
    {"id": "vendor/per-image", "name": "Image Model",
     # Zero token pricing but billed per request: not free.
     "pricing": {"prompt": "0", "completion": "0", "request": "0.01"},
     "supported_parameters": ["max_tokens"],
     "architecture": {"input_modalities": ["image"]}},
]}


def _linked(tmp_path, key="sk-or-test"):
    acct = AP.add_account(tmp_path, "openrouter", "Personal")
    AP.save_token(tmp_path, "openrouter", acct["id"], key)
    AP.forget_models()
    return acct["id"]


class Http(list):
    """Every request made, plus the scripted chat reply."""

    replies: dict


@pytest.fixture
def http(monkeypatch):
    """Stub the one network seam; the catalog is served, chat is scripted."""
    calls = Http()
    calls.replies = {}

    def fake(method, url, key, *, payload=None, timeout=60, headers=None):
        import copy
        calls.append({"method": method, "url": url, "key": key,
                      "headers": dict(headers or {}), "payload": copy.deepcopy(payload)})
        if "/models" in url:
            return {"code": 200, "json": CATALOG, "error": ""}
        reply = calls.replies.get("chat")
        if callable(reply):
            return reply(payload)
        return reply or {"code": 200, "error": "", "json": {
            "choices": [{"message": {"role": "assistant", "content": "hello"},
                         "finish_reason": "stop"}]}}

    monkeypatch.setattr(AP, "_http", fake)
    return calls


# ── the catalog ──────────────────────────────────────────────────────────────

def test_the_catalog_is_read_from_openrouter_not_hardcoded(tmp_path, http):
    """New models appear weekly; anything pinned in source rots. Only the free
    router is pinned, because it is the reason most people connect at all."""
    aid = _linked(tmp_path)
    res = AP.list_models(tmp_path, "openrouter", aid)

    assert res["source"] == "live"
    ids = [m["id"] for m in res["models"]]
    assert ids[0] == "" and ids[1] == AP.FREE_ROUTER
    assert "vendor/chat-only" in ids and "cohere/north-mini-code:free" in ids


def test_free_models_come_first_and_are_labelled(tmp_path, http):
    aid = _linked(tmp_path)
    models = AP.list_models(tmp_path, "openrouter", aid)["models"]
    free = [m for m in models if m.get("free")]
    paid = [m for m in models if m.get("id") and not m.get("free") and not m.get("pinned")]

    assert {m["id"] for m in free} == {AP.FREE_ROUTER, "cohere/north-mini-code:free"}
    assert all("free" in m["label"].lower() for m in free)
    assert models.index(free[0]) < models.index(paid[0]), "free first"
    # The pinned router still takes the catalog's facts, or the menu would not
    # know it supports tools.
    router = next(m for m in models if m["id"] == AP.FREE_ROUTER)
    assert router["capabilities"]["tools"] is True


def test_zero_token_pricing_is_not_enough_to_be_free(tmp_path, http):
    """A model billed per request while reporting zero token pricing is not free,
    and calling it free in the menu is the kind of wrong that costs money."""
    aid = _linked(tmp_path)
    models = {m["id"]: m for m in AP.list_models(tmp_path, "openrouter", aid)["models"]}
    assert models["vendor/per-image"]["free"] is False


def test_capabilities_are_surfaced_per_model(tmp_path, http):
    aid = _linked(tmp_path)
    models = {m["id"]: m for m in AP.list_models(tmp_path, "openrouter", aid)["models"]}

    assert models["cohere/north-mini-code:free"]["capabilities"]["tools"] is True
    assert models["vendor/chat-only"]["capabilities"]["tools"] is False
    assert models["vendor/chat-only"]["capabilities"]["vision"] is True
    assert AP.model_capabilities(tmp_path, "openrouter", aid, AP.FREE_ROUTER)["reasoning"] is True


def test_an_unknown_slug_keeps_its_tools(tmp_path, http):
    """A brand-new model absent from our cached catalog must stay usable, not be
    quietly stripped of everything it can do."""
    aid = _linked(tmp_path)
    assert AP.model_capabilities(tmp_path, "openrouter", aid, "vendor/brand-new") == {}


# ── requests ─────────────────────────────────────────────────────────────────

def test_the_free_router_is_the_default(tmp_path, http):
    """An unattended agent should not start spending because nobody picked."""
    aid = _linked(tmp_path)
    assert AP.resolve_model(tmp_path, "openrouter", aid) == AP.FREE_ROUTER


def test_requests_identify_the_app(tmp_path, http, monkeypatch):
    from core import env_store
    monkeypatch.setattr(env_store, "read_env", lambda *a, **k: {})
    aid = _linked(tmp_path)
    AP.api_generate(tmp_path, "openrouter", aid, "hi")

    chat = [c for c in http if "chat/completions" in c["url"]][0]
    assert chat["headers"]["Authorization"] == "sk-or-test".join(("Bearer ", ""))
    assert chat["headers"]["X-Title"] == "Plutus MCP Manager"
    assert chat["headers"]["HTTP-Referer"].startswith("https://")


def test_the_app_name_and_referrer_are_configurable(tmp_path, http, monkeypatch):
    from core import env_store
    monkeypatch.setattr(env_store, "read_env", lambda *a, **k: {
        "OPENROUTER_APP_NAME": "My Homelab", "OPENROUTER_APP_URL": "https://home.example"})
    aid = _linked(tmp_path)
    AP.api_generate(tmp_path, "openrouter", aid, "hi")

    chat = [c for c in http if "chat/completions" in c["url"]][0]
    assert chat["headers"]["X-Title"] == "My Homelab"
    # The icon is not a header — OpenRouter renders this URL's favicon.
    assert chat["headers"]["HTTP-Referer"] == "https://home.example"


def test_tools_are_not_sent_to_a_model_that_cannot_use_them(tmp_path, http):
    """Sending declarations to a chat-only model is an error, not a no-op."""
    aid = _linked(tmp_path)
    decls, _ = AT.tool_declarations(AT.LIBRARY_TOOLS, None, dialect="openai")

    AP.api_turn(tmp_path, "openrouter", aid, contents=[OPENAI.user_message("hi")],
                declarations=decls, model="vendor/chat-only")
    assert "tools" not in [c for c in http if "chat/completions" in c["url"]][0]["payload"]

    http.clear()
    AP.api_turn(tmp_path, "openrouter", aid, contents=[OPENAI.user_message("hi")],
                declarations=decls, model="cohere/north-mini-code:free")
    body = [c for c in http if "chat/completions" in c["url"]][0]["payload"]
    assert {t["function"]["name"] for t in body["tools"]} == {
        "library_write_file", "library_read_file", "library_list_files"}


def test_reasoning_is_requested_only_where_it_is_supported(tmp_path, http):
    aid = _linked(tmp_path)
    AP.api_turn(tmp_path, "openrouter", aid, contents=[OPENAI.user_message("hi")],
                model=AP.FREE_ROUTER)
    assert [c for c in http if "chat" in c["url"]][0]["payload"]["reasoning"] == {"effort": "medium"}

    http.clear()
    AP.api_turn(tmp_path, "openrouter", aid, contents=[OPENAI.user_message("hi")],
                model="vendor/chat-only")
    assert "reasoning" not in [c for c in http if "chat" in c["url"]][0]["payload"]


# ── the dialect ──────────────────────────────────────────────────────────────

def test_declarations_use_openais_function_envelope():
    decls, _ = AT.tool_declarations(
        [{"name": "t", "description": "d", "inputSchema": {"type": "object"}}],
        None, dialect="openai")
    assert decls[0]["type"] == "function"
    assert decls[0]["function"]["name"] == "t"


def test_openai_schemas_carry_no_openapi_nullable():
    """`nullable` is OpenAPI, not JSON Schema; an OpenAI-compatible endpoint can
    reject the unknown keyword outright."""
    decls, _ = AT.tool_declarations([{
        "name": "t", "description": "d",
        "inputSchema": {"type": "object", "properties": {
            "a": {"anyOf": [{"type": "string"}, {"type": "null"}]}}},
    }], None, dialect="openai")
    assert decls[0]["function"]["parameters"]["properties"]["a"] == {"type": "string"}


def test_tool_call_arguments_arrive_as_a_json_string():
    parsed = OPENAI.parse({"choices": [{"message": {
        "role": "assistant", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "library_write_file",
                          "arguments": '{"path": "a.md", "content": "x"}'}}]},
        "finish_reason": "tool_calls"}]})
    assert parsed["calls"] == [{"id": "call_1", "name": "library_write_file",
                                "args": {"path": "a.md", "content": "x"}}]


def test_unparseable_arguments_do_not_kill_the_run():
    parsed = OPENAI.parse({"choices": [{"message": {"tool_calls": [
        {"id": "c", "function": {"name": "t", "arguments": "{not json"}}]}}]})
    assert parsed["calls"][0]["args"] == {}


def test_a_tool_result_echoes_the_id_it_answers():
    """OpenAI rejects a tool message whose tool_call_id names no call it made."""
    msgs = OPENAI.tool_results_message([
        {"id": "call_1", "name": "t", "text": "done", "is_error": False}])
    assert msgs == [{"role": "tool", "tool_call_id": "call_1", "name": "t",
                     "content": "done"}]


# ── the loop, end to end ─────────────────────────────────────────────────────

def test_an_openrouter_agent_calls_a_tool_and_answers(tmp_path, http, monkeypatch):
    """The whole point: the shared agent loop drives a second wire format without
    knowing it has changed."""
    monkeypatch.setattr(AR, "load_agent_config",
                        lambda root: {**AR.DEFAULT_AGENT_CONFIG, "give_plutus_tools": True})
    monkeypatch.setattr("core.mcp_client.McpHttpClient",
                        lambda url, token="", **kw: pytest.fail("MCP should not be used"))
    aid = _linked(tmp_path)
    turns = {"n": 0}

    def chat(payload):
        turns["n"] += 1
        if turns["n"] == 1:
            return {"code": 200, "error": "", "json": {"choices": [{"message": {
                "role": "assistant", "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "library_write_file",
                                 "arguments": json.dumps({"path": "or/notes.md",
                                                          "content": "# via OpenRouter\n"})}}]},
                "finish_reason": "tool_calls"}]}}
        return {"code": 200, "error": "", "json": {"choices": [{
            "message": {"role": "assistant", "content": "Saved to or/notes.md."},
            "finish_reason": "stop"}]}}

    http.replies["chat"] = chat
    rec = AR.run_agent(tmp_path, "write it up", label="x",
                       provider="openrouter", account_id=aid, mcp_url="")

    assert rec["ok"] is True and rec["result"] == "Saved to or/notes.md."
    assert (tmp_path / "data" / "library" / "or" / "notes.md").read_text() == "# via OpenRouter\n"

    # The second request must carry the assistant's own message *and* a tool
    # reply naming the call id, or OpenAI-compatible servers reject it.
    second = [c for c in http if "chat/completions" in c["url"]][1]["payload"]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "tool"]
    assert second[-1]["tool_call_id"] == "call_1"
    assert "via OpenRouter" not in second[-1]["content"]      # the tool's reply, not the file


def test_an_openrouter_error_is_explained(tmp_path, http):
    aid = _linked(tmp_path)
    http.replies["chat"] = {"code": 402, "json": {},
                            "error": "Insufficient credits"}
    res = AP.api_generate(tmp_path, "openrouter", aid, "hi")
    assert res["ok"] is False and "Insufficient credits" in res["error"]

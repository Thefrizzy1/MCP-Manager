"""How each HTTP provider's chat API is shaped.

Plutus drives two wire formats that do the same job in different words:

- **Gemini** — ``:generateContent``, a ``contents`` array of ``parts``, tools as
  ``functionDeclarations``, calls as ``functionCall`` parts.
- **OpenAI-compatible** — ``/chat/completions``, a ``messages`` array of roles,
  tools as ``{"type": "function", ...}``, calls as ``tool_calls`` with ids that
  must be echoed on the reply. OpenRouter speaks this, as does anything else
  claiming OpenAI compatibility.

Keeping that difference here means the agent runner's tool loop is written once.
It appends whatever ``raw_message`` a turn returns and asks the dialect to build
the tool-result reply — it never learns which provider it is talking to.

The alternative was a second copy of the loop per provider, which is where the
subtle bugs live: OpenAI rejects a tool result whose ``tool_call_id`` does not
match a call it made, and Gemini rejects function responses that are not preceded
by the model's own message. Both rules are encoded once, below.
"""
from __future__ import annotations

import json
from typing import Any


class Dialect:
    """One provider's request/response shape. Stateless; all methods are pure."""

    name = ""

    # ── auth and routing ─────────────────────────────────────────────────────
    def auth_headers(self, key: str) -> dict[str, str]:
        raise NotImplementedError

    def chat_url(self, api_base: str, model: str) -> str:
        raise NotImplementedError

    def models_url(self, api_base: str) -> str:
        raise NotImplementedError

    # ── requests ─────────────────────────────────────────────────────────────
    def user_message(self, text: str) -> dict:
        raise NotImplementedError

    def build(self, *, contents: list[dict], model: str,
              declarations: list[dict] | None, extras: dict | None = None) -> dict:
        raise NotImplementedError

    def declaration(self, tool: dict, schema: dict) -> dict:
        """One tool, in this provider's function-declaration shape."""
        raise NotImplementedError

    def tool_results_message(self, results: list[dict]) -> list[dict]:
        """Reply carrying tool output. ``results``: {id, name, text, is_error}."""
        raise NotImplementedError

    # ── responses ────────────────────────────────────────────────────────────
    def parse(self, body: dict) -> dict:
        """{"text", "calls", "raw_message", "finish"}.

        ``calls`` is [{"id", "name", "args"}]; ``raw_message`` is what has to go
        back into the history verbatim before any tool result.
        """
        raise NotImplementedError

    def parse_models(self, body: dict) -> list[dict]:
        """[{"id", "label", "free", "capabilities": {...}}] — newest catalog."""
        raise NotImplementedError

    def empty_reason(self, body: dict) -> str:
        return "the model returned no text"


# ── Gemini ───────────────────────────────────────────────────────────────────

class GeminiDialect(Dialect):
    name = "gemini"

    def auth_headers(self, key: str) -> dict[str, str]:
        return {"x-goog-api-key": key}

    def chat_url(self, api_base: str, model: str) -> str:
        return f"{api_base}/models/{model}:generateContent"

    def models_url(self, api_base: str) -> str:
        return f"{api_base}/models?pageSize=200"

    def user_message(self, text: str) -> dict:
        return {"role": "user", "parts": [{"text": text}]}

    def declaration(self, tool: dict, schema: dict) -> dict:
        return {"name": tool["name"],
                "description": (tool.get("description") or tool["name"])[:1024],
                "parameters": schema}

    def build(self, *, contents: list[dict], model: str,
              declarations: list[dict] | None, extras: dict | None = None) -> dict:
        body: dict[str, Any] = {"contents": contents}
        if declarations:
            body["tools"] = [{"functionDeclarations": declarations}]
        elif (extras or {}).get("search"):
            # Only when no declarations: Gemini forbids mixing function
            # declarations with a built-in tool in one request.
            body["tools"] = [{"google_search": {}}]
        return body

    def tool_results_message(self, results: list[dict]) -> list[dict]:
        parts = [{"functionResponse": {
            "name": r["name"],
            # A tool's own error is data the model should read and work around,
            # so it rides in the same envelope as a success.
            "response": {"error" if r.get("is_error") else "result": r.get("text", "")},
        }} for r in results]
        return [{"role": "user", "parts": parts}]

    def parse(self, body: dict) -> dict:
        cand = (body.get("candidates") or [{}])[0]
        parts = ((cand.get("content") or {}).get("parts")) or []
        calls = []
        for p in parts:
            fc = p.get("functionCall") if isinstance(p, dict) else None
            if isinstance(fc, dict):
                calls.append({"id": "", "name": str(fc.get("name") or ""),
                              "args": fc.get("args") if isinstance(fc.get("args"), dict) else {}})
        text = "\n".join(p["text"] for p in parts
                         if isinstance(p, dict) and isinstance(p.get("text"), str)).strip()
        return {"text": text, "calls": calls,
                "raw_message": {"role": "model", "parts": parts},
                "finish": cand.get("finishReason") or ""}

    def parse_models(self, body: dict) -> list[dict]:
        out = []
        for m in body.get("models") or []:
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue        # embedding/token-count models cannot answer a prompt
            mid = str(m.get("name") or "").split("models/")[-1]
            if mid:
                out.append({"id": mid, "label": m.get("displayName") or mid,
                            "free": False, "capabilities": {"tools": True}})
        return out

    def empty_reason(self, body: dict) -> str:
        blocked = (body.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            return f"the prompt was blocked by a safety filter ({blocked})"
        finish = ((body.get("candidates") or [{}])[0]).get("finishReason")
        if finish and finish != "STOP":
            return f"the model stopped early ({finish})"
        return "the model returned no text"


# ── OpenAI-compatible (OpenRouter) ───────────────────────────────────────────

class OpenAIDialect(Dialect):
    name = "openai"

    def auth_headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    def chat_url(self, api_base: str, model: str) -> str:
        return f"{api_base}/chat/completions"      # the model rides in the body

    def models_url(self, api_base: str) -> str:
        return f"{api_base}/models"

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def declaration(self, tool: dict, schema: dict) -> dict:
        return {"type": "function", "function": {
            "name": tool["name"],
            "description": (tool.get("description") or tool["name"])[:1024],
            "parameters": schema,
        }}

    def build(self, *, contents: list[dict], model: str,
              declarations: list[dict] | None, extras: dict | None = None) -> dict:
        body: dict[str, Any] = {"model": model, "messages": contents}
        if declarations:
            body["tools"] = declarations
        for key in ("reasoning", "usage", "provider", "transforms"):
            if (extras or {}).get(key) is not None:
                body[key] = extras[key]
        return body

    def tool_results_message(self, results: list[dict]) -> list[dict]:
        # One message per call, each echoing the id the model issued. A missing or
        # mismatched tool_call_id is rejected outright rather than ignored.
        return [{"role": "tool", "tool_call_id": r.get("id") or r["name"],
                 "name": r["name"], "content": r.get("text", "")}
                for r in results]

    def parse(self, body: dict) -> dict:
        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = (c or {}).get("function") or {}
            raw = fn.get("arguments")
            # Arguments arrive as a JSON *string*; a model can still emit
            # something unparseable, and an empty dict beats crashing the run.
            if isinstance(raw, str):
                try:
                    args = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw if isinstance(raw, dict) else {}
            calls.append({"id": c.get("id") or "", "name": str(fn.get("name") or ""),
                          "args": args if isinstance(args, dict) else {}})
        content = msg.get("content")
        text = content.strip() if isinstance(content, str) else ""
        if not text and isinstance(content, list):
            # Some OpenAI-compatible servers return content blocks.
            text = "\n".join(b.get("text", "") for b in content
                             if isinstance(b, dict)).strip()
        return {"text": text, "calls": calls,
                "raw_message": msg or {"role": "assistant", "content": ""},
                "finish": choice.get("finish_reason") or ""}

    def parse_models(self, body: dict) -> list[dict]:
        out = []
        for m in body.get("data") or []:
            mid = str(m.get("id") or "")
            if not mid:
                continue
            params = set(m.get("supported_parameters") or [])
            modalities = set((m.get("architecture") or {}).get("input_modalities") or [])
            free = mid.endswith(":free") or _all_free(m.get("pricing") or {})
            out.append({
                "id": mid,
                "label": m.get("name") or mid,
                "free": bool(free),
                "context": m.get("context_length") or 0,
                "capabilities": {
                    "tools": "tools" in params or "tool_choice" in params,
                    "reasoning": "reasoning" in params or "include_reasoning" in params,
                    "structured_outputs": "structured_outputs" in params
                                          or "response_format" in params,
                    "vision": "image" in modalities,
                },
            })
        return out

    def empty_reason(self, body: dict) -> str:
        choice = (body.get("choices") or [{}])[0]
        finish = choice.get("finish_reason")
        if finish == "length":
            return "the model hit its output limit before answering"
        if finish and finish not in ("stop", "tool_calls"):
            return f"the model stopped early ({finish})"
        return "the model returned no text"


def _all_free(pricing: dict) -> bool:
    """True only when *every* priced dimension is zero.

    Not just prompt and completion: a model can bill per request or per image
    while reporting zero token pricing, and calling that "free" in the menu would
    be the kind of wrong that costs money.
    """
    seen = False
    for value in (pricing or {}).values():
        if isinstance(value, (list, dict)):
            continue                      # tiered overrides; the base rates decide
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        seen = True
        if price != 0.0:
            return False
    return seen


DIALECTS: dict[str, Dialect] = {
    "gemini": GeminiDialect(),
    "openai": OpenAIDialect(),
}


def dialect_for(name: str) -> Dialect:
    d = DIALECTS.get((name or "").strip())
    if d is None:
        raise ValueError(f"unknown API dialect: {name!r}")
    return d

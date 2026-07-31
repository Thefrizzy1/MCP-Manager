"""Sub-agents — a coordinator handing work to cheaper models.

The expensive account should be deciding what to do, not doing all of it. A
Claude or Codex agent can delegate the bulk work — read these forty pages,
summarise each, extract the fields — to a free or near-free model, and spend its
own turns on the judgement.

Two design choices carry most of the weight:

**Workers run on HTTP providers only** (Gemini, OpenRouter). Not a limitation
dressed up as a principle — it is what makes delegation safe. ``run_agent`` holds
one global slot and refuses a second run, so a CLI sub-agent launched from inside
a run would be refused or deadlock. An HTTP worker is just a request, so several
can be in flight without touching that slot at all. It is also the cheap half of
the roster, which is the point.

**A worker gets tools, but cannot delegate.** Nesting coordinators is how a
runaway bill happens, so ``agent_delegate`` is never offered to a worker.

Everything here runs inside whichever process is serving MCP, so it is
independent of the agent runner's own state — no run records, no LIVE console, no
lock. A delegation is a tool call that returns text.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

# A worker is a helper, not a second agent loop: it answers, optionally using
# tools, and stops. Without a ceiling a confused worker re-calls the same failing
# tool until the coordinator's patience (or the free tier) runs out.
MAX_WORKER_TURNS = 6

# How many workers may be in flight at once. Enough for a coordinator to fan out,
# low enough that a free tier's rate limit is not the thing that discovers the
# problem.
MAX_CONCURRENCY = 4
_slots = asyncio.Semaphore(MAX_CONCURRENCY)

# Tools a worker may never have. Delegation is the one that recurses.
WORKER_DENIED = ("agent_delegate", "agent_list_workers")


def worker_accounts(root: Path) -> list[dict]:
    """Linked accounts that can run a sub-agent, cheapest signal first.

    Only HTTP providers: see the module docstring for why that is the whole
    mechanism rather than a restriction.
    """
    from core import ai_providers

    out: list[dict] = []
    for pid, spec in ai_providers.PROVIDERS.items():
        if spec.get("kind") != ai_providers.KIND_API:
            continue
        for account in ai_providers.load_accounts(root).get(pid, []):
            status = ai_providers.account_status(root, pid, account)
            if not status["authenticated"]:
                continue
            out.append({
                "provider": pid,
                "provider_label": spec["label"],
                "account_id": account["id"],
                "account_label": account.get("label") or account["id"],
                "default_model": ai_providers.resolve_model(root, pid, account["id"]),
                "role": spec.get("role", ""),
            })
    return out


def _pick(root: Path, provider: str, account_id: str) -> tuple[str, str, str]:
    """(provider, account_id, error) — resolve a partial or empty request.

    A coordinator should not have to know the account ids: naming a provider, or
    nothing at all, picks the first linked worker.
    """
    workers = worker_accounts(root)
    if not workers:
        return "", "", ("No worker accounts are linked. Add a Gemini or OpenRouter "
                        "account in Settings → AI providers — those run over HTTP, "
                        "which is what lets them run alongside the main agent.")
    if provider and account_id:
        if any(w["provider"] == provider and w["account_id"] == account_id for w in workers):
            return provider, account_id, ""
        return "", "", f"'{provider}/{account_id}' is not a linked worker account."
    if provider:
        match = next((w for w in workers if w["provider"] == provider), None)
        if not match:
            return "", "", (f"No linked {provider} account. Available: "
                            + ", ".join(sorted({w['provider'] for w in workers})))
        return match["provider"], match["account_id"], ""
    return workers[0]["provider"], workers[0]["account_id"], ""


async def delegate(root: Path, task: str, *, provider: str = "", account_id: str = "",
                   model: str = "", mcp_url: str = "", mcp_token: str = "",
                   use_tools: bool = True, max_turns: int = MAX_WORKER_TURNS) -> dict:
    """Run one sub-agent to completion. {"ok", "text", "error", "worker", "turns"}.

    Never raises: a coordinator asked a question and deserves an answer it can
    reason about, not an exception that ends its own run.
    """
    from core import agent_tools, ai_providers

    provider, account_id, err = _pick(root, provider, account_id)
    if err:
        return {"ok": False, "text": "", "error": err, "worker": "", "turns": 0}
    worker = f"{provider}/{account_id}"

    declarations: list[dict] = []
    client = None
    if use_tools:
        declarations, client = await asyncio.to_thread(
            _worker_tools, mcp_url, mcp_token, ai_providers.api_dialect(provider).name)

    contents = [ai_providers.api_user_message(provider, task)]
    turns = 0
    try:
        async with _slots:
            for _ in range(max(1, min(max_turns, MAX_WORKER_TURNS))):
                turns += 1
                res = await asyncio.to_thread(
                    ai_providers.api_turn, root, provider, account_id,
                    contents=contents, declarations=declarations or None,
                    model=model, timeout=180)
                if not res["ok"]:
                    return {"ok": False, "text": "", "error": res["error"],
                            "worker": worker, "turns": turns}
                if not res["calls"]:
                    return {"ok": True, "text": res["text"], "error": "",
                            "worker": worker, "turns": turns}

                contents.append(res["raw_message"])
                replies = []
                for call in res["calls"]:
                    name = str(call.get("name") or "")
                    args = call.get("args") if isinstance(call.get("args"), dict) else {}
                    if agent_tools.is_library_tool(name):
                        out = agent_tools.call_library_tool(name, args, root=root)
                    elif client is None:
                        out = {"text": "no tools available", "is_error": True}
                    else:
                        out = await asyncio.to_thread(client.call_tool, name, args)
                    replies.append({"id": call.get("id") or "", "name": name,
                                    "text": out["text"], "is_error": out["is_error"]})
                contents.extend(ai_providers.api_tool_results_message(provider, replies))

        return {"ok": False, "text": "",
                "error": f"the worker used all {turns} turns without answering",
                "worker": worker, "turns": turns}
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e), "worker": worker, "turns": turns}
    finally:
        if client is not None:
            client.close()


def _worker_tools(mcp_url: str, token: str, dialect: str) -> tuple[list[dict], Any]:
    """Declarations a worker may use, minus the ones that would let it recurse."""
    from core import agent_tools
    from core.mcp_client import McpHttpClient

    builtin = agent_tools.library_tools_for(None)
    tools = list(builtin)
    client = None
    if mcp_url:
        client = McpHttpClient(mcp_url, token)
        try:
            tools += [t for t in client.list_tools()
                      if t.get("name") not in WORKER_DENIED]
        except Exception:
            client.close()
            client = None
            tools = list(builtin)
    decls, _dropped = agent_tools.tool_declarations(tools, None, dialect=dialect)
    return decls, client


def render(result: dict) -> str:
    """One worker's answer, as the coordinator will read it."""
    if not result["ok"]:
        return (f"Sub-agent failed ({result.get('worker') or 'no worker'}): "
                f"{result.get('error') or 'unknown error'}")
    return (f"## Sub-agent result\n\n_{result['worker']} · {result['turns']} "
            f"turn{'s' if result['turns'] != 1 else ''}_\n\n{result['text']}")


def render_workers(workers: list[dict]) -> str:
    if not workers:
        return ("No worker accounts are linked. Add a Gemini or OpenRouter account "
                "in Settings → AI providers.")
    lines = [f"## Sub-agent workers ({len(workers)})", ""]
    for w in workers:
        lines.append(f"- **{w['provider']}** / `{w['account_id']}` — "
                     f"{w['account_label']} · default model "
                     f"`{w['default_model'] or 'account default'}`"
                     + (f" · {w['role']}" if w.get("role") else ""))
    lines.append("")
    lines.append("Pass `provider` (and optionally `account_id`) to agent_delegate, "
                 "or omit both to use the first.")
    return "\n".join(lines)


def parse_batch(raw: str) -> list[str]:
    """Accept a JSON array or newline-separated tasks — models produce both."""
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [line.strip() for line in text.splitlines() if line.strip()]

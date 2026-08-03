"""
tools/agents.py — delegation, so an expensive agent can hand work to a cheap one.

Registered as ordinary MCP tools on purpose. That is what makes them reach every
runtime at once: Claude gets them through `--mcp-config`, Codex through the stdio
bridge, and Gemini through the function declarations built from `tools/list`. A
built-in like the library tools would have covered only two of the three.

Workers run on HTTP providers (Gemini, OpenRouter) and never on a CLI — see
core/subagents for why that is the mechanism rather than a restriction.
"""
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from core import subagents


def register_agent_tools(mcp: FastMCP, *, allow: "set[str] | None" = None):
    from core.profiles import tool_filter
    mcp = tool_filter(mcp, allow)

    def _root():
        from core.library import default_root
        return default_root()

    def _mcp_target() -> tuple[str, str]:
        """Where a worker reaches Plutus's tools. Same endpoint the caller used —
        resolved through the one shared helper (core.agent_orchestrator) rather
        than a third private copy of the loopback URL + token logic."""
        try:
            from config import cfg
            from core.agent_orchestrator import mcp_target
            return mcp_target(cfg)
        except Exception:
            return "", ""

    @mcp.tool(name="agent_list_workers", annotations={"readOnlyHint": True})
    async def agent_list_workers() -> str:
        """List the accounts available to run sub-agents, with their default models.

        Worth calling before delegating a batch: it says which providers are
        linked and what each would cost you nothing to use.
        """
        return subagents.render_workers(subagents.worker_accounts(_root()))

    class DelegateInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        task: str = Field(..., description="The complete instruction for the worker. "
                                           "It sees only this — no conversation history.",
                          min_length=1, max_length=20000)
        provider: str = Field(default="", description="gemini | openrouter (default: first linked)")
        account_id: str = Field(default="", description="Specific account (default: first of that provider)")
        model: str = Field(default="", description="Model slug (default: the account's cheapest)")
        use_tools: bool = Field(default=True,
                                description="Let the worker call Plutus's tools")
        max_turns: int = Field(default=subagents.MAX_WORKER_TURNS, ge=1,
                               le=subagents.MAX_WORKER_TURNS)

    @mcp.tool(name="agent_delegate",
              annotations={"readOnlyHint": False, "destructiveHint": False})
    async def agent_delegate(params: DelegateInput) -> str:
        """Hand one self-contained task to a cheaper model and return its answer.

        Use this for the bulk of a job — reading, summarising, extracting,
        drafting — and keep the judgement for yourself. The worker starts with no
        history, so the task has to stand alone.

        The worker can use Plutus's tools, but cannot delegate further.
        """
        url, token = _mcp_target()
        res = await subagents.delegate(
            _root(), params.task, provider=params.provider,
            account_id=params.account_id, model=params.model,
            mcp_url=url if params.use_tools else "", mcp_token=token,
            use_tools=params.use_tools, max_turns=params.max_turns)
        return subagents.render(res)

    class DelegateBatchInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        tasks: str = Field(..., description="One task per line, or a JSON array of strings",
                           min_length=1, max_length=40000)
        provider: str = Field(default="", description="gemini | openrouter (default: first linked)")
        account_id: str = Field(default="")
        model: str = Field(default="")
        use_tools: bool = Field(default=True)

    @mcp.tool(name="agent_delegate_batch",
              annotations={"readOnlyHint": False, "destructiveHint": False})
    async def agent_delegate_batch(params: DelegateBatchInput) -> str:
        """Run several independent tasks on cheap workers at once, and collect them.

        This is where delegation pays: forty summaries cost one expensive agent
        forty turns, or one call here. Tasks run concurrently up to a small limit,
        so a free tier's rate limit is not the thing that finds the ceiling.

        Each task must stand alone — they do not see each other's results.
        """
        import asyncio

        tasks = subagents.parse_batch(params.tasks)
        if not tasks:
            return "Error: no tasks given."
        url, token = _mcp_target()
        results = await asyncio.gather(*[
            subagents.delegate(_root(), t, provider=params.provider,
                               account_id=params.account_id, model=params.model,
                               mcp_url=url if params.use_tools else "",
                               mcp_token=token, use_tools=params.use_tools)
            for t in tasks])

        done = sum(1 for r in results if r["ok"])
        lines = [f"## {done}/{len(results)} sub-agent tasks completed", ""]
        for task, res in zip(tasks, results):
            lines.append(f"### {task[:120]}")
            lines.append(res["text"] if res["ok"]
                         else f"_failed: {res.get('error') or 'unknown error'}_")
            lines.append("")
        return "\n".join(lines)

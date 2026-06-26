"""Small compatibility wrapper around FastMCP's current tool manager internals."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class ToolRegistryAdapter:
    """Contain private FastMCP tool-manager access in one place."""

    def __init__(self, mcp: Any):
        self._mcp = mcp
        self._tools_cache: list[Any] | None = None
        self._tool_names_cache: list[str] | None = None

    @property
    def raw_manager(self) -> Any:
        manager = getattr(self._mcp, "_tool_manager", None)
        if manager is None:
            raise RuntimeError("FastMCP tool manager is unavailable")
        return manager

    def get_tool(self, name: str) -> Any | None:
        return self.raw_manager.get_tool(name)

    def list_tools(self) -> list[Any]:
        if self._tools_cache is None:
            self._tools_cache = list(self.raw_manager.list_tools() or [])
        return self._tools_cache

    def tool_names(self) -> list[str]:
        if self._tool_names_cache is None:
            self._tool_names_cache = sorted(t.name for t in self.list_tools())
        return self._tool_names_cache

    def invalidate(self) -> None:
        self._tools_cache = None
        self._tool_names_cache = None

    def count(self) -> int:
        try:
            return len(self.list_tools())
        except Exception as exc:
            log.warning("Unable to count registered tools: %s", exc)
            return 0

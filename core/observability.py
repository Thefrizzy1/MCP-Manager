"""Tiny in-process telemetry for router/tool calls."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Telemetry:
    limit: int = 250
    events: deque[dict[str, Any]] = field(init=False)
    total: int = 0
    failures: int = 0
    fallback: int = 0
    latency_total: int = 0
    token_total: int = 0
    by_route: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.events = deque(maxlen=self.limit)

    def record(self, *, route: str, status: str, latency_ms: int, tokens_est: int = 0, detail: str = "") -> None:
        route = route or "unknown"
        status = status or "unknown"
        latency_ms = max(0, int(latency_ms or 0))
        tokens_est = max(0, int(tokens_est or 0))
        self.events.append(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "route": route,
                "status": status,
                "latency_ms": latency_ms,
                "tokens_est": tokens_est,
                "detail": detail[:180],
            }
        )
        self.total += 1
        self.failures += 1 if status == "fail" else 0
        self.fallback += 1 if route == "llm_fallback" else 0
        self.latency_total += latency_ms
        self.token_total += tokens_est
        self.by_route[route] = self.by_route.get(route, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        rows = list(self.events)
        return {
            "total": self.total,
            "window": len(rows),
            "by_route": dict(sorted(self.by_route.items())),
            "failure_rate": round(self.failures / self.total, 3) if self.total else 0.0,
            "fallback_rate": round(self.fallback / self.total, 3) if self.total else 0.0,
            "avg_latency_ms": round(self.latency_total / self.total) if self.total else 0,
            "avg_tokens_est": round(self.token_total / self.total) if self.total else 0,
            "recent": rows[-25:],
        }

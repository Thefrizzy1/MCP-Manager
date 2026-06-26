"""In-memory login rate limiter for the UI Basic-auth gate.

The dashboard is protected by a single credential, with no limit on guesses.
This adds a per-client lockout: after `max_attempts` failures inside a sliding
`window_s` window, that client is locked for `lock_s` seconds. A success clears
the client's history.

Time is injected so the logic is deterministic and unit-testable. State is
process-local, which is fine: the Web UI runs as one uvicorn process.
"""
from __future__ import annotations

import threading


class LoginRateLimiter:
    def __init__(self, max_attempts: int = 8, window_s: float = 900.0, lock_s: float = 900.0) -> None:
        self.max_attempts = max_attempts
        self.window_s = window_s
        self.lock_s = lock_s
        self._fails: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def locked_for(self, key: str, now: float) -> float:
        """Seconds remaining on the lock for `key`, or 0.0 if not locked."""
        with self._lock:
            until = self._locked_until.get(key, 0.0)
            return max(0.0, until - now) if until > now else 0.0

    def record_failure(self, key: str, now: float) -> float:
        """Record a failed attempt; return remaining lock seconds (0 if not locked)."""
        with self._lock:
            recent = [t for t in self._fails.get(key, []) if now - t < self.window_s]
            recent.append(now)
            self._fails[key] = recent
            if len(recent) >= self.max_attempts:
                self._locked_until[key] = now + self.lock_s
                self._fails[key] = []
                return self.lock_s
            return 0.0

    def record_success(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
            self._locked_until.pop(key, None)

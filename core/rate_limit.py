"""In-memory login rate limiter for the UI Basic-auth gate.

The dashboard is protected by a single credential, with no limit on guesses.
This adds a per-client lockout: after `max_attempts` failures inside a sliding
`window_s` window, that client is locked for `lock_s` seconds. A success clears
the client's history.

Time is injected so the logic is deterministic and unit-testable. State is
process-local, which is fine: the Web UI runs as one uvicorn process.

The client key is derived from the peer address, which uvicorn rewrites from
``X-Forwarded-For`` for any peer inside ``FORWARDED_ALLOW_IPS``. That header is
attacker-controlled, so a caller who can reach us directly can mint a fresh key
per request. Two consequences, both handled here: the lockout can be evaded (so
this is a speed bump, not the security boundary — the boundary is that the port
stays on LAN/Tailscale), and the state dicts must be bounded or they become a
memory-exhaustion vector.
"""
from __future__ import annotations

import threading

# Cap on distinct client keys tracked at once. Well above any real deployment;
# low enough that forged X-Forwarded-For values can't grow the process.
_MAX_TRACKED_KEYS = 4096


class LoginRateLimiter:
    def __init__(self, max_attempts: int = 8, window_s: float = 900.0, lock_s: float = 900.0,
                 max_keys: int = _MAX_TRACKED_KEYS) -> None:
        self.max_attempts = max_attempts
        self.window_s = window_s
        self.lock_s = lock_s
        self.max_keys = max_keys
        self._fails: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def _evict(self, now: float) -> None:
        """Drop entries that can no longer affect a decision. Caller holds _lock."""
        self._locked_until = {k: t for k, t in self._locked_until.items() if t > now}
        self._fails = {
            k: ts for k, ts in self._fails.items()
            if (ts and now - ts[-1] < self.window_s)
        }
        # Still over budget after dropping expired entries (a flood inside one
        # window): keep the most recently active keys, discard the rest.
        if len(self._fails) > self.max_keys:
            newest = sorted(self._fails.items(), key=lambda kv: kv[1][-1], reverse=True)
            self._fails = dict(newest[: self.max_keys])
        if len(self._locked_until) > self.max_keys:
            newest_locks = sorted(self._locked_until.items(), key=lambda kv: kv[1], reverse=True)
            self._locked_until = dict(newest_locks[: self.max_keys])

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
            locked = 0.0
            if len(recent) >= self.max_attempts:
                self._locked_until[key] = now + self.lock_s
                self._fails[key] = []
                locked = self.lock_s
            # Evict *after* inserting so the caps are exact rather than max_keys+1.
            if len(self._fails) > self.max_keys or len(self._locked_until) > self.max_keys:
                self._evict(now)
            return locked

    def record_success(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
            self._locked_until.pop(key, None)

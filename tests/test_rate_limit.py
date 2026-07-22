"""Login lockout logic (time injected for determinism)."""
from core.rate_limit import LoginRateLimiter


def test_locks_after_max_attempts():
    rl = LoginRateLimiter(max_attempts=3, window_s=100, lock_s=60)
    assert rl.record_failure("ip", now=0) == 0.0
    assert rl.record_failure("ip", now=1) == 0.0
    locked = rl.record_failure("ip", now=2)  # third failure trips the lock
    assert locked == 60
    assert rl.locked_for("ip", now=2) > 0


def test_lock_expires():
    rl = LoginRateLimiter(max_attempts=2, window_s=100, lock_s=30)
    rl.record_failure("ip", now=0)
    rl.record_failure("ip", now=1)  # locked until 31
    assert rl.locked_for("ip", now=10) > 0
    assert rl.locked_for("ip", now=31) == 0.0


def test_window_slides_old_failures_off():
    rl = LoginRateLimiter(max_attempts=3, window_s=10, lock_s=60)
    rl.record_failure("ip", now=0)
    rl.record_failure("ip", now=1)
    # 12s later the first two are outside the window -> this is failure #1 again
    assert rl.record_failure("ip", now=12) == 0.0
    assert rl.locked_for("ip", now=12) == 0.0


def test_success_clears_history():
    rl = LoginRateLimiter(max_attempts=2, window_s=100, lock_s=60)
    rl.record_failure("ip", now=0)
    rl.record_success("ip")
    assert rl.record_failure("ip", now=1) == 0.0  # counter reset, not locked


def test_keys_are_independent():
    rl = LoginRateLimiter(max_attempts=2, window_s=100, lock_s=60)
    rl.record_failure("a", now=0)
    rl.record_failure("a", now=1)  # a locked
    assert rl.locked_for("a", now=1) > 0
    assert rl.locked_for("b", now=1) == 0.0

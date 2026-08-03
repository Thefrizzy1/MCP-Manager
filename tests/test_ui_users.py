"""The multi-user store and signed sessions (core/ui_users.py)."""
from __future__ import annotations

import time

import pytest

from core import ui_users as U


@pytest.fixture
def root(tmp_path, monkeypatch):
    # No env credential configured -> the default-seed path.
    from config import cfg
    monkeypatch.setattr(cfg, "ui_username", "admin", raising=False)
    monkeypatch.setattr(cfg, "ui_password", "", raising=False)
    return tmp_path


def test_seed_creates_default_admin_when_no_env_password(root):
    info = U.ensure_seed(root)
    assert info["seeded"] is True
    assert info["username"] == "admin"
    assert info["default_password"] == "adminadmin"
    assert U.verify_credentials(root, "admin", "adminadmin")
    assert U.default_password_active(root) is True


def test_seed_is_idempotent(root):
    U.ensure_seed(root)
    U.ensure_seed(root)
    assert len(U.list_users(root)) == 1


def test_seed_adopts_env_password_without_default_flag(tmp_path, monkeypatch):
    from config import cfg
    monkeypatch.setattr(cfg, "ui_username", "operator", raising=False)
    monkeypatch.setattr(cfg, "ui_password", "s3cret-from-env", raising=False)
    info = U.ensure_seed(tmp_path)
    assert info["username"] == "operator"
    assert info["default_password"] is None
    assert U.default_password_active(tmp_path) is False
    assert U.verify_credentials(tmp_path, "operator", "s3cret-from-env")


def test_wrong_password_rejected(root):
    U.ensure_seed(root)
    assert U.verify_credentials(root, "admin", "nope") is None


def test_store_wins_over_stale_env_for_known_user(tmp_path, monkeypatch):
    from config import cfg
    monkeypatch.setattr(cfg, "ui_username", "admin", raising=False)
    monkeypatch.setattr(cfg, "ui_password", "old-env-pw", raising=False)
    U.ensure_seed(tmp_path)  # seeds admin from env
    U.set_password(tmp_path, "admin", "brand-new-password")
    # The changed password authenticates; the stale env one no longer does.
    assert U.verify_credentials(tmp_path, "admin", "brand-new-password")
    assert U.verify_credentials(tmp_path, "admin", "old-env-pw") is None


def test_env_fallback_when_user_absent_from_store(tmp_path, monkeypatch):
    from config import cfg
    monkeypatch.setattr(cfg, "ui_username", "envadmin", raising=False)
    monkeypatch.setattr(cfg, "ui_password", "env-pass-1234", raising=False)
    # Pre-populate the store WITHOUT the env user, so ensure_seed is a no-op and
    # the env credential must go through the back-compat fallback branch.
    U.add_user(tmp_path, "someoneelse", "placeholder-pw", role="admin")
    assert U.verify_credentials(tmp_path, "envadmin", "env-pass-1234")


def test_add_and_remove_user(root):
    U.ensure_seed(root)
    U.add_user(root, "alice", "alice-password", role="user")
    assert any(u["username"] == "alice" for u in U.list_users(root))
    assert U.verify_credentials(root, "alice", "alice-password")
    U.remove_user(root, "alice")
    assert not any(u["username"] == "alice" for u in U.list_users(root))


def test_add_user_rejects_short_password_and_duplicates(root):
    U.ensure_seed(root)
    with pytest.raises(ValueError):
        U.add_user(root, "bob", "short")
    U.add_user(root, "bob", "bob-password")
    with pytest.raises(ValueError):
        U.add_user(root, "bob", "another-password")


def test_cannot_remove_or_demote_last_admin(root):
    U.ensure_seed(root)
    with pytest.raises(ValueError):
        U.remove_user(root, "admin")
    with pytest.raises(ValueError):
        U.set_role(root, "admin", "user")


def test_set_password_clears_default_flag(root):
    U.ensure_seed(root)
    assert U.default_password_active(root) is True
    U.set_password(root, "admin", "a-proper-password")
    assert U.default_password_active(root) is False
    assert U.verify_credentials(root, "admin", "a-proper-password")


def test_session_roundtrip(root):
    U.ensure_seed(root)
    tok = U.sign_session(root, "admin")
    assert U.verify_session(root, tok) == "admin"


def test_session_tamper_rejected(root):
    U.ensure_seed(root)
    tok = U.sign_session(root, "admin")
    body, _, sig = tok.partition(".")
    assert U.verify_session(root, f"{body}x.{sig}") is None
    # Flip the last signature char to one that is guaranteed different (a plain
    # "0" is a no-op ~1/16 of the time, when the hex sig already ends in 0).
    flipped = "1" if sig[-1] != "1" else "2"
    assert U.verify_session(root, f"{body}.{sig[:-1]}{flipped}") is None
    assert U.verify_session(root, "garbage") is None


def test_expired_session_rejected(root, monkeypatch):
    U.ensure_seed(root)
    tok = U.sign_session(root, "admin")
    monkeypatch.setattr(time, "time", lambda: 9_999_999_999.0)
    assert U.verify_session(root, tok) is None


def test_session_for_deleted_user_rejected(root):
    U.ensure_seed(root)
    U.add_user(root, "carol", "carol-password")
    tok = U.sign_session(root, "carol")
    U.remove_user(root, "carol")
    assert U.verify_session(root, tok) is None

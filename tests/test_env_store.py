"""Canonical .env read/write store — validation, atomicity, round-trip."""
import pytest

from core import env_store


def test_round_trip(tmp_path):
    p = tmp_path / ".env"
    env_store.update_env({"FOO_BAR": "hello", "BAZ": "1"}, path=p)
    got = env_store.read_env(p)
    assert got["FOO_BAR"] == "hello"
    assert got["BAZ"] == "1"


def test_merge_preserves_existing(tmp_path):
    p = tmp_path / ".env"
    env_store.update_env({"A": "1"}, path=p)
    env_store.update_env({"B": "2"}, path=p)
    got = env_store.read_env(p)
    assert got == {"A": "1", "B": "2"}


def test_write_falls_back_when_rename_fails(tmp_path, monkeypatch):
    """A Docker single-file bind mount (./.env:/app/.env) can't be replaced via
    rename (EXDEV/EBUSY). The write must fall back to an in-place overwrite
    instead of raising — otherwise every settings save 500s in the container."""
    import os
    p = tmp_path / ".env"
    p.write_text("A=1\n")

    def boom(_src, _dst):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "replace", boom)
    env_store.update_env({"B": "2"}, path=p)  # must not raise
    got = env_store.read_env(p)
    assert got == {"A": "1", "B": "2"}
    assert not (tmp_path / ".env.tmp").exists()  # no leftover temp file


def test_disallowed_key_rejected(tmp_path):
    p = tmp_path / ".env"
    for bad in ("PATH", "LD_PRELOAD", "lowercase", "HAS-DASH"):
        with pytest.raises(ValueError):
            env_store.update_env({bad: "x"}, path=p)


def test_newline_value_rejected(tmp_path):
    p = tmp_path / ".env"
    with pytest.raises(ValueError):
        env_store.update_env({"FOO": "a\nb"}, path=p)


def test_empty_ui_password_rejected(tmp_path):
    p = tmp_path / ".env"
    with pytest.raises(ValueError):
        env_store.update_env({"UI_PASSWORD": ""}, path=p)


def test_bool_coercion(tmp_path):
    p = tmp_path / ".env"
    env_store.update_env({"MCP_REQUIRE_BEARER": True}, path=p)
    assert env_store.read_env(p)["MCP_REQUIRE_BEARER"] == "true"


def test_missing_file_reads_empty(tmp_path):
    assert env_store.read_env(tmp_path / "nope.env") == {}

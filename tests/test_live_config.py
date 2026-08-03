"""Live-config reload: a .env change in one process reaches this process's cfg
without a restart. Deterministic — mtimes are set explicitly, no sleeps."""
from __future__ import annotations

import os

import pytest

from config import cfg
from core import live_config as LC


@pytest.fixture(autouse=True)
def _reset():
    LC._reset_state_for_tests()
    yield
    LC._reset_state_for_tests()


def _write(env, text):
    env.write_text(text, encoding="utf-8")


def _set_mtime(env, when: float):
    os.utime(env, (when, when))


def test_first_call_is_inert(tmp_path, monkeypatch):
    """cfg already matches .env at boot, so the first call records the mtime and
    applies nothing (and never reads the file — keeps it clear of stubbed tests)."""
    env = tmp_path / ".env"
    _write(env, "WEATHER_DEFAULT_LOCATION=Boot\n")
    monkeypatch.setattr(cfg, "weather_default_location", "Boot", raising=False)
    assert LC.refresh_cfg_from_env_if_changed(env, ttl=0) is False
    assert cfg.weather_default_location == "Boot"


def test_a_later_change_propagates_to_cfg(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    _write(env, "WEATHER_DEFAULT_LOCATION=Boot\n")
    monkeypatch.setattr(cfg, "weather_default_location", "Boot", raising=False)
    assert LC.refresh_cfg_from_env_if_changed(env, ttl=0) is False   # lazy init
    base = env.stat().st_mtime

    _write(env, "WEATHER_DEFAULT_LOCATION=Hamburg\n")
    _set_mtime(env, base + 100)
    assert LC.refresh_cfg_from_env_if_changed(env, ttl=0) is True
    assert cfg.weather_default_location == "Hamburg"


def test_ttl_gates_repeated_checks(tmp_path):
    env = tmp_path / ".env"
    _write(env, "WEATHER_DEFAULT_LOCATION=X\n")
    assert LC.refresh_cfg_from_env_if_changed(env, ttl=100) is False   # init
    base = env.stat().st_mtime
    _write(env, "WEATHER_DEFAULT_LOCATION=Y\n")
    _set_mtime(env, base + 100)
    # Within the TTL the file is not even re-stat'd, so the change is not seen yet.
    assert LC.refresh_cfg_from_env_if_changed(env, ttl=100) is False


def test_a_removed_key_is_cleared(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    _write(env, "WEATHER_DEFAULT_LOCATION=Boot\n")
    monkeypatch.setattr(cfg, "weather_default_location", "Boot", raising=False)
    LC.refresh_cfg_from_env_if_changed(env, ttl=0)                     # init
    base = env.stat().st_mtime

    _write(env, "WEATHER_DEFAULT_LOCATION=Hamburg\n")
    _set_mtime(env, base + 100)
    assert LC.refresh_cfg_from_env_if_changed(env, ttl=0) is True
    assert cfg.weather_default_location == "Hamburg"

    _write(env, "# nothing here now\n")
    _set_mtime(env, base + 200)
    assert LC.refresh_cfg_from_env_if_changed(env, ttl=0) is True
    assert cfg.weather_default_location == ""                          # cleared

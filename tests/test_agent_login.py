"""Web login token save — session tokens only, never API keys."""
from core import agent_login, env_store


def test_rejects_empty():
    assert agent_login.save_token("")["ok"] is False


def test_rejects_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(env_store, "ENV_PATH", tmp_path / ".env")
    res = agent_login.save_token("sk-ant-api03-abcdefghijklmnop")
    assert res["ok"] is False
    assert "API key" in res["error"]


def test_saves_session_token(monkeypatch, tmp_path):
    p = tmp_path / ".env"
    monkeypatch.setattr(env_store, "ENV_PATH", p)
    res = agent_login.save_token("sk-ant-oat01-longsessiontokenvalue-1234567890")
    assert res["ok"] is True
    assert env_store.read_env(p)["CLAUDE_CODE_OAUTH_TOKEN"].startswith("sk-ant-oat")

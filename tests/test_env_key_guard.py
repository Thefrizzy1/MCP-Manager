"""POST /env/save key allowlist — blocks tampering with process/shell env."""
from config import is_ui_writable_env_key


def test_normal_service_keys_allowed():
    for k in ("JELLYFIN_URL", "MCP_BEARER_TOKEN", "UI_PASSWORD", "SSH_HOSTS", "A", "A1_B2"):
        assert is_ui_writable_env_key(k), k


def test_blocked_system_keys_rejected():
    for k in ("PATH", "PYTHONPATH", "LD_PRELOAD", "HOME", "SYSTEMROOT", "COMSPEC"):
        assert not is_ui_writable_env_key(k), k


def test_malformed_keys_rejected():
    for k in ("", "lowercase", "1LEADING_DIGIT", "HAS-DASH", "HAS SPACE", "WITH.DOT", "A" * 64):
        assert not is_ui_writable_env_key(k), k


def test_boundary_length_accepted():
    # regex allows 1 + up to 62 trailing chars = 63 total
    assert is_ui_writable_env_key("A" + "B" * 62)
    assert not is_ui_writable_env_key("A" + "B" * 63)

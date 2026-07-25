"""Connection config helpers + ignore store (offline)."""
from core import service_utils as su
from core import ui_prefs


def test_service_url_from_config_key():
    svc = {"config_keys": [("FOO_URL", "URL", "http://ph", False),
                           ("FOO_API_KEY", "Key", "", True)]}
    assert su.service_url(svc, {"FOO_URL": "http://host:1"}) == "http://host:1"
    assert su.service_url(svc, {}) == ""


def test_service_url_from_open_from_env_custom():
    svc = {"open_from_env": "CUST_X_URL", "config_keys": [("CUST_X_URL", "Base URL", "", False)]}
    assert su.service_url(svc, {"CUST_X_URL": "https://x"}) == "https://x"


def test_config_fields_mask_secrets():
    svc = {"config_keys": [("FOO_URL", "URL", "http://ph", False),
                           ("FOO_API_KEY", "Key", "", True)]}
    env = {"FOO_URL": "http://host", "FOO_API_KEY": "s3cret"}
    fields = su.service_config_fields(svc, env)
    by = {f["key"]: f for f in fields}
    # non-secret value passes through; secret value never leaves the server
    assert by["FOO_URL"]["value"] == "http://host"
    assert by["FOO_API_KEY"]["value"] == ""
    assert by["FOO_API_KEY"]["secret"] is True
    assert by["FOO_API_KEY"]["set"] is True   # present, just not disclosed


def test_ignore_store_roundtrip(tmp_path):
    assert ui_prefs.load_ignored_services(tmp_path) == []
    ui_prefs.set_service_ignored(tmp_path, "sonarr", True)
    ui_prefs.set_service_ignored(tmp_path, "radarr", True)
    assert ui_prefs.load_ignored_services(tmp_path) == ["radarr", "sonarr"]
    ui_prefs.set_service_ignored(tmp_path, "sonarr", False)
    assert ui_prefs.load_ignored_services(tmp_path) == ["radarr"]
    # unignoring something absent is a no-op, not an error
    ui_prefs.set_service_ignored(tmp_path, "nope", False)
    assert ui_prefs.load_ignored_services(tmp_path) == ["radarr"]

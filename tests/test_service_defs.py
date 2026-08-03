"""The typed ServiceDef registry, and the drift guards that keep config.py,
builtin_services.SERVICES and the logo/open-url maps in agreement.

These are the tests that make the remaining metadata duplication *safe*: if a new
service is added to SERVICES without a matching config.py field (so it could
never be configured), or a configured_keys attr is misspelled, CI fails here
instead of the service silently never working.
"""
from __future__ import annotations

from config import Config, _cfg_attr_for
from core import service_defs as SD
from core.builtin_services import SERVICES


def test_registry_covers_every_builtin_service():
    assert {d.id for d in SD.service_defs()} == {s["id"] for s in SERVICES}


def test_ids_are_unique():
    ids = [d.id for d in SD.service_defs()]
    assert len(ids) == len(set(ids))


def test_every_service_env_key_has_a_backing_config_field():
    """A config_key with no cfg.<attr> means the service can be "configured" in
    .env but the tools never read it — a silent dead service."""
    missing = []
    for d in SD.service_defs():
        for ek in d.env_keys:
            if _cfg_attr_for(ek.key) is None:
                missing.append(f"{d.id}:{ek.key}")
    assert missing == [], f"service env keys with no config.py field: {missing}"


def test_every_configured_attr_is_a_real_config_field():
    bad = []
    for d in SD.service_defs():
        for attr in d.configured_attrs:
            if attr not in Config.model_fields:
                bad.append(f"{d.id}:{attr}")
    assert bad == [], f"configured_keys naming non-existent cfg fields: {bad}"


def test_url_key_when_present_is_a_real_config_field():
    bad = [f"{d.id}:{d.url_key}" for d in SD.service_defs()
           if d.url_key and d.url_key not in Config.model_fields]
    assert bad == [], f"open-url keys naming non-existent cfg fields: {bad}"


def test_all_env_keys_is_deduped_and_nonempty():
    keys = SD.all_env_keys()
    assert keys and len(keys) == len(set(keys))


def test_secret_keys_are_flagged():
    """API keys / passwords must be marked secret so the UI masks them."""
    jf = SD.service_def("jellyfin")
    assert jf is not None
    by_key = {e.key: e for e in jf.env_keys}
    assert by_key["JELLYFIN_API_KEY"].secret is True
    assert by_key["JELLYFIN_URL"].secret is False

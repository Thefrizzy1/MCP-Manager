"""Brand-logo metadata is a single source of truth (core.builtin_services); the
service_logos module renders from it rather than keeping a second copy."""
from core import builtin_services as B
from core import service_logos as L


def test_logo_data_comes_from_the_canonical_source():
    assert L.CLEARBIT_DOMAIN_BY_ID is B.SERVICE_LOGO_DOMAIN
    assert L.SIMPLE_ICON_SLUG_BY_ID is B.SERVICE_ICON_SLUG


def test_currency_domain_is_stable_after_dedup():
    # The old two-dict layout had a duplicate "currency" key that silently
    # flipped the domain frankfurter.app -> ecb.europa.eu; a single dict cannot
    # regress that way. ecb is the value that already won, kept for parity.
    assert B.SERVICE_LOGO_DOMAIN["currency"] == "ecb.europa.eu"


def test_every_sampled_service_still_resolves_its_logo(tmp_path):
    for sid, domain in [("jellyfin", "jellyfin.org"), ("nextcloud", "nextcloud.com"),
                        ("github", "github.com"), ("fal", "fal.ai"), ("weather", "wttr.in"),
                        ("audiobookshelf", "audiobookshelf.org")]:
        srcs = L.logo_sources_ordered(service_id=sid, root=tmp_path,
                                      logo_domain_override=None, http_base_url=None)
        assert any(domain in s for s in srcs), (sid, srcs)


def test_wizard_logo_domain_reads_the_canonical_map():
    assert L.wizard_logo_domain("gitlab") == "gitlab.com"
    assert L.wizard_logo_domain("nope") is None

"""Secret redaction for fs_read_file output."""
from core.redact import redact_secrets


def test_env_secret_values_masked():
    text = "JELLYFIN_API_KEY=abcd1234\nPASSWORD: hunter2\nclient_secret = zzz"
    out, n = redact_secrets(text)
    assert n == 3
    for leaked in ("abcd1234", "hunter2", "zzz"):
        assert leaked not in out
    # keys remain visible
    assert "JELLYFIN_API_KEY=" in out
    assert "PASSWORD:" in out


def test_non_secret_assignments_untouched():
    text = "HOST=192.168.1.111\nPORT=8765\nNAME=Plutus"
    out, n = redact_secrets(text)
    assert n == 0
    assert out == text


def test_comments_and_prose_untouched():
    text = "# my password is in the vault\njust some normal text about a token system"
    out, n = redact_secrets(text)
    assert n == 0
    assert out == text


def test_authorization_header_masked():
    text = "Authorization: Bearer eyJabc.def.ghi"
    out, n = redact_secrets(text)
    assert n == 1
    assert "eyJabc.def.ghi" not in out
    assert out.startswith("Authorization:")


def test_authorization_header_inline_masked():
    # When not a standalone "key: value" line, the header regex catches it.
    text = "curl -H 'Authorization: Bearer eyJabc.def.ghi' http://x"
    out, n = redact_secrets(text)
    assert n == 1
    assert "eyJabc.def.ghi" not in out


def test_pem_private_key_body_masked():
    text = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIBVgIBADANBgkqh\nkiG9w0BAQEFAAS\n"
        "-----END PRIVATE KEY-----"
    )
    out, n = redact_secrets(text)
    assert n >= 1
    assert "MIIBVgIBADANBgkqh" not in out
    assert "-----BEGIN PRIVATE KEY-----" in out
    assert "-----END PRIVATE KEY-----" in out


def test_empty_input():
    assert redact_secrets("") == ("", 0)

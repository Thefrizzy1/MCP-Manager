"""SSRF screening for web_fetch."""
from core.ssrf_guard import ip_is_blocked, screen_url


def test_private_and_special_ips_blocked():
    for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.111", "169.254.169.254", "::1", "0.0.0.0", "224.0.0.1"):
        assert ip_is_blocked(ip), ip


def test_public_ips_allowed():
    for ip in ("1.1.1.1", "8.8.8.8", "93.184.216.34"):
        assert not ip_is_blocked(ip), ip


def test_non_http_scheme_rejected():
    assert screen_url("ftp://example.com/x") is not None
    assert screen_url("file:///etc/passwd") is not None


def test_ip_literal_loopback_rejected():
    assert screen_url("http://127.0.0.1:8080/admin") is not None
    assert screen_url("http://169.254.169.254/latest/meta-data/") is not None


def test_private_resolution_rejected():
    reason = screen_url("http://internal.example", resolver=lambda h: ["192.168.1.10"])
    assert reason is not None


def test_public_host_allowed():
    assert screen_url("https://example.com/page", resolver=lambda h: ["93.184.216.34"]) is None


def test_unresolvable_host_rejected():
    def boom(_h):
        raise OSError("no dns")
    assert screen_url("http://does-not-resolve.invalid", resolver=boom) is not None

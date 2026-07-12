"""Tests for :func:`app.security.url_guard.is_safe_public_url`."""

from __future__ import annotations

import socket

import pytest

from app.security.url_guard import is_safe_public_url

# ---------------------------------------------------------------------------
# scheme rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'url',
    [
        'file:///etc/passwd',
        'gopher://127.0.0.1:6379/_INFO',
        'data:text/plain;base64,aGVsbG8=',
        'ftp://example.com/file',
        'sftp://example.com/file',
        'custom://example.com/x',
        'example.com/no-scheme',
        '',
    ],
)
def test_rejects_unsupported_scheme(url: str) -> None:
    ok, reason = is_safe_public_url(url)
    assert ok is False
    assert reason in ('unsupported scheme', 'missing hostname', 'malformed URL')


def test_http_and_https_schemes_pass_the_scheme_gate() -> None:
    ok, _reason = is_safe_public_url('http://8.8.8.8/')
    assert ok is True
    ok, _reason = is_safe_public_url('https://8.8.8.8/')
    assert ok is True


# ---------------------------------------------------------------------------
# IPv4 private / special-use ranges — IP literals, no DNS involved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'ip',
    [
        '127.0.0.1',
        '127.255.255.255',
        '10.0.0.1',
        '10.255.255.255',
        '172.16.0.1',
        '172.31.255.255',
        '192.168.0.1',
        '192.168.255.255',
        '169.254.169.254',  # cloud metadata endpoint
        '169.254.0.1',
        '0.0.0.0',
        '100.64.0.1',
        '100.127.255.255',
        '255.255.255.255',
        '224.0.0.1',  # multicast
        '239.255.255.255',  # multicast
    ],
)
def test_rejects_blocked_ipv4_literal(ip: str) -> None:
    ok, reason = is_safe_public_url(f'http://{ip}/')
    assert ok is False
    assert reason == 'private IP'


@pytest.mark.parametrize(
    'ip',
    [
        '172.15.255.255',  # just below 172.16.0.0/12
        '172.32.0.0',  # just above 172.16.0.0/12
        '100.63.255.255',  # just below CGNAT
        '100.128.0.0',  # just above CGNAT
        '8.8.8.8',
        '1.1.1.1',
    ],
)
def test_allows_public_ipv4_literal_boundaries(ip: str) -> None:
    ok, reason = is_safe_public_url(f'http://{ip}/')
    assert ok is True
    assert reason == ''


# ---------------------------------------------------------------------------
# IPv6 private / special-use ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'ip',
    [
        '::1',
        'fe80::1',
        'fc00::1',
        'fd12:3456:789a::1',
    ],
)
def test_rejects_blocked_ipv6_literal(ip: str) -> None:
    ok, reason = is_safe_public_url(f'http://[{ip}]/')
    assert ok is False
    assert reason == 'private IP'


def test_allows_public_ipv6_literal() -> None:
    ok, reason = is_safe_public_url('http://[2606:4700:4700::1111]/')
    assert ok is True
    assert reason == ''


def test_rejects_ipv4_mapped_ipv6_bypass() -> None:
    ok, reason = is_safe_public_url('http://[::ffff:127.0.0.1]/')
    assert ok is False
    assert reason == 'private IP'


# ---------------------------------------------------------------------------
# container hostname denylist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('host', ['redis', 'scheduler', 'api', 'web', 'localhost', 'REDIS'])
def test_rejects_container_hostnames(host: str) -> None:
    ok, reason = is_safe_public_url(f'http://{host}/')
    assert ok is False
    assert reason == 'container hostname'


def test_rejects_container_hostname_with_port() -> None:
    ok, reason = is_safe_public_url('http://redis:6379/')
    assert ok is False
    assert reason == 'container hostname'


# ---------------------------------------------------------------------------
# hostname resolution + DNS rebinding
# ---------------------------------------------------------------------------


def test_hostname_resolving_to_public_ip_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))]

    monkeypatch.setattr('app.security.url_guard.socket.getaddrinfo', fake_getaddrinfo)
    ok, reason = is_safe_public_url('http://good.example.com/feed.rss')
    assert ok is True
    assert reason == ''


def test_hostname_resolving_to_private_ip_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding: a hostname that resolves to an internal address must be blocked."""

    def fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 0))]

    monkeypatch.setattr('app.security.url_guard.socket.getaddrinfo', fake_getaddrinfo)
    ok, reason = is_safe_public_url('http://evil.example.com/feed.rss')
    assert ok is False
    assert reason == 'private IP (resolved)'


def test_hostname_with_mixed_resolution_rejected_if_any_address_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multi-A-record rebinding: reject even if only one resolved address is internal."""

    def fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.5', 0)),
        ]

    monkeypatch.setattr('app.security.url_guard.socket.getaddrinfo', fake_getaddrinfo)
    ok, reason = is_safe_public_url('http://mixed.example.com/feed.rss')
    assert ok is False
    assert reason == 'private IP (resolved)'


def test_dns_resolution_failure_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple]:
        raise socket.gaierror('name not known')

    monkeypatch.setattr('app.security.url_guard.socket.getaddrinfo', fake_getaddrinfo)
    ok, reason = is_safe_public_url('http://nonexistent.invalid/feed.rss')
    assert ok is False
    assert reason.startswith('DNS resolution failed')


# ---------------------------------------------------------------------------
# env-var allowlist override
# ---------------------------------------------------------------------------


def test_allowlist_env_var_overrides_container_hostname_denylist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_URL_GUARD_ALLOWLIST', 'redis,scheduler')
    ok, reason = is_safe_public_url('http://redis/')
    assert ok is True
    assert reason == ''


def test_allowlist_env_var_is_case_insensitive_and_whitespace_tolerant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_URL_GUARD_ALLOWLIST', ' Redis , api ')
    ok, _reason = is_safe_public_url('http://redis/')
    assert ok is True
    ok, _reason = is_safe_public_url('http://api/')
    assert ok is True


def test_allowlist_env_var_does_not_bypass_scheme_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_URL_GUARD_ALLOWLIST', 'redis')
    ok, reason = is_safe_public_url('file://redis/etc/passwd')
    assert ok is False
    assert reason == 'unsupported scheme'


def test_no_allowlist_env_var_falls_through_to_denylist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('ANIGAMERPLUS_URL_GUARD_ALLOWLIST', raising=False)
    ok, reason = is_safe_public_url('http://redis/')
    assert ok is False
    assert reason == 'container hostname'


# ---------------------------------------------------------------------------
# report sample URLs
# ---------------------------------------------------------------------------


def test_rejects_cloud_metadata_endpoint() -> None:
    ok, reason = is_safe_public_url('http://169.254.169.254/')
    assert ok is False
    assert reason == 'private IP'


def test_rejects_redis_container_url() -> None:
    ok, reason = is_safe_public_url('http://redis:6379/')
    assert ok is False
    assert reason == 'container hostname'


def test_rejects_file_scheme() -> None:
    ok, reason = is_safe_public_url('file:///etc/passwd')
    assert ok is False
    assert reason == 'unsupported scheme'

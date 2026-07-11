"""SSRF-safe URL validation.

Shared by every code path that fetches a URL supplied by an admin (RSS feed
URLs, bilibili ``b23.tv`` short-link resolution, ...). Blocks requests aimed
at loopback / private / link-local / CGNAT / multicast / broadcast address
ranges and at this deployment's own Docker Compose service names, so a
malicious or misconfigured URL can't be used to probe internal
infrastructure (cloud metadata endpoints, redis, the scheduler's internal
API, ...).
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse

_ALLOWED_SCHEMES = frozenset({'http', 'https'})

# Docker Compose service names (see docker-compose.yml) — resolvable only
# from inside the container network, never a legitimate external feed host.
_DENYLISTED_HOSTNAMES = frozenset(
    {
        'redis',
        'scheduler',
        'api',
        'web',
        'localhost',
        'backend',
        'frontend',
        'db',
        'database',
    }
)

_ENV_ALLOWLIST_VAR = 'ANIGAMERPLUS_URL_GUARD_ALLOWLIST'

_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('0.0.0.0/8'),
    ipaddress.ip_network('100.64.0.0/10'),  # CGNAT
    ipaddress.ip_network('255.255.255.255/32'),  # limited broadcast
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fe80::/10'),
    ipaddress.ip_network('fc00::/7'),  # unique local (fc00::/8, fd00::/8)
)


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """Return ``(True, '')`` if *url* is safe to fetch, else ``(False, reason)``.

    Checks, in order: scheme allowlist, an explicit env-var hostname
    allowlist, the Docker-internal hostname denylist, then either an
    IP-literal blocklist check or (for hostnames) a
    :func:`socket.getaddrinfo` resolution with every returned address
    checked against the same blocklist. Checking every resolved address
    (rather than just the first) defends against DNS rebinding, where a
    hostname resolves to a public address at validation time and an
    internal one at request time.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False, 'malformed URL'

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, 'unsupported scheme'

    try:
        hostname = parsed.hostname
    except ValueError:
        return False, 'malformed URL'
    if not hostname:
        return False, 'missing hostname'
    hostname = hostname.lower()

    if hostname in _allowlisted_hostnames():
        return True, ''

    if hostname in _DENYLISTED_HOSTNAMES:
        return False, 'container hostname'

    literal_ip = _parse_ip_literal(hostname)
    if literal_ip is not None:
        if _is_blocked_ip(literal_ip):
            return False, 'private IP'
        return True, ''

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        return False, f'DNS resolution failed: {exc}'

    if not addr_infos:
        return False, 'DNS resolution returned no addresses'

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        resolved_ip = ipaddress.ip_address(sockaddr[0])
        if _is_blocked_ip(resolved_ip):
            return False, 'private IP (resolved)'

    return True, ''


def _allowlisted_hostnames() -> frozenset[str]:
    raw = os.environ.get(_ENV_ALLOWLIST_VAR, '')
    return frozenset(h.strip().lower() for h in raw.split(',') if h.strip())


def _parse_ip_literal(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_multicast or ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    return any(ip.version == net.version and ip in net for net in _BLOCKED_NETWORKS)

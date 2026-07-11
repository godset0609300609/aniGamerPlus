"""Bilibili URL / ID parser.

Accepts full video URLs, b23.tv short links, raw BV strings, and raw av
strings.  Returns a ``(bvid, aid, is_multi_part_hint)`` tuple.
``is_multi_part_hint`` is always ``False`` here; the real multi-part check
happens later via yt-dlp ``extract_info``.
"""

from __future__ import annotations

import re

import requests

from ...security.url_guard import is_safe_public_url

# ---------------------------------------------------------------------------
# BV <-> aid conversion (published bilibili algorithm)
# ---------------------------------------------------------------------------

_XOR_CODE: int = 23442827791579
_MASK_CODE: int = 2251799813685247
_MAX_AID: int = 1 << 51
_BASE: int = 58
_DATA: str = 'FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6Zkr9XOm21DoS'
_PREFIX: str = 'BV1'
_CODE_LEN: int = 9


def _bv_to_aid(bvid: str) -> int:
    bvid = bvid[3:]  # strip 'BV1'
    r = 0
    for c in bvid:
        r = r * _BASE + _DATA.index(c)
    return (r & _MASK_CODE) ^ _XOR_CODE


def _aid_to_bv(aid: int) -> str:
    enc = (aid ^ _XOR_CODE) + _MAX_AID
    r = []
    for _ in range(_CODE_LEN):
        enc, rem = divmod(enc, _BASE)
        r.append(_DATA[rem])
    return _PREFIX + ''.join(reversed(r))


# ---------------------------------------------------------------------------
# URL patterns
# ---------------------------------------------------------------------------

_BV_RE = re.compile(r'BV[0-9A-Za-z]{10}')
_AV_RE = re.compile(r'av(\d+)', re.IGNORECASE)
# Anchored to the start of the (stripped) input: a bare ``re.search`` here
# previously matched the substring ``b23.tv/`` anywhere in the input, so a
# string like ``169.254.169.254/b23.tv/x`` or ``evil.com/b23.tv/x`` was
# treated as a b23.tv short link and handed *whole* to ``_resolve_b23``,
# which would then issue a HEAD request to the attacker-controlled host
# rather than to b23.tv (SSRF). Anchoring guarantees the input actually
# starts with b23.tv (optionally prefixed with a scheme).
_B23_RE = re.compile(r'^(https?://)?b23\.tv/', re.IGNORECASE)
_FULL_URL_RE = re.compile(r'^https?://', re.IGNORECASE)
_BARE_BV_RE = re.compile(r'^BV[0-9A-Za-z]{10}$')
_BARE_AV_RE = re.compile(r'^av\d+$', re.IGNORECASE)


def parse_bilibili_input(s: str) -> tuple[str, int, bool]:
    """Parse any Bilibili video identifier into ``(bvid, aid, False)``.

    Accepted inputs:
    - Full URL: ``https://www.bilibili.com/video/BV...``
    - Short URL: ``https://b23.tv/...`` (performs a synchronous HEAD redirect)
    - Raw BV:  ``BV1xx411c7mD``
    - Raw av:  ``av170001`` / ``av170001``

    Anything that isn't one of the above well-formed shapes is rejected
    immediately, before any regex is asked to find a BV/av id or a b23.tv
    link *inside* the string — matching a substring buried in an otherwise
    attacker-controlled string (e.g. an internal IP or hostname) is the
    SSRF vector this guards against.

    Raises :class:`ValueError` if the input cannot be resolved to a BV/av id.
    """
    s = s.strip()

    if not (_FULL_URL_RE.match(s) or _B23_RE.match(s) or _BARE_BV_RE.match(s) or _BARE_AV_RE.match(s)):
        raise ValueError(f'Cannot extract BV or av id from: {s!r}')

    if _B23_RE.match(s):
        s = _resolve_b23(s)

    bv_match = _BV_RE.search(s)
    if bv_match:
        bvid = bv_match.group(0)
        aid = _bv_to_aid(bvid)
        return bvid, aid, False

    av_match = _AV_RE.search(s)
    if av_match:
        aid = int(av_match.group(1))
        bvid = _aid_to_bv(aid)
        return bvid, aid, False

    raise ValueError(f'Cannot extract BV or av id from: {s!r}')


def _resolve_b23(url: str) -> str:
    """Follow a validated b23.tv short URL's redirects and return the final URL."""
    if not url.startswith('http'):
        url = 'https://' + url
    ok, reason = is_safe_public_url(url)
    if not ok:
        raise ValueError(f'b23.tv URL rejected by SSRF guard: {reason}')
    resp = requests.head(url, allow_redirects=True, timeout=10)
    return resp.url

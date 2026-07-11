"""RSS/Atom feed fetching + field mapping for the BT downloader pipeline.

``feedparser`` renames several classic RSS tag names onto its own Atom-ish
vocabulary (``guid`` -> ``id``, ``description`` -> ``summary``, ``pubDate``
-> ``published``) and buries ``<enclosure>`` data in an ``enclosures`` list
rather than a plain nested dict. Callers configuring ``title_key`` /
``link_key`` / ``guid_key`` / ``author_key`` (via the probe wizard) think in
terms of the raw XML tag names, so :func:`_normalize_entry` projects each
parsed entry back into that shape before either ``available_keys`` or
``map_entries`` looks at it.
"""

from __future__ import annotations

import collections.abc
import typing as T

import feedparser
import httpx

from ..security.url_guard import is_safe_public_url

if T.TYPE_CHECKING:
    from ..models import BtFeed

_TIMEOUT_SECONDS = 15.0
_PROBE_ENTRY_COUNT = 5
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class FeedFetchError(Exception):
    """Raised when an RSS/Atom feed cannot be fetched over HTTP."""

    def __init__(self, url: str, cause: Exception) -> None:
        super().__init__(f'failed to fetch feed {url}: {cause}')
        self.url = url
        self.cause = cause


class FeedFetcher:
    """Fetches an RSS/Atom feed over HTTP and maps entries per a feed's field config.

    ``transport`` is injectable (defaults to a real network transport) so
    tests can exercise the real ``httpx`` request/response machinery via
    ``httpx.MockTransport`` instead of stubbing this class's methods.
    """

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def fetch(self, url: str) -> feedparser.FeedParserDict:
        """Fetch and parse *url*, guarding against SSRF, open redirects, and oversized bodies.

        Redirects are not followed automatically: a 3xx response is reported
        as a :class:`FeedFetchError` naming the ``Location`` target, so a
        feed owner can update the configured URL to the final destination
        rather than the fetcher silently chasing a redirect chain an
        attacker (or a compromised upstream) could point at an internal
        address. The response body is streamed and capped at
        ``_MAX_RESPONSE_BYTES`` so a malicious or misbehaving server can't
        exhaust memory.
        """
        ok, reason = is_safe_public_url(url)
        if not ok:
            raise FeedFetchError(url, ValueError(f'URL rejected by SSRF guard: {reason}'))

        data = bytearray()
        try:
            with (
                httpx.Client(timeout=_TIMEOUT_SECONDS, transport=self._transport, follow_redirects=False) as client,
                client.stream('GET', url) as response,
            ):
                if response.is_redirect:
                    location = response.headers.get('location', '<missing>')
                    raise FeedFetchError(
                        url,
                        ValueError(f'feed URL redirects to {location!r} — use the final URL directly'),
                    )
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > _MAX_RESPONSE_BYTES:
                        raise FeedFetchError(
                            url,
                            ValueError(f'response exceeded {_MAX_RESPONSE_BYTES // 1024 // 1024} MB cap'),
                        )
        except httpx.HTTPError as exc:
            raise FeedFetchError(url, exc) from exc
        return feedparser.parse(bytes(data))

    def map_entries(self, raw: feedparser.FeedParserDict, feed: BtFeed) -> list[dict[str, str | None]]:
        """Extract ``{title, link, guid, author, published_at}`` per *feed*'s key mapping.

        Entries missing a resolvable ``title`` or ``link`` are dropped —
        there's nothing useful to persist without them. When ``guid_key``
        is unset, the mapped ``link`` value is reused as the guid.
        """
        out: list[dict[str, str | None]] = []
        for raw_entry in raw.get('entries', []):
            normalized = _normalize_entry(raw_entry)
            title = _dotted_get(normalized, feed.title_key)
            link = _dotted_get(normalized, feed.link_key)
            if title is None or link is None:
                continue
            guid_key = feed.guid_key or feed.link_key
            guid = _dotted_get(normalized, guid_key) or link
            author = _dotted_get(normalized, feed.author_key) if feed.author_key else None
            out.append(
                {
                    'title': title,
                    'link': link,
                    'guid': guid,
                    'author': author,
                    'published_at': normalized.get('pubDate'),
                }
            )
        return out

    def available_keys(self, raw: feedparser.FeedParserDict) -> list[str]:
        """Dotted-path field names available across the first few entries, sorted.

        Used by the "add feed" dry-run wizard so the user can pick
        ``title_key`` / ``link_key`` / ``guid_key`` / ``author_key`` from a
        dropdown instead of guessing.
        """
        keys: set[str] = set()
        for raw_entry in raw.get('entries', [])[:_PROBE_ENTRY_COUNT]:
            keys.update(_flatten_keys(_normalize_entry(raw_entry)))
        return sorted(keys)

    def sample_entries(
        self,
        raw: feedparser.FeedParserDict,
        count: int = _PROBE_ENTRY_COUNT,
    ) -> list[dict[str, T.Any]]:
        """Normalized dict for the first *count* entries — same key vocabulary as :meth:`available_keys`.

        Used by the dry-run wizard to preview values next to the
        ``available_keys`` dropdown before the user commits a key mapping.
        """
        return [_normalize_entry(raw_entry) for raw_entry in raw.get('entries', [])[:count]]


def _normalize_entry(entry: feedparser.FeedParserDict) -> dict[str, T.Any]:
    """Project a feedparser entry back onto raw RSS/Atom tag names."""
    normalized: dict[str, T.Any] = {}
    for key in ('title', 'link', 'guid', 'author'):
        value = entry.get(key)
        if value is not None:
            normalized[key] = value

    pub_date = entry.get('published') or entry.get('updated')
    if pub_date is not None:
        normalized['pubDate'] = pub_date

    description = entry.get('summary')
    if description is not None:
        normalized['description'] = description

    enclosures = entry.get('enclosures') or []
    if enclosures:
        first = enclosures[0]
        enclosure: dict[str, T.Any] = {}
        if 'href' in first:
            enclosure['url'] = first['href']
        if 'type' in first:
            enclosure['type'] = first['type']
        if 'length' in first:
            enclosure['length'] = first['length']
        if enclosure:
            normalized['enclosure'] = enclosure

    return normalized


def _flatten_keys(value: collections.abc.Mapping[str, T.Any], prefix: str = '') -> set[str]:
    keys: set[str] = set()
    for k, v in value.items():
        dotted = f'{prefix}{k}'
        if isinstance(v, collections.abc.Mapping):
            keys.update(_flatten_keys(v, prefix=f'{dotted}.'))
        else:
            keys.add(dotted)
    return keys


def _dotted_get(entry: collections.abc.Mapping[str, T.Any], dotted_key: str | None) -> str | None:
    if not dotted_key:
        return None
    node: T.Any = entry
    for part in dotted_key.split('.'):
        if isinstance(node, collections.abc.Mapping) and part in node:
            node = node[part]
        else:
            return None
    return str(node) if node is not None else None

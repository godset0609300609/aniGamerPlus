"""Tests for FeedFetcher.

Uses ``httpx.MockTransport`` so the real ``httpx`` request/response pipeline
(and the real ``feedparser.parse`` call over real bytes) is exercised end to
end, rather than stubbing ``FeedFetcher`` methods with canned return values.
"""

from __future__ import annotations

import httpx
import pytest

from app.bt_downloader.feed_fetcher import FeedFetcher, FeedFetchError
from app.models import BtFeed

_RSS_BASIC = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Test Feed</title>
<item>
<title>Episode One</title>
<link>https://example.org/topics/view/1</link>
<enclosure url="magnet:?xt=urn:btih:aaa111" length="1000" type="application/x-bittorrent" />
<guid isPermaLink="false">guid-1</guid>
<pubDate>Mon, 01 Jul 2026 12:00:00 +0800</pubDate>
<author>uploader@example.com (UploaderName)</author>
</item>
<item>
<title>Episode Two</title>
<link>https://example.org/topics/view/2</link>
<enclosure url="magnet:?xt=urn:btih:bbb222" length="2000" type="application/x-bittorrent" />
<guid isPermaLink="false">guid-2</guid>
<pubDate>Tue, 02 Jul 2026 12:00:00 +0800</pubDate>
</item>
</channel>
</rss>
"""

# Two real dmhy.org-shaped entries — CDATA description, category tag, no
# <author> element (dmhy doesn't emit one).
_RSS_DMHY_STYLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dmhy="https://dmhy.org/">
<channel>
<title>DMHY RSS</title>
<item>
<title>[LoliHouse] Hikaru ga Shinda Natsu - 01 [WebRip 1080p HEVC-10bit AAC]</title>
<link>https://share.dmhy.org/topics/view/1</link>
<description><![CDATA[<p>Episode 01</p>]]></description>
<enclosure url="magnet:?xt=urn:btih:cccccc333333" length="734003200" type="application/x-bittorrent" />
<category>動畫</category>
<guid isPermaLink="false">https://share.dmhy.org/topics/view/1</guid>
<pubDate>Mon, 01 Jul 2026 21:00:00 +0800</pubDate>
</item>
<item>
<title>[LoliHouse] Hikaru ga Shinda Natsu - 02 [WebRip 1080p HEVC-10bit AAC]</title>
<link>https://share.dmhy.org/topics/view/2</link>
<description><![CDATA[<p>Episode 02</p>]]></description>
<enclosure url="magnet:?xt=urn:btih:dddddd444444" length="741203200" type="application/x-bittorrent" />
<category>動畫</category>
<guid isPermaLink="false">https://share.dmhy.org/topics/view/2</guid>
<pubDate>Mon, 08 Jul 2026 21:00:00 +0800</pubDate>
</item>
</channel>
</rss>
"""


def _fetcher_for(body: str, *, status_code: int = 200) -> FeedFetcher:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(status_code, content=body.encode('utf-8'))

    return FeedFetcher(transport=httpx.MockTransport(handler))


def _feed(**overrides: object) -> BtFeed:
    base: dict[str, object] = {
        'id': 1,
        'name': 'test feed',
        'url': 'https://example.org/rss',
        'title_key': 'title',
        'link_key': 'link',
        'guid_key': None,
        'author_key': None,
        'enabled': True,
        'created_at': '2026-01-01T00:00:00+00:00',
        'updated_at': '2026-01-01T00:00:00+00:00',
    }
    base.update(overrides)
    return BtFeed.model_validate(base)


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


def test_fetch_returns_parsed_feed_with_expected_entry_count() -> None:
    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    assert len(raw.entries) == 2
    assert raw.entries[0]['title'] == 'Episode One'


def test_fetch_wraps_http_status_error() -> None:
    fetcher = _fetcher_for('server error', status_code=500)
    with pytest.raises(FeedFetchError) as exc_info:
        fetcher.fetch('https://example.org/rss')
    assert exc_info.value.url == 'https://example.org/rss'


def test_fetch_wraps_transport_level_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.ConnectError('boom')

    fetcher = FeedFetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(FeedFetchError):
        fetcher.fetch('https://example.org/rss')


# ---------------------------------------------------------------------------
# fetch — SSRF guard / redirects / size cap
# ---------------------------------------------------------------------------


def test_fetch_rejects_url_blocked_by_ssrf_guard() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise AssertionError('must not perform a network request for an SSRF-blocked URL')

    fetcher = FeedFetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(FeedFetchError, match='SSRF guard'):
        fetcher.fetch('http://169.254.169.254/latest/meta-data/')


def test_fetch_rejects_container_hostname_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise AssertionError('must not perform a network request for a denylisted hostname')

    fetcher = FeedFetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(FeedFetchError, match='SSRF guard'):
        fetcher.fetch('http://redis:6379/')


def test_fetch_rejects_non_http_scheme() -> None:
    fetcher = FeedFetcher(transport=httpx.MockTransport(lambda request: httpx.Response(200)))  # noqa: ARG005
    with pytest.raises(FeedFetchError, match='SSRF guard'):
        fetcher.fetch('file:///etc/passwd')


def test_fetch_does_not_follow_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(302, headers={'location': 'https://internal.example/secret'})

    fetcher = FeedFetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(FeedFetchError, match='redirects'):
        fetcher.fetch('https://example.org/rss')


def test_fetch_response_exceeding_size_cap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.bt_downloader.feed_fetcher as feed_fetcher_module

    monkeypatch.setattr(feed_fetcher_module, '_MAX_RESPONSE_BYTES', 100)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, content=b'<rss>' + b'x' * 1000 + b'</rss>')

    fetcher = FeedFetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(FeedFetchError, match='MB cap'):
        fetcher.fetch('https://example.org/rss')


def test_fetch_response_within_size_cap_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.bt_downloader.feed_fetcher as feed_fetcher_module

    monkeypatch.setattr(feed_fetcher_module, '_MAX_RESPONSE_BYTES', len(_RSS_BASIC.encode('utf-8')) + 10)

    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    assert len(raw.entries) == 2


# ---------------------------------------------------------------------------
# available_keys
# ---------------------------------------------------------------------------


def test_available_keys_exact_set_for_basic_rss() -> None:
    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    keys = fetcher.available_keys(raw)
    assert set(keys) == {
        'title',
        'link',
        'guid',
        'pubDate',
        'author',
        'enclosure.url',
        'enclosure.type',
        'enclosure.length',
    }


def test_available_keys_is_sorted() -> None:
    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    keys = fetcher.available_keys(raw)
    assert keys == sorted(keys)


def test_available_keys_excludes_author_when_no_entry_has_one() -> None:
    fetcher = _fetcher_for(_RSS_DMHY_STYLE)
    raw = fetcher.fetch('https://example.org/rss')
    keys = set(fetcher.available_keys(raw))
    assert 'author' not in keys
    assert {'title', 'link', 'guid', 'pubDate', 'description', 'enclosure.url', 'enclosure.type'} <= keys


# ---------------------------------------------------------------------------
# map_entries
# ---------------------------------------------------------------------------


def test_map_entries_uses_enclosure_url_as_link() -> None:
    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    feed = _feed(link_key='enclosure.url')

    entries = fetcher.map_entries(raw, feed)

    assert len(entries) == 2
    assert entries[0]['title'] == 'Episode One'
    assert entries[0]['link'] == 'magnet:?xt=urn:btih:aaa111'
    assert entries[1]['link'] == 'magnet:?xt=urn:btih:bbb222'


def test_map_entries_guid_key_none_falls_back_to_link_value() -> None:
    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    feed = _feed(link_key='enclosure.url', guid_key=None)

    entries = fetcher.map_entries(raw, feed)

    assert entries[0]['guid'] == entries[0]['link']


def test_map_entries_explicit_guid_key_uses_rss_guid() -> None:
    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    feed = _feed(link_key='enclosure.url', guid_key='guid')

    entries = fetcher.map_entries(raw, feed)

    assert entries[0]['guid'] == 'guid-1'
    assert entries[1]['guid'] == 'guid-2'


def test_map_entries_extracts_author_when_configured() -> None:
    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    feed = _feed(author_key='author')

    entries = fetcher.map_entries(raw, feed)

    assert entries[0]['author'] == 'uploader@example.com (UploaderName)'
    assert entries[1]['author'] is None


def test_map_entries_dmhy_style_two_entries() -> None:
    fetcher = _fetcher_for(_RSS_DMHY_STYLE)
    raw = fetcher.fetch('https://share.dmhy.org/topics/rss/sort_id/2/rss.xml')
    feed = _feed(title_key='title', link_key='enclosure.url', guid_key='guid')

    entries = fetcher.map_entries(raw, feed)

    assert len(entries) == 2
    assert entries[0]['title'] == '[LoliHouse] Hikaru ga Shinda Natsu - 01 [WebRip 1080p HEVC-10bit AAC]'
    assert entries[0]['link'] == 'magnet:?xt=urn:btih:cccccc333333'
    assert entries[0]['guid'] == 'https://share.dmhy.org/topics/view/1'
    assert entries[1]['link'] == 'magnet:?xt=urn:btih:dddddd444444'


def test_map_entries_drops_entries_missing_mapped_link() -> None:
    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    # No <enclosure> in this feed carries a "missing" field — point link_key
    # at a key that doesn't exist anywhere to exercise the drop path.
    feed = _feed(link_key='enclosure.doesnotexist')

    entries = fetcher.map_entries(raw, feed)

    assert entries == []


# ---------------------------------------------------------------------------
# sample_entries
# ---------------------------------------------------------------------------


def test_sample_entries_uses_same_key_vocabulary_as_available_keys() -> None:
    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    samples = fetcher.sample_entries(raw)

    assert len(samples) == 2
    assert samples[0]['title'] == 'Episode One'
    assert samples[0]['enclosure']['url'] == 'magnet:?xt=urn:btih:aaa111'
    assert samples[0]['guid'] == 'guid-1'


def test_sample_entries_respects_count_limit() -> None:
    fetcher = _fetcher_for(_RSS_BASIC)
    raw = fetcher.fetch('https://example.org/rss')
    samples = fetcher.sample_entries(raw, count=1)
    assert len(samples) == 1

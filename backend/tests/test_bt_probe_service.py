"""Tests for BtProbeService."""

from __future__ import annotations

import typing as T

import httpx

from app.bt_downloader.feed_fetcher import FeedFetcher
from app.services.bt_probe_service import BtProbeService


class FakeFeedFetcher:
    def __init__(self, raw: str, keys: list[str], samples: list[dict[str, T.Any]]) -> None:
        self._raw = raw
        self._keys = keys
        self._samples = samples
        self.fetch_calls: list[str] = []

    def fetch(self, url: str) -> str:
        self.fetch_calls.append(url)
        return self._raw

    def available_keys(self, raw: str) -> list[str]:
        assert raw == self._raw
        return self._keys

    def sample_entries(self, raw: str) -> list[dict[str, T.Any]]:
        assert raw == self._raw
        return self._samples


def test_probe_returns_available_keys_and_sample_entries() -> None:
    fetcher = FakeFeedFetcher(
        raw='RAW',
        keys=['title', 'link', 'guid'],
        samples=[{'title': 'Ep 1', 'link': 'magnet:1'}, {'title': 'Ep 2', 'link': 'magnet:2'}],
    )
    service = BtProbeService(feed_fetcher=fetcher)

    result = service.probe('https://example.org/rss')

    assert fetcher.fetch_calls == ['https://example.org/rss']
    assert result.available_keys == ['title', 'link', 'guid']
    assert result.sample_entries == [{'title': 'Ep 1', 'link': 'magnet:1'}, {'title': 'Ep 2', 'link': 'magnet:2'}]


def test_probe_default_constructor_builds_a_real_feed_fetcher() -> None:
    service = BtProbeService()
    assert isinstance(service._feed_fetcher, FeedFetcher)


def test_probe_end_to_end_with_real_feed_fetcher_and_mock_transport() -> None:
    body = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><item>'
        b'<title>Ep 1</title><link>https://x/1</link>'
        b'<enclosure url="magnet:1" type="application/x-bittorrent" />'
        b'<guid isPermaLink="false">g1</guid>'
        b'</item></channel></rss>'
    )

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, content=body)

    fetcher = FeedFetcher(transport=httpx.MockTransport(handler))
    service = BtProbeService(feed_fetcher=fetcher)

    result = service.probe('https://example.org/rss')

    assert 'title' in result.available_keys
    assert 'enclosure.url' in result.available_keys
    assert result.sample_entries[0]['title'] == 'Ep 1'

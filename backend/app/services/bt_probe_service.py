"""Feed dry-run probe used by the "add feed" wizard.

Given a bare RSS/Atom URL, returns the set of dotted field-path keys found
in the feed plus a handful of sample entries, so the frontend can let the
user pick ``title_key`` / ``link_key`` / ``guid_key`` / ``author_key`` from
a dropdown instead of guessing at raw XML tag names.
"""

from __future__ import annotations

from ..bt_downloader.feed_fetcher import FeedFetcher
from ..models import BtProbeResult


class BtProbeService:
    """Wraps :class:`~app.bt_downloader.feed_fetcher.FeedFetcher` for the probe endpoint."""

    def __init__(self, feed_fetcher: FeedFetcher | None = None) -> None:
        self._feed_fetcher = feed_fetcher if feed_fetcher is not None else FeedFetcher()

    def probe(self, url: str) -> BtProbeResult:
        raw = self._feed_fetcher.fetch(url)
        return BtProbeResult(
            available_keys=self._feed_fetcher.available_keys(raw),
            sample_entries=self._feed_fetcher.sample_entries(raw),
        )

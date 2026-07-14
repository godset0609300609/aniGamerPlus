"""Tests for BtDownloaderService.

Orchestration-level tests — every collaborator is a hand-written fake.
``FeedFetcher`` / ``PutioClient`` each already have their own dedicated
tests exercising the real ``httpx`` request/response pipeline; this module
is only about the sequencing / branching logic in ``run_iteration``.
"""

from __future__ import annotations

import typing as T

import opencc

from app.bt_downloader.feed_fetcher import FeedFetchError
from app.bt_downloader.putio_client import PutioAuthError, PutioClientError, PutioTransferAlreadyAddedError
from app.models import BtDownloaderSettings, BtFeed, BtFeedEntry, BtFilter
from app.services.bt_downloader_service import BtDownloaderService


def _feed(feed_id: int, name: str, url: str) -> BtFeed:
    return BtFeed(
        id=feed_id,
        name=name,
        url=url,
        created_at='2026-01-01T00:00:00+00:00',
        updated_at='2026-01-01T00:00:00+00:00',
    )


class FakeFeedRepo:
    def __init__(self, feeds: list[BtFeed]) -> None:
        self._feeds = feeds

    def list_enabled(self) -> list[BtFeed]:
        return self._feeds

    def get(self, feed_id: int) -> BtFeed | None:
        for feed in self._feeds:
            if feed.id == feed_id:
                return feed
        return None


class FakeFilterRepo:
    def __init__(self, filters: list[BtFilter]) -> None:
        self._filters = filters
        self.list_all_calls = 0

    def list_all(self) -> list[BtFilter]:
        self.list_all_calls += 1
        return self._filters


class FakeFeedEntryRepo:
    def __init__(self) -> None:
        self._seen: set[tuple[int, str]] = set()
        self._next_id = 1
        self._rows: dict[int, BtFeedEntry] = {}
        self.inserted: list[BtFeedEntry] = []
        self.dispatched: list[tuple[int, int, int]] = []
        self.matched: list[tuple[int, int]] = []
        self.list_unmatched_within_calls = 0

    def insert_if_new(
        self,
        feed_id: int,
        guid: str,
        title: str,
        link: str,
        author: str | None = None,
        published_at: str | None = None,
    ) -> BtFeedEntry | None:
        key = (feed_id, guid)
        if key in self._seen:
            return None
        self._seen.add(key)
        row = BtFeedEntry(
            id=self._next_id,
            feed_id=feed_id,
            guid=guid,
            title=title,
            link=link,
            author=author,
            published_at=published_at,
            fetched_at='2026-01-01T00:00:00+00:00',
        )
        self._next_id += 1
        self.inserted.append(row)
        self._rows[row.id] = row
        return row

    def mark_dispatched(self, entry_id: int, filter_id: int, transfer_id: int) -> None:
        self.dispatched.append((entry_id, filter_id, transfer_id))
        row = self._rows.get(entry_id)
        if row is not None:
            self._rows[entry_id] = row.model_copy(
                update={'matched_filter_id': filter_id, 'dispatched_at': '2026-01-01T00:00:01+00:00'}
            )

    def mark_matched(self, entry_id: int, filter_id: int) -> None:
        self.matched.append((entry_id, filter_id))
        row = self._rows.get(entry_id)
        if row is not None:
            self._rows[entry_id] = row.model_copy(update={'matched_filter_id': filter_id})

    def list_pending_dispatch(self, limit: int) -> list[BtFeedEntry]:
        pending = [r for r in self._rows.values() if r.matched_filter_id is not None and r.dispatched_at is None]
        pending.sort(key=lambda r: r.id)  # insertion order proxy for fetched_at asc
        return pending[:limit]

    def list_unmatched_within(self, retention_days: int) -> list[BtFeedEntry]:  # noqa: ARG002
        self.list_unmatched_within_calls += 1
        unmatched = [r for r in self._rows.values() if r.matched_filter_id is None]
        unmatched.sort(key=lambda r: r.id)  # insertion order proxy for fetched_at asc
        return unmatched


class FakePutioTokenRepo:
    def __init__(self, token: str | None) -> None:
        self._token = token

    def exists_and_nonempty(self) -> bool:
        return bool(self._token)

    def read(self) -> str:
        return self._token or ''


class FakeFeedFetcher:
    """Keys entries off the feed URL — ``fetch`` returns the URL itself as the sentinel "raw" value."""

    def __init__(
        self,
        entries_by_url: dict[str, list[dict[str, T.Any]]],
        raise_for: dict[str, Exception] | None = None,
    ) -> None:
        self._entries_by_url = entries_by_url
        self._raise_for = raise_for or {}

    def fetch(self, url: str) -> str:
        if url in self._raise_for:
            raise self._raise_for[url]
        return url

    def map_entries(self, raw: str, feed: BtFeed) -> list[dict[str, T.Any]]:  # noqa: ARG002
        return self._entries_by_url.get(raw, [])


class FakeFilterMatcher:
    def __init__(self, match_titles: set[str]) -> None:
        self._match_titles = match_titles

    def match(self, title: str, filters: list[BtFilter], hanzi_convert: bool) -> BtFilter | None:  # noqa: ARG002
        if not filters:
            return None
        if title in self._match_titles:
            return filters[0]
        return None


class FakePutioClient:
    def __init__(
        self, *, raise_error: Exception | None = None, extra_transfer_fields: dict[str, T.Any] | None = None
    ) -> None:
        self.add_transfer_calls: list[str] = []
        self._raise_error = raise_error
        self._next_transfer_id = 100
        self._extra_transfer_fields = extra_transfer_fields or {}

    def add_transfer(self, url: str) -> dict[str, T.Any]:
        self.add_transfer_calls.append(url)
        if self._raise_error is not None:
            raise self._raise_error
        transfer_id = self._next_transfer_id
        self._next_transfer_id += 1
        return {'id': transfer_id, 'status': 'IN_QUEUE', **self._extra_transfer_fields}


class FakeProgressBus:
    """Records ProgressBus.start() calls — mirrors the real API surface used by BtDownloaderService."""

    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []

    def start(
        self,
        sn: int,
        filename: str,
        status: str = '等待下載',
        *,
        bangumi_name: str | None = None,
        episode: str | None = None,
        resolution: str | None = None,
        owner_id: str | None = None,
        source: str | None = None,
        external_id: str | None = None,
    ) -> None:
        self.start_calls.append(
            {
                'sn': sn,
                'filename': filename,
                'status': status,
                'bangumi_name': bangumi_name,
                'source': source,
                'external_id': external_id,
            }
        )


def _entry_dict(guid: str, title: str, link: str) -> dict[str, T.Any]:
    return {'guid': guid, 'title': title, 'link': link, 'author': None, 'published_at': None}


def _settings(**overrides: object) -> BtDownloaderSettings:
    return BtDownloaderSettings(**overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# fetch + insert (no token)
# ---------------------------------------------------------------------------


def test_no_token_still_fetches_and_inserts_but_skips_dispatch() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='f1', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo(None)
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    factory_calls: list[str] = []

    def factory(token: str) -> FakePutioClient:
        factory_calls.append(token)
        return FakePutioClient()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        factory,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    assert len(entry_repo.inserted) == 1
    assert entry_repo.dispatched == []
    assert factory_calls == []
    assert filter_repo.list_all_calls == 0


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------


def test_second_iteration_does_not_reinsert_or_redispatch_same_entry() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='f1', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()
    service.run_iteration()

    assert len(entry_repo.inserted) == 1
    assert len(entry_repo.dispatched) == 1
    assert len(putio_client.add_transfer_calls) == 1


# ---------------------------------------------------------------------------
# match + dispatch
# ---------------------------------------------------------------------------


def test_matched_entry_is_dispatched_to_putio() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    matching_filter = BtFilter(id=7, name='f1', keywords=['Show'])
    filter_repo = FakeFilterRepo([matching_filter])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'magnet:link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    assert putio_client.add_transfer_calls == ['magnet:link1']
    [(_entry_id, filter_id, transfer_id)] = entry_repo.dispatched
    assert filter_id == 7
    assert transfer_id == 100


def test_unmatched_entry_is_inserted_but_not_dispatched() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='f1', keywords=['NoMatch'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'link1')]})
    matcher = FakeFilterMatcher(set())  # nothing matches
    putio_client = FakePutioClient()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    assert len(entry_repo.inserted) == 1
    assert entry_repo.dispatched == []
    assert putio_client.add_transfer_calls == []


def test_empty_filter_list_never_calls_add_transfer() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([])  # no filters configured at all
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    assert putio_client.add_transfer_calls == []


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_feed_fetch_error_skips_that_feed_but_continues_with_others() -> None:
    broken_feed = _feed(1, 'broken', 'https://broken.example/rss')
    ok_feed = _feed(2, 'ok', 'https://ok.example/rss')
    feed_repo = FakeFeedRepo([broken_feed, ok_feed])
    filter_repo = FakeFilterRepo([])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo(None)
    fetcher = FakeFeedFetcher(
        {ok_feed.url: [_entry_dict('g1', 'Title', 'link1')]},
        raise_for={broken_feed.url: FeedFetchError(broken_feed.url, RuntimeError('boom'))},
    )
    matcher = FakeFilterMatcher(set())

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: FakePutioClient(),
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()  # must not raise

    assert len(entry_repo.inserted) == 1
    assert entry_repo.inserted[0].feed_id == ok_feed.id


def test_putio_auth_error_stops_further_dispatch_but_keeps_fetching() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='f1', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('bad-token')
    fetcher = FakeFeedFetcher(
        {
            feed.url: [
                _entry_dict('g1', 'Some Show - 01', 'link1'),
                _entry_dict('g2', 'Some Show - 02', 'link2'),
            ]
        }
    )
    matcher = FakeFilterMatcher({'Some Show - 01', 'Some Show - 02'})
    putio_client = FakePutioClient(raise_error=PutioAuthError('token rejected'))

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    # Both entries fetched/inserted, but only the first add_transfer was attempted.
    assert len(entry_repo.inserted) == 2
    assert putio_client.add_transfer_calls == ['link1']
    assert entry_repo.dispatched == []


def test_dispatched_event_fired_after_successful_add_transfer() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    matching_filter = BtFilter(id=7, name='my-filter', keywords=['Show'])
    filter_repo = FakeFilterRepo([matching_filter])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'magnet:link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        notify_event_send=notify_event_send,
    )
    service.run_iteration()

    assert len(events) == 1
    event = events[0]
    assert event['event'] == 'bt_dispatched'
    assert event['title'] == 'Some Show - 01'
    assert event['feed_name'] == 'feed1'
    assert event['filter_name'] == 'my-filter'
    assert event['putio_transfer_id'] == 100
    assert event['entry_id'] == 1


def test_failed_event_fired_when_add_transfer_raises_putio_auth_error() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='my-filter', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('bad-token')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient(raise_error=PutioAuthError('token rejected'))
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        notify_event_send=notify_event_send,
    )
    service.run_iteration()

    assert len(events) == 1
    event = events[0]
    assert event['event'] == 'bt_failed'
    assert event['feed_name'] == 'feed1'
    assert event['filter_name'] == 'my-filter'
    assert event['putio_transfer_id'] is None
    assert event['error_message'] == 'token rejected'


def test_failed_event_fired_when_add_transfer_raises_putio_client_error() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='my-filter', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient(raise_error=PutioClientError('temporary 500'))
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        notify_event_send=notify_event_send,
    )
    service.run_iteration()

    assert len(events) == 1
    assert events[0]['event'] == 'bt_failed'
    assert events[0]['error_message'] == 'temporary 500'


def test_already_added_skips_without_bt_failed() -> None:
    """A benign PutioTransferAlreadyAddedError (duplicate dispatch of a link
    already on Put.io) must be skipped silently — no bt_failed notification,
    and the entry is neither marked dispatched nor stuck in a failure state."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='my-filter', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient(raise_error=PutioTransferAlreadyAddedError('already added'))
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        notify_event_send=notify_event_send,
    )
    service.run_iteration()

    assert putio_client.add_transfer_calls == ['link1']
    assert events == []  # no bt_failed (or any other) notification fired
    assert entry_repo.dispatched == []  # not marked dispatched — no double-dispatch


def test_no_notify_event_send_wired_does_not_raise() -> None:
    """notify_event_send defaults to None (e.g. CLI mode) — must stay a no-op."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='f1', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()  # must not raise

    assert len(entry_repo.dispatched) == 1


def test_putio_client_error_skips_entry_but_continues_dispatching_others() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='f1', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher(
        {
            feed.url: [
                _entry_dict('g1', 'Some Show - 01', 'link1'),
                _entry_dict('g2', 'Some Show - 02', 'link2'),
            ]
        }
    )
    matcher = FakeFilterMatcher({'Some Show - 01', 'Some Show - 02'})

    class FlakyPutioClient(FakePutioClient):
        def add_transfer(self, url: str) -> dict[str, T.Any]:
            if url == 'link1':
                self.add_transfer_calls.append(url)
                raise PutioClientError('temporary 500')
            return super().add_transfer(url)

    putio_client = FlakyPutioClient()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    assert putio_client.add_transfer_calls == ['link1', 'link2']
    assert len(entry_repo.dispatched) == 1


# ---------------------------------------------------------------------------
# Put.io per-tick dispatch cap (fix #8)
# ---------------------------------------------------------------------------


class FakeLogger:
    """Minimal duck-typed stand-in for :class:`app.logging_.Logger`."""

    def __init__(self) -> None:
        self.error_messages: list[str] = []

    def error(self, sn: object, tag: str, detail: str = '', *, display: bool = True, display_time: bool = True) -> None:
        self.error_messages.append(detail)


def test_dispatch_cap_defers_excess_matches_and_next_tick_picks_them_up() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    matching_filter = BtFilter(id=7, name='f1', keywords=['Show'])
    filter_repo = FakeFilterRepo([matching_filter])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')

    titles = [f'Some Show - {i:02d}' for i in range(25)]
    entries = [_entry_dict(f'g{i}', titles[i], f'link{i}') for i in range(25)]
    fetcher = FakeFeedFetcher({feed.url: entries})
    matcher = FakeFilterMatcher(set(titles))
    putio_client = FakePutioClient()
    logger = FakeLogger()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        logger=logger,
    )
    service.run_iteration()

    assert len(entry_repo.inserted) == 25
    assert len(entry_repo.dispatched) == 20
    assert len(entry_repo.matched) == 5
    assert len(putio_client.add_transfer_calls) == 20
    assert any('dispatch cap' in msg and '5 entries deferred' in msg for msg in logger.error_messages)

    # Second tick: the fetcher would return the same 25 entries again, but
    # insert_if_new dedupes them — only the drain of the 5 deferred entries
    # (matched_filter_id set, dispatched_at still None) should fire.
    service.run_iteration()

    assert len(entry_repo.dispatched) == 25
    assert len(putio_client.add_transfer_calls) == 25


def test_dispatch_cap_pending_drain_happens_before_new_matches() -> None:
    """Entries deferred by a previous tick are dispatched before this tick's new matches."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    matching_filter = BtFilter(id=7, name='f1', keywords=['Show'])
    filter_repo = FakeFilterRepo([matching_filter])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()

    # Tick 1: 22 matched entries — 20 dispatched, 2 deferred.
    tick1_titles = [f'Some Show - {i:02d}' for i in range(22)]
    tick1_entries = [_entry_dict(f'g{i}', tick1_titles[i], f'link{i}') for i in range(22)]
    fetcher = FakeFeedFetcher({feed.url: tick1_entries})
    matcher = FakeFilterMatcher(set(tick1_titles))
    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()
    assert len(entry_repo.dispatched) == 20
    assert len(entry_repo.matched) == 2

    # Tick 2: 5 brand-new matched entries. The 2 pending from tick 1 must be
    # dispatched first, leaving 18 of this tick's budget for the new ones —
    # well within budget, so all 5 new entries dispatch too.
    tick2_titles = [f'New Show - {i:02d}' for i in range(5)]
    tick2_entries = [_entry_dict(f'h{i}', tick2_titles[i], f'newlink{i}') for i in range(5)]
    fetcher._entries_by_url[feed.url] = tick2_entries  # type: ignore[attr-defined]
    matcher._match_titles |= set(tick2_titles)  # type: ignore[attr-defined]

    service.run_iteration()

    # 20 (tick 1) + 2 (drained pending) + 5 (new) = 27.
    assert len(entry_repo.dispatched) == 27
    assert len(entry_repo.matched) == 2  # no additional deferrals this tick


# ---------------------------------------------------------------------------
# rescan pass — filters added after fetch still get a chance to match
# ---------------------------------------------------------------------------


def test_rescan_matches_entry_after_filter_added_on_next_iteration() -> None:
    """An entry fetched while no filter matched it must still match once a
    filter is added later — the per-tick rescan pass re-evaluates unmatched
    entries against the *current* filter list, not just the filter list that
    existed at insert time."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([])  # no filters configured yet
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'magnet:link1')]})
    matcher = FakeFilterMatcher(set())  # matches nothing yet
    putio_client = FakePutioClient()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    assert len(entry_repo.inserted) == 1
    assert entry_repo.dispatched == []

    # A filter is added after the entry was already fetched and stored
    # unmatched; the matcher is updated to reflect it now matching the title.
    late_filter = BtFilter(id=9, name='late-filter', keywords=['Show'])
    filter_repo._filters.append(late_filter)  # type: ignore[attr-defined]
    matcher._match_titles.add('Some Show - 01')  # type: ignore[attr-defined]
    fetcher._entries_by_url[feed.url] = []  # type: ignore[attr-defined]  # nothing new this tick

    service.run_iteration()

    assert putio_client.add_transfer_calls == ['magnet:link1']
    [(_entry_id, filter_id, _transfer_id)] = entry_repo.dispatched
    assert filter_id == 9


def test_rescan_matched_entry_fires_dispatched_notification() -> None:
    """The rescan pass reuses the same dispatch helper as the new-entry pass,
    so a rescan-triggered match must fire the same 'bt_dispatched' telegram
    notification (Wave 2-D)."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'magnet:link1')]})
    matcher = FakeFilterMatcher(set())
    putio_client = FakePutioClient()
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        notify_event_send=notify_event_send,
    )
    service.run_iteration()
    assert events == []  # nothing matched yet — no notification

    late_filter = BtFilter(id=9, name='late-filter', keywords=['Show'])
    filter_repo._filters.append(late_filter)  # type: ignore[attr-defined]
    matcher._match_titles.add('Some Show - 01')  # type: ignore[attr-defined]
    fetcher._entries_by_url[feed.url] = []  # type: ignore[attr-defined]

    service.run_iteration()

    assert len(events) == 1
    event = events[0]
    assert event['event'] == 'bt_dispatched'
    assert event['title'] == 'Some Show - 01'
    assert event['feed_name'] == 'feed1'
    assert event['filter_name'] == 'late-filter'


def test_rescan_respects_shared_dispatch_cap_with_new_entries() -> None:
    """The rescan pass and the new-entry pass share one tick-wide dispatch
    budget (fix #8's cap) — rescanned (older) matches are dispatched first,
    then whatever budget remains goes to newly fetched entries, and the rest
    are deferred via mark_matched for next tick's list_pending_dispatch."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([])  # tick 1: nothing matches, entries land unmatched
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()

    old_titles = [f'Old Show - {i:02d}' for i in range(15)]
    old_entries = [_entry_dict(f'old{i}', old_titles[i], f'oldlink{i}') for i in range(15)]
    fetcher = FakeFeedFetcher({feed.url: old_entries})
    matcher = FakeFilterMatcher(set())  # tick 1: no filters, matcher irrelevant

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()
    assert len(entry_repo.inserted) == 15
    assert entry_repo.dispatched == []

    # Tick 2: a filter now matches both the 15 old (unmatched) entries and
    # 10 brand-new ones. Rescan (oldest first) consumes 15 of the 20-entry
    # cap; the new-entry pass gets the remaining 5, deferring the other 5.
    matching_filter = BtFilter(id=7, name='f1', keywords=['Show'])
    filter_repo._filters.append(matching_filter)  # type: ignore[attr-defined]
    matcher._match_titles |= set(old_titles)  # type: ignore[attr-defined]

    new_titles = [f'New Show - {i:02d}' for i in range(10)]
    new_entries = [_entry_dict(f'new{i}', new_titles[i], f'newlink{i}') for i in range(10)]
    fetcher._entries_by_url[feed.url] = new_entries  # type: ignore[attr-defined]
    matcher._match_titles |= set(new_titles)  # type: ignore[attr-defined]

    service.run_iteration()

    assert entry_repo.list_unmatched_within_calls >= 1
    assert len(entry_repo.dispatched) == 20  # 15 rescanned + 5 of the 10 new entries
    assert len(entry_repo.matched) == 5  # the other 5 new entries deferred to next tick
    assert len(putio_client.add_transfer_calls) == 20


def test_no_rescan_when_putio_token_missing() -> None:
    """Mirrors the existing no-token behavior for the new-entry pass: with no
    Put.io token, run_iteration must not even query for rescan candidates."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='f1', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo(None)
    fetcher = FakeFeedFetcher({feed.url: []})
    matcher = FakeFilterMatcher(set())

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: FakePutioClient(),
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    assert entry_repo.list_unmatched_within_calls == 0


def test_no_rescan_after_auth_failure_in_pending_dispatch_drain() -> None:
    """Once the pending-dispatch drain trips the tick-sticky auth_failed
    flag, the rescan pass must be skipped too — it shares the same
    ``putio_client is not None and not auth_failed`` gate."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='f1', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('bad-token')
    fetcher = FakeFeedFetcher({feed.url: []})
    matcher = FakeFilterMatcher(set())
    putio_client = FakePutioClient(raise_error=PutioAuthError('token rejected'))

    # A previously-deferred pending-dispatch entry so the drain pass is what
    # trips auth_failed before the rescan pass would otherwise run.
    pending_entry = entry_repo.insert_if_new(feed.id, 'g-pending', 'Pending Show', 'link-pending')
    assert pending_entry is not None
    entry_repo.mark_matched(pending_entry.id, 1)

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    assert entry_repo.list_unmatched_within_calls == 0


# ---------------------------------------------------------------------------
# hanzi_convert on insert (store 繁體, not 簡體, in the DB)
# ---------------------------------------------------------------------------


def test_insert_converts_title_when_hanzi_convert_true() -> None:
    """When hanzi_convert is on, the title persisted via insert_if_new must
    already be s2t-converted — the DB should never store 簡體 going forward."""
    raw_title = '【豌豆字幕组】关于我转生变成史莱姆'
    expected_title = opencc.OpenCC('s2t').convert(raw_title)

    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([])  # no filters — irrelevant to this test
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo(None)
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', raw_title, 'link1')]})
    matcher = FakeFilterMatcher(set())

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: FakePutioClient(),
        token_repo,
        _settings(hanzi_convert=True),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    assert len(entry_repo.inserted) == 1
    assert entry_repo.inserted[0].title == expected_title


def test_insert_preserves_title_when_hanzi_convert_false() -> None:
    """When hanzi_convert is off, insert_if_new must receive the raw title
    unmodified — no silent conversion happens behind the setting's back."""
    raw_title = '【豌豆字幕组】关于我转生变成史莱姆'

    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([])  # no filters — irrelevant to this test
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo(None)
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', raw_title, 'link1')]})
    matcher = FakeFilterMatcher(set())

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: FakePutioClient(),
        token_repo,
        _settings(hanzi_convert=False),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()

    assert len(entry_repo.inserted) == 1
    assert entry_repo.inserted[0].title == raw_title


# ---------------------------------------------------------------------------
# task_history integration
# ---------------------------------------------------------------------------


class FakeTaskIdMapRepo:
    def __init__(self) -> None:
        self.allocate_calls: list[tuple[str, str]] = []

    def allocate(self, source: str, external_id: str) -> int:
        self.allocate_calls.append((source, external_id))
        return 2**31 + int(external_id)


class FakeTaskHistoryRepo:
    def __init__(self, *, raise_on_start: Exception | None = None) -> None:
        self.start_calls: list[dict[str, object]] = []
        self._raise_on_start = raise_on_start

    def record_start(self, sn: int, filename: str, **kwargs: object) -> int:
        if self._raise_on_start is not None:
            raise self._raise_on_start
        self.start_calls.append({'sn': sn, 'filename': filename, **kwargs})
        return len(self.start_calls)


def test_dispatched_records_task_history_start_row() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    matching_filter = BtFilter(id=7, name='my-filter', keywords=['Show'])
    filter_repo = FakeFilterRepo([matching_filter])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'magnet:link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()
    task_history_repo = FakeTaskHistoryRepo()
    task_id_map_repo = FakeTaskIdMapRepo()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
    )
    service.run_iteration()

    assert task_id_map_repo.allocate_calls == [('bt', '1')]
    assert len(task_history_repo.start_calls) == 1
    call = task_history_repo.start_calls[0]
    assert call['sn'] == 2**31 + 1
    assert call['filename'] == 'Some Show - 01'
    assert call['bangumi_name'] == 'my-filter'
    assert call['source'] == 'bt'
    assert call['external_id'] == '1'
    assert call['owner_id'] is None


def test_task_history_write_failure_does_not_break_dispatch() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    matching_filter = BtFilter(id=7, name='my-filter', keywords=['Show'])
    filter_repo = FakeFilterRepo([matching_filter])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'magnet:link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()
    task_history_repo = FakeTaskHistoryRepo(raise_on_start=RuntimeError('db down'))
    task_id_map_repo = FakeTaskIdMapRepo()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
    )
    service.run_iteration()  # must not raise

    # Dispatch itself still succeeded despite the task_history failure.
    assert putio_client.add_transfer_calls == ['magnet:link1']
    [(_entry_id, filter_id, transfer_id)] = entry_repo.dispatched
    assert filter_id == 7
    assert transfer_id == 100


# ---------------------------------------------------------------------------
# ProgressBus integration (MonitorView live-monitor visibility)
# ---------------------------------------------------------------------------


def test_dispatched_publishes_to_progress_bus_with_bt_sn() -> None:
    """A successful dispatch must register with ProgressBus using the same
    sn TaskIdMapRepository derives for task_history, so the two rows stay
    linked via (source='bt', external_id) on the frontend."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    matching_filter = BtFilter(id=7, name='my-filter', keywords=['Show'])
    filter_repo = FakeFilterRepo([matching_filter])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'magnet:link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()
    progress_bus = FakeProgressBus()
    task_id_map_repo = FakeTaskIdMapRepo()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        progress_bus=progress_bus,
        task_id_map_repo=task_id_map_repo,
    )
    service.run_iteration()

    assert len(progress_bus.start_calls) == 1
    call = progress_bus.start_calls[0]
    assert call['sn'] == 2**31 + 1  # matches FakeTaskIdMapRepo.allocate('bt', '1')
    assert call['filename'] == 'Some Show - 01'
    assert call['status'] == '等待 Put.io'
    assert call['bangumi_name'] == 'my-filter'
    assert call['source'] == 'bt'
    assert call['external_id'] == '1'


def test_dispatched_progress_bus_falls_back_to_feed_name_when_no_filter_name() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    matching_filter = BtFilter(id=7, name='', keywords=['Show'])
    filter_repo = FakeFilterRepo([matching_filter])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'magnet:link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()
    progress_bus = FakeProgressBus()
    task_id_map_repo = FakeTaskIdMapRepo()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        progress_bus=progress_bus,
        task_id_map_repo=task_id_map_repo,
    )
    service.run_iteration()

    assert progress_bus.start_calls[0]['bangumi_name'] == 'feed1'


def test_no_progress_bus_wired_does_not_raise() -> None:
    """progress_bus defaults to None (e.g. CLI mode) — must stay a no-op."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    filter_repo = FakeFilterRepo([BtFilter(id=1, name='f1', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
    )
    service.run_iteration()  # must not raise

    assert len(entry_repo.dispatched) == 1


# ---------------------------------------------------------------------------
# percent_done / size passthrough from the Put.io add_transfer response
# ---------------------------------------------------------------------------


def test_status_update_payload_includes_percent_done_and_size_from_transfer() -> None:
    """The Put.io transfer object returned by add_transfer() carries
    percent_done/size (even at dispatch time, usually 0/absent) — these are
    forwarded on the emitted 'bt_dispatched' payload for shape symmetry with
    the 'bt_status_update' events LandingWorker fires later for the same
    entry (see notify_bt_event's shared percent_done/file_size_mb params)."""
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    matching_filter = BtFilter(id=7, name='my-filter', keywords=['Show'])
    filter_repo = FakeFilterRepo([matching_filter])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'magnet:link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient(extra_transfer_fields={'percent_done': 0, 'size': 300 * 1024 * 1024})
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        notify_event_send=notify_event_send,
    )
    service.run_iteration()

    assert len(events) == 1
    assert events[0]['event'] == 'bt_dispatched'
    # percent_done=0 is falsy but still an explicit int -> still forwarded (not omitted like None).
    assert events[0]['percent_done'] == 0
    assert events[0]['file_size_mb'] == 300


def test_dispatched_payload_omits_percent_done_and_size_when_absent_from_transfer() -> None:
    feed = _feed(1, 'feed1', 'https://feed1.example/rss')
    feed_repo = FakeFeedRepo([feed])
    matching_filter = BtFilter(id=7, name='my-filter', keywords=['Show'])
    filter_repo = FakeFilterRepo([matching_filter])
    entry_repo = FakeFeedEntryRepo()
    token_repo = FakePutioTokenRepo('tok')
    fetcher = FakeFeedFetcher({feed.url: [_entry_dict('g1', 'Some Show - 01', 'magnet:link1')]})
    matcher = FakeFilterMatcher({'Some Show - 01'})
    putio_client = FakePutioClient()  # no extra_transfer_fields -> no percent_done/size keys
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtDownloaderService(
        feed_repo,
        filter_repo,
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        _settings(),
        feed_fetcher=fetcher,
        filter_matcher=matcher,
        notify_event_send=notify_event_send,
    )
    service.run_iteration()

    assert 'percent_done' not in events[0]
    assert 'file_size_mb' not in events[0]

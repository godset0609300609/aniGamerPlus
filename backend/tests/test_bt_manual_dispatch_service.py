"""Tests for BtManualDispatchService.

Orchestration-level tests — every collaborator is a hand-written fake,
mirroring the style of ``test_bt_downloader_service.py``. This module only
exercises ``dispatch()``'s branching/error-translation logic; the real
``PutioClient`` / ``BtFeedEntryRepository`` each already have their own
dedicated tests.
"""

from __future__ import annotations

import typing as T

import pytest

from app.bt_downloader.putio_client import PutioAuthError, PutioClientError
from app.models import BtFeed, BtFeedEntry, BtFilter
from app.services.bt_manual_dispatch_service import (
    BtManualDispatchService,
    EntryNotFound,
    PutioApiError,
    PutioAuthFailed,
    PutioTokenMissing,
)


def _feed(feed_id: int = 1, name: str = 'dmhy', url: str = 'https://dmhy.example/rss') -> BtFeed:
    return BtFeed(
        id=feed_id,
        name=name,
        url=url,
        created_at='2026-01-01T00:00:00+00:00',
        updated_at='2026-01-01T00:00:00+00:00',
    )


def _entry(
    entry_id: int = 1,
    *,
    feed_id: int = 1,
    title: str = 'Some Show - 01',
    link: str = 'magnet:link1',
    matched_filter_id: int | None = None,
    putio_transfer_id: int | None = None,
) -> BtFeedEntry:
    return BtFeedEntry(
        id=entry_id,
        feed_id=feed_id,
        guid=f'guid-{entry_id}',
        title=title,
        link=link,
        fetched_at='2026-01-01T00:00:00+00:00',
        matched_filter_id=matched_filter_id,
        putio_transfer_id=putio_transfer_id,
    )


class FakeFeedEntryRepo:
    def __init__(self, rows: list[BtFeedEntry] | None = None) -> None:
        self._rows: dict[int, BtFeedEntry] = {r.id: r for r in (rows or [])}
        self.mark_dispatched_manual_calls: list[tuple[int, int]] = []

    def get(self, entry_id: int) -> BtFeedEntry | None:
        return self._rows.get(entry_id)

    def mark_dispatched_manual(self, entry_id: int, putio_transfer_id: int) -> None:
        self.mark_dispatched_manual_calls.append((entry_id, putio_transfer_id))
        row = self._rows.get(entry_id)
        if row is not None:
            self._rows[entry_id] = row.model_copy(
                update={
                    'putio_transfer_id': putio_transfer_id,
                    'dispatched_at': '2026-01-01T00:00:01+00:00',
                    'putio_status': 'IN_QUEUE',
                }
            )


class FakePutioTokenRepo:
    def __init__(self, token: str | None) -> None:
        self._token = token

    def exists_and_nonempty(self) -> bool:
        return bool(self._token)

    def read(self) -> str:
        return self._token or ''


class FakeFeedRepo:
    def __init__(self, feeds: list[BtFeed]) -> None:
        self._feeds = feeds

    def get(self, feed_id: int) -> BtFeed | None:
        for feed in self._feeds:
            if feed.id == feed_id:
                return feed
        return None


class FakeFilterRepo:
    def __init__(self, filters: list[BtFilter]) -> None:
        self._filters = filters

    def get(self, filter_id: int) -> BtFilter | None:
        for filt in self._filters:
            if filt.id == filter_id:
                return filt
        return None


class FakePutioClient:
    def __init__(self, *, raise_error: Exception | None = None) -> None:
        self.add_transfer_calls: list[str] = []
        self._raise_error = raise_error
        self._next_transfer_id = 100

    def add_transfer(self, url: str) -> dict[str, T.Any]:
        self.add_transfer_calls.append(url)
        if self._raise_error is not None:
            raise self._raise_error
        transfer_id = self._next_transfer_id
        self._next_transfer_id += 1
        return {'id': transfer_id, 'status': 'IN_QUEUE'}


class FakeLogger:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.error_messages: list[str] = []

    def info(
        self, sn: object, tag: str, detail: str = '', *, display: bool = True, display_time: bool = True
    ) -> None:
        self.info_messages.append(detail)

    def error(
        self, sn: object, tag: str, detail: str = '', *, display: bool = True, display_time: bool = True
    ) -> None:
        self.error_messages.append(detail)


# ---------------------------------------------------------------------------
# success path
# ---------------------------------------------------------------------------


def test_dispatch_success_marks_dispatched_and_returns_transfer_id() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()

    service = BtManualDispatchService(entry_repo, lambda _tok: putio_client, token_repo)
    result = service.dispatch(1, 'user-1')

    assert result == {'transfer_id': 100, 'status': 'IN_QUEUE'}
    assert putio_client.add_transfer_calls == ['magnet:link1']
    assert entry_repo.mark_dispatched_manual_calls == [(1, 100)]


def test_dispatch_does_not_require_a_prior_filter_match() -> None:
    """Works on an entry that never matched any filter (matched_filter_id is None)."""
    entry_repo = FakeFeedEntryRepo([_entry(matched_filter_id=None)])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()

    service = BtManualDispatchService(entry_repo, lambda _tok: putio_client, token_repo)
    result = service.dispatch(1, 'user-1')

    assert result['transfer_id'] == 100


def test_re_dispatch_overwrites_previous_transfer_id() -> None:
    """An already-dispatched entry gets a brand-new transfer id on re-dispatch."""
    entry_repo = FakeFeedEntryRepo([_entry(matched_filter_id=7, putio_transfer_id=999)])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()

    service = BtManualDispatchService(entry_repo, lambda _tok: putio_client, token_repo)
    result = service.dispatch(1, 'user-1')

    assert result['transfer_id'] == 100
    assert entry_repo.mark_dispatched_manual_calls == [(1, 100)]
    # matched_filter_id is untouched by mark_dispatched_manual (fake mirrors the real repo).
    assert entry_repo.get(1) is not None
    assert entry_repo.get(1).matched_filter_id == 7  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# typed failure paths
# ---------------------------------------------------------------------------


def test_dispatch_raises_entry_not_found_for_missing_entry() -> None:
    entry_repo = FakeFeedEntryRepo([])
    token_repo = FakePutioTokenRepo('tok')
    service = BtManualDispatchService(entry_repo, lambda _tok: FakePutioClient(), token_repo)

    with pytest.raises(EntryNotFound):
        service.dispatch(999, 'user-1')


def test_dispatch_raises_putio_token_missing_when_no_token() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo(None)
    service = BtManualDispatchService(entry_repo, lambda _tok: FakePutioClient(), token_repo)

    with pytest.raises(PutioTokenMissing):
        service.dispatch(1, 'user-1')


def test_dispatch_does_not_call_putio_when_token_missing() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo(None)
    factory_calls: list[str] = []

    def factory(token: str) -> FakePutioClient:
        factory_calls.append(token)
        return FakePutioClient()

    service = BtManualDispatchService(entry_repo, factory, token_repo)
    with pytest.raises(PutioTokenMissing):
        service.dispatch(1, 'user-1')

    assert factory_calls == []


def test_dispatch_raises_putio_auth_failed_on_401() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('bad-token')
    putio_client = FakePutioClient(raise_error=PutioAuthError('token rejected'))
    service = BtManualDispatchService(entry_repo, lambda _tok: putio_client, token_repo)

    with pytest.raises(PutioAuthFailed):
        service.dispatch(1, 'user-1')
    assert entry_repo.mark_dispatched_manual_calls == []


def test_dispatch_raises_putio_api_error_on_non_auth_failure() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient(raise_error=PutioClientError('temporary 500'))
    service = BtManualDispatchService(entry_repo, lambda _tok: putio_client, token_repo)

    with pytest.raises(PutioApiError):
        service.dispatch(1, 'user-1')
    assert entry_repo.mark_dispatched_manual_calls == []


def test_dispatch_raises_putio_api_error_when_transfer_id_missing() -> None:
    class NoIdPutioClient(FakePutioClient):
        def add_transfer(self, url: str) -> dict[str, T.Any]:
            self.add_transfer_calls.append(url)
            return {'status': 'IN_QUEUE'}  # no 'id' key

    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('tok')
    service = BtManualDispatchService(entry_repo, lambda _tok: NoIdPutioClient(), token_repo)

    with pytest.raises(PutioApiError):
        service.dispatch(1, 'user-1')
    assert entry_repo.mark_dispatched_manual_calls == []


# ---------------------------------------------------------------------------
# notification firing
# ---------------------------------------------------------------------------


def test_dispatch_fires_bt_dispatched_notification_with_feed_and_filter_name() -> None:
    feed_repo = FakeFeedRepo([_feed()])
    filter_repo = FakeFilterRepo([BtFilter(id=7, name='my-filter', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo([_entry(matched_filter_id=7)])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtManualDispatchService(
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        bt_feed_repo=feed_repo,
        bt_filter_repo=filter_repo,
        notify_event_send=notify_event_send,
    )
    service.dispatch(1, 'user-1')

    assert len(events) == 1
    event = events[0]
    assert event['event'] == 'bt_dispatched'
    assert event['title'] == 'Some Show - 01'
    assert event['feed_name'] == 'dmhy'
    assert event['filter_name'] == 'my-filter'
    assert event['putio_transfer_id'] == 100
    assert event['entry_id'] == 1


def test_dispatch_fires_bt_dispatched_notification_with_none_filter_name_when_unmatched() -> None:
    feed_repo = FakeFeedRepo([_feed()])
    entry_repo = FakeFeedEntryRepo([_entry(matched_filter_id=None)])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtManualDispatchService(
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        bt_feed_repo=feed_repo,
        notify_event_send=notify_event_send,
    )
    service.dispatch(1, 'user-1')

    assert len(events) == 1
    assert events[0]['filter_name'] is None


def test_dispatch_fires_bt_failed_notification_on_auth_error() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('bad-token')
    putio_client = FakePutioClient(raise_error=PutioAuthError('token rejected'))
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtManualDispatchService(
        entry_repo, lambda _tok: putio_client, token_repo, notify_event_send=notify_event_send
    )
    with pytest.raises(PutioAuthFailed):
        service.dispatch(1, 'user-1')

    assert len(events) == 1
    assert events[0]['event'] == 'bt_failed'
    assert events[0]['error_message'] == 'token rejected'


def test_dispatch_fires_bt_failed_notification_on_client_error() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient(raise_error=PutioClientError('temporary 500'))
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    service = BtManualDispatchService(
        entry_repo, lambda _tok: putio_client, token_repo, notify_event_send=notify_event_send
    )
    with pytest.raises(PutioApiError):
        service.dispatch(1, 'user-1')

    assert len(events) == 1
    assert events[0]['event'] == 'bt_failed'
    assert events[0]['error_message'] == 'temporary 500'


def test_no_notify_event_send_wired_does_not_raise() -> None:
    """notify_event_send defaults to None — must stay a no-op."""
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()

    service = BtManualDispatchService(entry_repo, lambda _tok: putio_client, token_repo)
    result = service.dispatch(1, 'user-1')  # must not raise

    assert result['transfer_id'] == 100


def test_notify_event_send_exception_does_not_propagate() -> None:
    """A broken telegram send must never break the dispatch response."""
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()

    def broken_notify(*, kwargs: dict[str, object]) -> None:
        raise RuntimeError('telegram down')

    service = BtManualDispatchService(
        entry_repo, lambda _tok: putio_client, token_repo, notify_event_send=broken_notify
    )
    result = service.dispatch(1, 'user-1')  # must not raise

    assert result['transfer_id'] == 100


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------


def test_dispatch_logs_manual_dispatch_line_on_success() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()
    logger = FakeLogger()

    service = BtManualDispatchService(entry_repo, lambda _tok: putio_client, token_repo, logger=logger)
    service.dispatch(1, 'user-42')

    assert len(logger.info_messages) == 1
    message = logger.info_messages[0]
    assert 'entry_id=1' in message
    assert 'user=user-42' in message
    assert 'transfer_id=100' in message


def test_dispatch_logs_error_on_auth_failure() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('bad-token')
    putio_client = FakePutioClient(raise_error=PutioAuthError('token rejected'))
    logger = FakeLogger()

    service = BtManualDispatchService(entry_repo, lambda _tok: putio_client, token_repo, logger=logger)
    with pytest.raises(PutioAuthFailed):
        service.dispatch(1, 'user-1')

    assert len(logger.error_messages) == 1
    assert 'token rejected' in logger.error_messages[0]


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
    feed_repo = FakeFeedRepo([_feed()])
    filter_repo = FakeFilterRepo([BtFilter(id=7, name='my-filter', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo([_entry(matched_filter_id=7)])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()
    task_history_repo = FakeTaskHistoryRepo()
    task_id_map_repo = FakeTaskIdMapRepo()

    service = BtManualDispatchService(
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        bt_feed_repo=feed_repo,
        bt_filter_repo=filter_repo,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
    )
    service.dispatch(1, 'user-1')

    assert task_id_map_repo.allocate_calls == [('bt', '1')]
    assert len(task_history_repo.start_calls) == 1
    call = task_history_repo.start_calls[0]
    assert call['sn'] == 2**31 + 1
    assert call['filename'] == 'Some Show - 01'
    assert call['bangumi_name'] == 'my-filter'
    assert call['source'] == 'bt'
    assert call['external_id'] == '1'


def test_task_history_write_failure_does_not_break_dispatch() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()
    task_history_repo = FakeTaskHistoryRepo(raise_on_start=RuntimeError('db down'))
    task_id_map_repo = FakeTaskIdMapRepo()

    service = BtManualDispatchService(
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
    )
    result = service.dispatch(1, 'user-1')  # must not raise

    assert result['transfer_id'] == 100
    assert entry_repo.mark_dispatched_manual_calls == [(1, 100)]


# ---------------------------------------------------------------------------
# ProgressBus integration (MonitorView live-monitor visibility)
# ---------------------------------------------------------------------------


class FakeProgressBus:
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


def test_manual_dispatch_publishes_to_progress_bus_with_bt_sn() -> None:
    feed_repo = FakeFeedRepo([_feed()])
    filter_repo = FakeFilterRepo([BtFilter(id=7, name='my-filter', keywords=['Show'])])
    entry_repo = FakeFeedEntryRepo([_entry(matched_filter_id=7)])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()
    progress_bus = FakeProgressBus()
    task_id_map_repo = FakeTaskIdMapRepo()

    service = BtManualDispatchService(
        entry_repo,
        lambda _tok: putio_client,
        token_repo,
        bt_feed_repo=feed_repo,
        bt_filter_repo=filter_repo,
        progress_bus=progress_bus,
        task_id_map_repo=task_id_map_repo,
    )
    service.dispatch(1, 'user-1')

    assert len(progress_bus.start_calls) == 1
    call = progress_bus.start_calls[0]
    assert call['sn'] == 2**31 + 1
    assert call['filename'] == 'Some Show - 01'
    assert call['status'] == '等待 Put.io'
    assert call['bangumi_name'] == 'my-filter'
    assert call['source'] == 'bt'
    assert call['external_id'] == '1'


def test_manual_dispatch_no_progress_bus_wired_does_not_raise() -> None:
    entry_repo = FakeFeedEntryRepo([_entry()])
    token_repo = FakePutioTokenRepo('tok')
    putio_client = FakePutioClient()

    service = BtManualDispatchService(entry_repo, lambda _tok: putio_client, token_repo)
    result = service.dispatch(1, 'user-1')  # must not raise

    assert result['transfer_id'] == 100

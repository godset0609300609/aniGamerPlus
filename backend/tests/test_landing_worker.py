"""Tests for LandingWorker.

Uses hand-written fakes for ``PutioClient`` and the feed-entry repo — the
real HTTP behaviour of ``PutioClient`` is already covered end to end in
``test_putio_client.py``; this module is purely about the polling/landing
orchestration.
"""

from __future__ import annotations

import collections.abc
import pathlib
import time
import types
import typing as T
import unittest.mock

from app.bt_downloader.landing_worker import LandingWorker
from app.bt_downloader.putio_client import PutioClientError, PutioNotFoundError, PutioRateLimitError
from app.models import BtFeed, BtFeedEntry, BtFilter


class FakeFeedEntryRepo:
    def __init__(self, entries: list[BtFeedEntry]) -> None:
        self._entries: dict[int, BtFeedEntry] = {e.id: e for e in entries}
        self._pending_override: list[BtFeedEntry] | None = None
        self.status_calls: list[tuple[int, str]] = []
        self.local_path_calls: list[tuple[int, str]] = []
        self.reset_dispatch_calls: list[int] = []
        self.mark_remote_cleared_calls: list[int] = []
        self.mark_remote_removed_calls: list[int] = []

    def force_pending(self, rows: list[BtFeedEntry]) -> None:
        self._pending_override = rows

    def list_pending_landing(self) -> list[BtFeedEntry]:
        if self._pending_override is not None:
            return self._pending_override
        return [e for e in self._entries.values() if e.putio_transfer_id is not None and e.local_path is None]

    def get(self, entry_id: int) -> BtFeedEntry | None:
        return self._entries.get(entry_id)

    def update_putio_status(self, entry_id: int, status: str) -> None:
        self.status_calls.append((entry_id, status))
        if entry_id in self._entries:
            self._entries[entry_id] = self._entries[entry_id].model_copy(update={'putio_status': status})

    def update_local_path(self, entry_id: int, path: str) -> None:
        self.local_path_calls.append((entry_id, path))
        if entry_id in self._entries:
            self._entries[entry_id] = self._entries[entry_id].model_copy(update={'local_path': path})

    def reset_dispatch(self, entry_id: int) -> None:
        """Mirrors BtFeedEntryRepository.reset_dispatch: clears the Put.io-side
        fields, preserves matched_filter_id."""
        self.reset_dispatch_calls.append(entry_id)
        if entry_id in self._entries:
            self._entries[entry_id] = self._entries[entry_id].model_copy(
                update={
                    'putio_transfer_id': None,
                    'putio_status': None,
                    'dispatched_at': None,
                    'local_path': None,
                }
            )

    def mark_remote_cleared(self, entry_id: int) -> None:
        """Mirrors BtFeedEntryRepository.mark_remote_cleared."""
        self.mark_remote_cleared_calls.append(entry_id)
        if entry_id in self._entries:
            self._entries[entry_id] = self._entries[entry_id].model_copy(
                update={'putio_status': '遠端已清理', 'remote_cleared_at': '2026-01-01T00:00:00+00:00'}
            )

    def mark_remote_removed(self, entry_id: int) -> None:
        """Mirrors BtFeedEntryRepository.mark_remote_removed."""
        self.mark_remote_removed_calls.append(entry_id)
        if entry_id in self._entries:
            self._entries[entry_id] = self._entries[entry_id].model_copy(
                update={'putio_status': '遠端已移除', 'remote_cleared_at': '2026-01-01T00:00:00+00:00'}
            )

    def list_landed_pending_remote_check(self, limit: int = 100) -> list[BtFeedEntry]:
        """Mirrors BtFeedEntryRepository.list_landed_pending_remote_check (newest ``fetched_at`` first, capped)."""
        matching = [
            e
            for e in self._entries.values()
            if e.local_path is not None and e.putio_transfer_id is not None and e.remote_cleared_at is None
        ]
        matching.sort(key=lambda e: e.fetched_at, reverse=True)
        return matching[:limit]


class FakePutioClient:
    def __init__(
        self,
        *,
        raise_on_get_transfer: Exception | None = None,
        raise_on_download: Exception | None = None,
        raise_on_delete: Exception | None = None,
    ) -> None:
        self.get_transfer_calls: list[int] = []
        self.list_files_calls: list[int] = []
        self.get_file_calls: list[int] = []
        self.download_calls: list[tuple[int, pathlib.Path]] = []
        self.landing_dir_calls: list[pathlib.Path | None] = []
        self.on_progress_received: list[collections.abc.Callable[[int, int], None] | None] = []
        self.delete_file_calls: list[int] = []
        self._transfer_responses: dict[int, list[dict[str, T.Any]]] = {}
        self._files: dict[int, list[dict[str, T.Any]]] = {}
        self._file_meta: dict[int, dict[str, T.Any]] = {}
        self._raise_on_get_transfer = raise_on_get_transfer
        self._raise_on_download = raise_on_download
        self._raise_on_delete = raise_on_delete
        self._progress_chunks: list[tuple[int, int]] = []

    def script_transfer(self, transfer_id: int, responses: list[dict[str, T.Any]]) -> None:
        self._transfer_responses[transfer_id] = list(responses)

    def script_files(self, folder_id: int, files: list[dict[str, T.Any]]) -> None:
        self._files[folder_id] = files

    def script_file(self, file_id: int, meta: dict[str, T.Any]) -> None:
        """Configure the ``GET /files/{id}`` response used as the single-file-
        transfer fallback when ``list_files`` comes back empty."""
        self._file_meta[file_id] = meta

    def get_transfer(self, transfer_id: int) -> dict[str, T.Any]:
        if self._raise_on_get_transfer is not None:
            raise self._raise_on_get_transfer
        self.get_transfer_calls.append(transfer_id)
        responses = self._transfer_responses[transfer_id]
        return responses.pop(0) if len(responses) > 1 else responses[0]

    def list_files(self, folder_id: int) -> list[dict[str, T.Any]]:
        self.list_files_calls.append(folder_id)
        return self._files.get(folder_id, [])

    def get_file(self, file_id: int) -> dict[str, T.Any]:
        self.get_file_calls.append(file_id)
        return self._file_meta.get(file_id, {'id': file_id, 'name': f'file-{file_id}', 'file_type': 'VIDEO'})

    def download_file(
        self,
        file_id: int,
        dest: pathlib.Path,
        *,
        landing_dir: pathlib.Path | None = None,
        on_progress: collections.abc.Callable[[int, int], None] | None = None,
    ) -> pathlib.Path:
        if self._raise_on_download is not None:
            raise self._raise_on_download
        self.download_calls.append((file_id, dest))
        self.landing_dir_calls.append(landing_dir)
        self.on_progress_received.append(on_progress)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b'fake video content')
        if on_progress is not None:
            for bytes_written, total_bytes in self._progress_chunks:
                on_progress(bytes_written, total_bytes)
        return dest

    def script_progress(self, chunks: list[tuple[int, int]]) -> None:
        """Configure ``on_progress`` invocations that ``download_file`` replays in order."""
        self._progress_chunks = chunks

    def delete_file(self, file_id: int) -> None:
        self.delete_file_calls.append(file_id)
        if self._raise_on_delete is not None:
            raise self._raise_on_delete


class FakeBtFeedRepo:
    def __init__(self, feeds: dict[int, BtFeed]) -> None:
        self._feeds = feeds

    def get(self, feed_id: int) -> BtFeed | None:
        return self._feeds.get(feed_id)


class FakeBtFilterRepo:
    def __init__(self, filters: dict[int, BtFilter]) -> None:
        self._filters = filters

    def get(self, filter_id: int) -> BtFilter | None:
        return self._filters.get(filter_id)


class FakeTaskIdMapRepo:
    def __init__(self) -> None:
        self.allocate_calls: list[tuple[str, str]] = []

    def allocate(self, source: str, external_id: str) -> int:
        self.allocate_calls.append((source, external_id))
        return 2**31 + int(external_id)


class FakeTaskHistoryRepo:
    """Mirrors just enough of TaskHistoryRepository for LandingWorker's finish path."""

    def __init__(self) -> None:
        self._rows: dict[int, dict[str, object]] = {}
        self._next_id = 1
        self.finish_calls: list[dict[str, object]] = []

    def seed_in_progress(self, sn: int, *, row_id: int | None = None) -> int:
        rid = row_id if row_id is not None else self._next_id
        self._next_id = max(self._next_id, rid + 1)
        self._rows[rid] = {'id': rid, 'sn': sn, 'final_status': '(in_progress)'}
        return rid

    def get_latest_in_progress_by_sn(self, sn: int) -> object | None:
        candidates = [r for r in self._rows.values() if r['sn'] == sn and r['final_status'] == '(in_progress)']
        if not candidates:
            return None
        row = max(candidates, key=lambda r: T.cast('int', r['id']))
        return types.SimpleNamespace(id=row['id'], sn=row['sn'])

    def record_finish(
        self,
        row_id: int,
        *,
        final_status: str,
        finished_at: object,
        retries: int = 0,
        bangumi_name: str | None = None,
        episode: str | None = None,
        resolution: str | None = None,
        filename: str | None = None,
    ) -> None:
        self.finish_calls.append({'row_id': row_id, 'final_status': final_status, 'filename': filename})
        if row_id in self._rows:
            self._rows[row_id]['final_status'] = final_status


class FakeProgressBus:
    """Records ProgressBus calls — mirrors the real API surface LandingWorker uses."""

    def __init__(self) -> None:
        self.status_calls: list[tuple[int, str]] = []
        self.stats_calls: list[dict[str, object]] = []
        self.metadata_calls: list[dict[str, object]] = []
        self.finish_calls: list[int] = []

    def update_status(self, sn: int, status: str) -> None:
        self.status_calls.append((sn, status))

    def update_stats(
        self,
        sn: int,
        *,
        speed_mbps: float | None = None,
        eta_seconds: int | None = None,
        rate: float | None = None,
    ) -> None:
        self.stats_calls.append({'sn': sn, 'speed_mbps': speed_mbps, 'eta_seconds': eta_seconds, 'rate': rate})

    def update_metadata(
        self,
        sn: int,
        *,
        bangumi_name: str | None = None,
        episode: str | None = None,
        resolution: str | None = None,
        filename: str | None = None,
    ) -> None:
        self.metadata_calls.append({'sn': sn, 'filename': filename})

    def finish(self, sn: int) -> None:
        self.finish_calls.append(sn)


class FakeSettingsRepo:
    """Minimal duck-typed stand-in for :class:`app.persistence.settings_repo.SettingsRepository`."""

    def __init__(self, *, auto_delete_remote_on_landed: bool) -> None:
        self._auto_delete_remote_on_landed = auto_delete_remote_on_landed

    def load(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            bt_downloader=types.SimpleNamespace(auto_delete_remote_on_landed=self._auto_delete_remote_on_landed)
        )


class FakeLogger:
    """Minimal duck-typed stand-in for :class:`app.logging_.Logger`."""

    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.error_messages: list[str] = []

    def info(self, sn: object, tag: str, detail: str = '', *, display: bool = True, display_time: bool = True) -> None:
        self.info_messages.append(detail)

    def error(self, sn: object, tag: str, detail: str = '', *, display: bool = True, display_time: bool = True) -> None:
        self.error_messages.append(detail)


def _entry(
    entry_id: int,
    transfer_id: int | None,
    local_path: str | None = None,
    *,
    matched_filter_id: int | None = None,
) -> BtFeedEntry:
    return BtFeedEntry(
        id=entry_id,
        feed_id=1,
        guid=f'guid-{entry_id}',
        title=f'title-{entry_id}',
        link=f'link-{entry_id}',
        fetched_at='2026-01-01T00:00:00+00:00',
        putio_transfer_id=transfer_id,
        local_path=local_path,
        matched_filter_id=matched_filter_id,
    )


def test_updates_status_without_downloading_when_still_in_queue(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'IN_QUEUE', 'file_id': None}])

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert repo.status_calls == [(1, 'IN_QUEUE')]
    assert putio.download_calls == []
    assert repo.local_path_calls == []


def test_updates_status_without_downloading_when_downloading(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'DOWNLOADING', 'file_id': None}])

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert repo.status_calls == [(1, 'DOWNLOADING')]
    assert putio.download_calls == []
    assert repo.local_path_calls == []


def test_full_lifecycle_in_queue_downloading_completed(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])
    worker = LandingWorker(putio, repo, tmp_path)

    putio.script_transfer(42, [{'status': 'IN_QUEUE', 'file_id': None}])
    worker.run_iteration()
    assert putio.download_calls == []

    putio.script_transfer(42, [{'status': 'DOWNLOADING', 'file_id': None}])
    worker.run_iteration()
    assert putio.download_calls == []

    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    worker.run_iteration()

    assert putio.download_calls == [(555, tmp_path / 'episode.mp4')]
    assert repo.local_path_calls == [(1, 'episode.mp4')]
    assert repo.status_calls[-1] == (1, 'COMPLETED')
    assert (tmp_path / 'episode.mp4').read_bytes() == b'fake video content'


def test_landing_triggers_on_seeding_status(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'SEEDING', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert putio.list_files_calls == [99]
    assert putio.download_calls == [(555, tmp_path / 'episode.mp4')]
    assert repo.local_path_calls == [(1, 'episode.mp4')]
    assert repo.status_calls == [(1, 'SEEDING')]


def test_landing_triggers_on_completed_status(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert putio.list_files_calls == [99]
    assert putio.download_calls == [(555, tmp_path / 'episode.mp4')]
    assert putio.landing_dir_calls == [tmp_path]
    assert repo.local_path_calls == [(1, 'episode.mp4')]
    assert repo.status_calls == [(1, 'COMPLETED')]


def test_landing_does_not_trigger_on_downloading_status(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'DOWNLOADING', 'file_id': 99}])

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert putio.list_files_calls == []
    assert putio.download_calls == []
    assert repo.local_path_calls == []


def test_landing_does_not_re_download_when_local_path_set(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42, local_path='already-landed.mp4')])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'SEEDING', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert putio.get_transfer_calls == []
    assert putio.list_files_calls == []
    assert putio.download_calls == []
    assert repo.local_path_calls == []


def test_completed_with_multiple_files_records_the_last_ones_name(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(
        99,
        [
            {'id': 1, 'name': 'part1.mp4'},
            {'id': 2, 'name': 'part2.mp4'},
        ],
    )

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert putio.download_calls == [
        (1, tmp_path / 'part1.mp4'),
        (2, tmp_path / 'part2.mp4'),
    ]
    assert repo.local_path_calls == [(1, 'part2.mp4')]


def test_completed_without_file_id_does_not_list_or_download(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': None}])

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert putio.list_files_calls == []
    assert putio.download_calls == []
    assert repo.local_path_calls == []


def test_skips_rows_without_a_transfer_id(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([])
    repo.force_pending([_entry(1, transfer_id=None)])
    putio = FakePutioClient()

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert putio.get_transfer_calls == []
    assert repo.status_calls == []


def test_already_landed_rows_are_not_returned_by_list_pending_landing(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42, local_path='already-landed.mp4')])
    putio = FakePutioClient()

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert putio.get_transfer_calls == []


def test_filename_is_sanitized_via_filename_builder_legalize(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 1, 'name': 'episode: the *reckoning*.mp4'}])

    LandingWorker(putio, repo, tmp_path).run_iteration()

    downloaded_dest = putio.download_calls[0][1]
    assert ':' not in downloaded_dest.name
    assert '*' not in downloaded_dest.name


# ---------------------------------------------------------------------------
# Telegram lifecycle events
# ---------------------------------------------------------------------------


def test_terminal_landed_event_fires_when_local_path_set(tmp_path: pathlib.Path) -> None:
    """COMPLETED landing in one tick fires both an intermediate 'bt_status_update'
    (previous_status None -> COMPLETED) and the terminal 'bt_landed' — the
    latter is what matters here; see test_status_update_* for the former."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42, matched_filter_id=7)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])
    feed_repo = FakeBtFeedRepo(
        {
            1: BtFeed(
                id=1,
                name='my-feed',
                url='https://feed.example/rss',
                created_at='2026-01-01T00:00:00+00:00',
                updated_at='2026-01-01T00:00:00+00:00',
            )
        }
    )
    filter_repo = FakeBtFilterRepo({7: BtFilter(id=7, name='my-filter', keywords=['x'])})
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(
        putio,
        repo,
        tmp_path,
        bt_feed_repo=feed_repo,
        bt_filter_repo=filter_repo,
        notify_event_send=notify_event_send,
    )
    worker.run_iteration()

    landed = [e for e in events if e['event'] == 'bt_landed']
    assert len(landed) == 1
    event = landed[0]
    assert event['title'] == 'title-1'
    assert event['feed_name'] == 'my-feed'
    assert event['filter_name'] == 'my-filter'
    assert event['local_path'] == 'episode.mp4'
    assert event['putio_transfer_id'] == 42
    assert event['entry_id'] == 1


def test_failed_event_fired_when_download_file_raises(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient(raise_on_download=PutioClientError('boom'))
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(putio, repo, tmp_path, notify_event_send=notify_event_send)
    worker.run_iteration()  # must not raise

    failed = [e for e in events if e['event'] == 'bt_failed']
    assert len(failed) == 1
    assert failed[0]['error_message'] == 'boom'
    # No local_path recorded since the download never completed.
    assert repo.local_path_calls == []


def test_failed_event_fired_when_get_transfer_raises(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient(raise_on_get_transfer=PutioClientError('unreachable'))
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(putio, repo, tmp_path, notify_event_send=notify_event_send)
    worker.run_iteration()  # must not raise

    assert len(events) == 1
    assert events[0]['event'] == 'bt_failed'
    assert events[0]['error_message'] == 'unreachable'


def test_failed_event_fired_on_error_status(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'ERROR', 'file_id': None}])
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(putio, repo, tmp_path, notify_event_send=notify_event_send)
    worker.run_iteration()

    assert len(events) == 1
    assert events[0]['event'] == 'bt_failed'
    assert putio.download_calls == []
    assert repo.status_calls == [(1, 'ERROR')]


def test_notify_failure_does_not_break_landing_loop(tmp_path: pathlib.Path) -> None:
    """A raising notify_event_send must not stop other rows from landing."""
    repo = FakeFeedEntryRepo(
        [
            _entry(1, transfer_id=42),
            _entry(2, transfer_id=43),
        ]
    )
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 991}])
    putio.script_files(991, [{'id': 1, 'name': 'ep1.mp4'}])
    putio.script_transfer(43, [{'status': 'COMPLETED', 'file_id': 992}])
    putio.script_files(992, [{'id': 2, 'name': 'ep2.mp4'}])

    def notify_event_send(*, kwargs: dict[str, object]) -> None:  # noqa: ARG001
        raise RuntimeError('telegram is down')

    worker = LandingWorker(putio, repo, tmp_path, notify_event_send=notify_event_send)
    worker.run_iteration()  # must not raise

    assert sorted(repo.local_path_calls) == [(1, 'ep1.mp4'), (2, 'ep2.mp4')]


def test_no_notify_event_send_wired_does_not_raise(tmp_path: pathlib.Path) -> None:
    """notify_event_send defaults to None (e.g. CLI mode) — must stay a no-op."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])

    LandingWorker(putio, repo, tmp_path).run_iteration()  # must not raise

    assert repo.local_path_calls == [(1, 'episode.mp4')]


# ---------------------------------------------------------------------------
# 404 handling — stale/deleted Put.io transfer (Task 1)
# ---------------------------------------------------------------------------


def test_404_from_put_io_resets_dispatch_state_without_failure_notification(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42, matched_filter_id=7)])
    putio = FakePutioClient(raise_on_get_transfer=PutioNotFoundError('transfer gone'))
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(putio, repo, tmp_path, notify_event_send=notify_event_send)
    worker.run_iteration()  # must not raise

    assert events == []
    assert repo.reset_dispatch_calls == [1]


def test_404_preserves_matched_filter_id(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42, matched_filter_id=7)])
    putio = FakePutioClient(raise_on_get_transfer=PutioNotFoundError('transfer gone'))

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert repo.reset_dispatch_calls == [1]
    entry = repo.get(1)
    assert entry is not None
    assert entry.matched_filter_id == 7
    assert entry.putio_transfer_id is None
    assert entry.putio_status is None
    assert entry.local_path is None


# ---------------------------------------------------------------------------
# Single-file transfer fallback via get_file (Extra fix #1)
# ---------------------------------------------------------------------------


def test_single_file_transfer_downloads_via_get_file_when_list_files_empty(tmp_path: pathlib.Path) -> None:
    """A single-file torrent's transfer.file_id points at the file itself,
    not a folder — list_files(parent_id=file_id) legitimately returns []
    since a file has no children. LandingWorker must fall back to GET
    /files/{id} and download that file directly instead of polling forever."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 555}])
    # list_files(555) intentionally NOT scripted -> defaults to [].
    putio.script_file(555, {'id': 555, 'name': 'episode.mp4', 'file_type': 'VIDEO'})

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert putio.get_file_calls == [555]
    assert putio.download_calls == [(555, tmp_path / 'episode.mp4')]
    assert repo.local_path_calls == [(1, 'episode.mp4')]


def test_completed_with_empty_folder_via_get_file_retries_next_tick(tmp_path: pathlib.Path) -> None:
    """If GET /files/{id} confirms it really is an (empty) folder — not a
    single-file transfer — log and retry next tick rather than downloading
    the folder itself."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 555}])
    putio.script_file(555, {'id': 555, 'name': 'empty-folder', 'file_type': 'FOLDER'})

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert putio.get_file_calls == [555]
    assert putio.download_calls == []
    assert repo.local_path_calls == []


# ---------------------------------------------------------------------------
# In-place status-change notifications (Task 3)
# ---------------------------------------------------------------------------


def test_status_update_edits_existing_message_when_status_changes(tmp_path: pathlib.Path) -> None:
    """bt_status_update only fires when putio_status actually changed since
    the last tick — 'bt_dispatched' (the initial send) is fired upstream by
    the dispatching service, which also sets putio_status='IN_QUEUE' at
    dispatch time, so the first poll here already reflects IN_QUEUE."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    repo.update_putio_status(1, 'IN_QUEUE')  # mirrors mark_dispatched's initial status
    putio = FakePutioClient()
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(putio, repo, tmp_path, notify_event_send=notify_event_send)

    putio.script_transfer(42, [{'status': 'IN_QUEUE', 'file_id': None}])
    worker.run_iteration()
    assert events == []  # unchanged -> no status_update

    putio.script_transfer(42, [{'status': 'DOWNLOADING', 'file_id': None}])
    worker.run_iteration()

    assert len(events) == 1
    assert events[0]['event'] == 'bt_status_update'
    assert events[0]['putio_status'] == 'DOWNLOADING'
    assert events[0]['entry_id'] == 1


def test_status_update_not_fired_when_status_unchanged(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    repo.update_putio_status(1, 'DOWNLOADING')
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'DOWNLOADING', 'file_id': None}])
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(putio, repo, tmp_path, notify_event_send=notify_event_send)
    worker.run_iteration()

    assert events == []


def test_status_update_not_fired_for_error_status(tmp_path: pathlib.Path) -> None:
    """ERROR jumps straight to the 'bt_failed' terminal event — no separate
    'bt_status_update' for it (there's no transient label for ERROR in the
    state machine, only a terminal one)."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    repo.update_putio_status(1, 'DOWNLOADING')
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'ERROR', 'file_id': None}])
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(putio, repo, tmp_path, notify_event_send=notify_event_send)
    worker.run_iteration()

    assert [e['event'] for e in events] == ['bt_failed']


# ---------------------------------------------------------------------------
# task_history integration (Extra fix #2)
# ---------------------------------------------------------------------------


def test_landed_finishes_task_history_with_local_path_as_filename(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])
    task_history_repo = FakeTaskHistoryRepo()
    task_id_map_repo = FakeTaskIdMapRepo()
    task_sn = task_id_map_repo.allocate('bt', '1')
    task_history_repo.seed_in_progress(task_sn, row_id=10)

    worker = LandingWorker(
        putio,
        repo,
        tmp_path,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
    )
    worker.run_iteration()

    assert task_history_repo.finish_calls == [{'row_id': 10, 'final_status': '下載完成', 'filename': 'episode.mp4'}]


def test_failed_finishes_task_history_with_error_status(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'ERROR', 'file_id': None}])
    task_history_repo = FakeTaskHistoryRepo()
    task_id_map_repo = FakeTaskIdMapRepo()
    task_sn = task_id_map_repo.allocate('bt', '1')
    task_history_repo.seed_in_progress(task_sn, row_id=11)

    worker = LandingWorker(
        putio,
        repo,
        tmp_path,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
    )
    worker.run_iteration()

    assert task_history_repo.finish_calls == [{'row_id': 11, 'final_status': '下載失敗', 'filename': None}]


def test_failed_via_generic_exception_finishes_task_history(tmp_path: pathlib.Path) -> None:
    """The run_iteration-level catch-all (e.g. download_file raising) must
    also finish task_history — not just the ERROR-status branch."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient(raise_on_download=PutioClientError('boom'))
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])
    task_history_repo = FakeTaskHistoryRepo()
    task_id_map_repo = FakeTaskIdMapRepo()
    task_sn = task_id_map_repo.allocate('bt', '1')
    task_history_repo.seed_in_progress(task_sn, row_id=12)

    worker = LandingWorker(
        putio,
        repo,
        tmp_path,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
    )
    worker.run_iteration()  # must not raise

    assert task_history_repo.finish_calls == [{'row_id': 12, 'final_status': '下載失敗', 'filename': None}]


def test_404_reset_does_not_touch_task_history(tmp_path: pathlib.Path) -> None:
    """A 404 (stale transfer) resets dispatch state silently — it must NOT
    finish the still-open task_history row, since the entry is expected to
    be re-dispatched and finish naturally later."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42, matched_filter_id=7)])
    putio = FakePutioClient(raise_on_get_transfer=PutioNotFoundError('gone'))
    task_history_repo = FakeTaskHistoryRepo()
    task_id_map_repo = FakeTaskIdMapRepo()
    task_sn = task_id_map_repo.allocate('bt', '1')
    task_history_repo.seed_in_progress(task_sn, row_id=13)

    worker = LandingWorker(
        putio,
        repo,
        tmp_path,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
    )
    worker.run_iteration()  # must not raise

    assert task_history_repo.finish_calls == []
    assert repo.reset_dispatch_calls == [1]


def test_no_task_history_repo_wired_does_not_raise(tmp_path: pathlib.Path) -> None:
    """task_history_repo/task_id_map_repo default to None (e.g. CLI mode) — no-op."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])

    LandingWorker(putio, repo, tmp_path).run_iteration()  # must not raise

    assert repo.local_path_calls == [(1, 'episode.mp4')]


# ---------------------------------------------------------------------------
# bt_status_update payload — percent_done / size from the Put.io transfer
# ---------------------------------------------------------------------------


def test_status_update_includes_percent_done_and_size_from_transfer(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    repo.update_putio_status(1, 'IN_QUEUE')
    putio = FakePutioClient()
    putio.script_transfer(
        42, [{'status': 'DOWNLOADING', 'file_id': None, 'percent_done': 37, 'size': 500 * 1024 * 1024}]
    )
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(putio, repo, tmp_path, notify_event_send=notify_event_send)
    worker.run_iteration()

    assert len(events) == 1
    assert events[0]['event'] == 'bt_status_update'
    assert events[0]['percent_done'] == 37
    assert events[0]['file_size_mb'] == 500


def test_status_update_omits_percent_done_when_absent_from_transfer(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    repo.update_putio_status(1, 'IN_QUEUE')
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'DOWNLOADING', 'file_id': None}])
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(putio, repo, tmp_path, notify_event_send=notify_event_send)
    worker.run_iteration()

    assert 'percent_done' not in events[0]
    assert 'file_size_mb' not in events[0]


# ---------------------------------------------------------------------------
# Landing progress — throttled bt_landing_progress emit (Task: landing infra)
# ---------------------------------------------------------------------------


def test_landing_worker_passes_on_progress_callback_to_download_file(tmp_path: pathlib.Path) -> None:
    """_process_row must wire a real callback into download_file's on_progress."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])

    LandingWorker(putio, repo, tmp_path).run_iteration()

    assert len(putio.on_progress_received) == 1
    assert putio.on_progress_received[0] is not None
    assert callable(putio.on_progress_received[0])


def test_download_progress_throttled_to_5s_between_edits(tmp_path: pathlib.Path) -> None:
    """100 rapid callback invocations that barely move the percentage (all
    within the same tick, so real elapsed time is ~0s) must collapse to a
    single emission — only the very first callback (last_edit_at is None)
    fires; the 5s/10%-jump throttle suppresses the rest."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(FakePutioClient(), repo, tmp_path, notify_event_send=notify_event_send)
    callback = worker._make_landing_progress_callback(_entry(1, transfer_id=42))  # noqa: SLF001

    chunk = 1024 * 1024  # 1 MiB
    total = 10_000 * chunk  # large enough that 100 x 1 MiB chunks stay under a 10% jump
    for i in range(1, 101):
        callback(i * chunk, total)

    landing_events = [e for e in events if e['event'] == 'bt_landing_progress']
    assert len(landing_events) == 1
    assert landing_events[0]['bytes_written'] == chunk


def test_download_progress_emits_on_10_percent_jump_even_before_5s(tmp_path: pathlib.Path) -> None:
    """0%, 10%, 20% in immediate succession (no sleep) must each emit —
    the percent-jump rule fires even though far less than 5s has elapsed."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(FakePutioClient(), repo, tmp_path, notify_event_send=notify_event_send)
    callback = worker._make_landing_progress_callback(_entry(1, transfer_id=42))  # noqa: SLF001

    total = 1000
    callback(0, total)  # 0%
    callback(100, total)  # 10%
    callback(200, total)  # 20%

    landing_events = [e for e in events if e['event'] == 'bt_landing_progress']
    assert len(landing_events) == 3


def test_landing_progress_publishes_rate_and_speed_to_progress_bus(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    progress_bus = FakeProgressBus()
    task_id_map_repo = FakeTaskIdMapRepo()
    expected_sn = task_id_map_repo.allocate('bt', '1')
    task_id_map_repo.allocate_calls.clear()

    worker = LandingWorker(
        FakePutioClient(),
        repo,
        tmp_path,
        progress_bus=progress_bus,
        task_id_map_repo=task_id_map_repo,
    )
    callback = worker._make_landing_progress_callback(_entry(1, transfer_id=42))  # noqa: SLF001

    total = 1000
    callback(0, total)  # first call always emits: rate 0.0, no speed sample yet
    time.sleep(0.02)  # ensure a nonzero time delta for the speed calculation
    callback(500, total)  # 50% jump -> emits again, this time with a real speed sample

    assert len(progress_bus.status_calls) == 2
    assert all(status == '落地中' for _sn, status in progress_bus.status_calls)
    assert len(progress_bus.stats_calls) == 2
    assert progress_bus.stats_calls[0]['rate'] == 0.0
    assert progress_bus.stats_calls[0]['speed_mbps'] is None  # no previous sample on the first callback
    assert progress_bus.stats_calls[1]['rate'] == 0.5
    assert progress_bus.stats_calls[1]['speed_mbps'] is not None
    assert progress_bus.stats_calls[1]['speed_mbps'] > 0
    assert progress_bus.stats_calls[1]['eta_seconds'] is not None
    for sn, _status in progress_bus.status_calls:
        assert sn == expected_sn


def test_landed_finishes_progress_bus_entry(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])
    progress_bus = FakeProgressBus()
    task_id_map_repo = FakeTaskIdMapRepo()
    expected_sn = task_id_map_repo.allocate('bt', '1')
    task_id_map_repo.allocate_calls.clear()

    worker = LandingWorker(
        putio,
        repo,
        tmp_path,
        progress_bus=progress_bus,
        task_id_map_repo=task_id_map_repo,
    )
    worker.run_iteration()

    assert expected_sn in progress_bus.finish_calls
    assert any(status == '下載完成' for _sn, status in progress_bus.status_calls)
    assert any(call['filename'] == 'episode.mp4' for call in progress_bus.metadata_calls)


def test_failed_finishes_progress_bus_entry_with_失敗(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'ERROR', 'file_id': None}])
    progress_bus = FakeProgressBus()
    task_id_map_repo = FakeTaskIdMapRepo()
    expected_sn = task_id_map_repo.allocate('bt', '1')
    task_id_map_repo.allocate_calls.clear()

    worker = LandingWorker(
        putio,
        repo,
        tmp_path,
        progress_bus=progress_bus,
        task_id_map_repo=task_id_map_repo,
    )
    worker.run_iteration()

    assert expected_sn in progress_bus.finish_calls
    assert (expected_sn, '失敗') in progress_bus.status_calls


def test_404_reset_finishes_progress_bus_with_中斷(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42, matched_filter_id=7)])
    putio = FakePutioClient(raise_on_get_transfer=PutioNotFoundError('gone'))
    progress_bus = FakeProgressBus()
    task_id_map_repo = FakeTaskIdMapRepo()
    expected_sn = task_id_map_repo.allocate('bt', '1')
    task_id_map_repo.allocate_calls.clear()

    worker = LandingWorker(
        putio,
        repo,
        tmp_path,
        progress_bus=progress_bus,
        task_id_map_repo=task_id_map_repo,
    )
    worker.run_iteration()  # must not raise

    assert expected_sn in progress_bus.finish_calls
    assert (expected_sn, '中斷') in progress_bus.status_calls


def test_no_progress_bus_wired_does_not_raise(tmp_path: pathlib.Path) -> None:
    """progress_bus defaults to None — every progress-bus call site must stay a no-op."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])

    LandingWorker(putio, repo, tmp_path).run_iteration()  # must not raise


# ---------------------------------------------------------------------------
# Auto-delete Put.io remote after successful landing
# ---------------------------------------------------------------------------


def test_auto_delete_remote_fires_after_successful_landing_when_setting_on(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])
    settings_repo = FakeSettingsRepo(auto_delete_remote_on_landed=True)

    worker = LandingWorker(putio, repo, tmp_path, settings_repo=settings_repo)
    worker.run_iteration()

    # Deletes the transfer's top-level file_id (99), not the individual
    # downloaded file's id (555) — matches the id LandingWorker already
    # resolved from the transfer to list/download files in the first place.
    assert putio.delete_file_calls == [99]
    assert repo.mark_remote_cleared_calls == [1]
    result = repo.get(1)
    assert result is not None
    assert result.putio_status == '遠端已清理'
    assert result.remote_cleared_at is not None
    assert result.local_path == 'episode.mp4'  # landing itself is unaffected


def test_auto_delete_remote_skipped_when_setting_off(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])
    settings_repo = FakeSettingsRepo(auto_delete_remote_on_landed=False)

    worker = LandingWorker(putio, repo, tmp_path, settings_repo=settings_repo)
    worker.run_iteration()

    assert putio.delete_file_calls == []
    assert repo.mark_remote_cleared_calls == []
    result = repo.get(1)
    assert result is not None
    assert result.local_path == 'episode.mp4'
    assert result.remote_cleared_at is None


def test_auto_delete_remote_not_attempted_when_no_settings_repo_wired(tmp_path: pathlib.Path) -> None:
    """settings_repo defaults to None — auto-delete must stay a silent no-op."""
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])

    LandingWorker(putio, repo, tmp_path).run_iteration()  # must not raise

    assert putio.delete_file_calls == []
    assert repo.mark_remote_cleared_calls == []


def test_auto_delete_remote_failure_logged_but_landing_still_marked_successful(tmp_path: pathlib.Path) -> None:
    repo = FakeFeedEntryRepo([_entry(1, transfer_id=42)])
    putio = FakePutioClient(raise_on_delete=PutioClientError('put.io rejected the delete'))
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    putio.script_files(99, [{'id': 555, 'name': 'episode.mp4'}])
    settings_repo = FakeSettingsRepo(auto_delete_remote_on_landed=True)
    logger = FakeLogger()
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    worker = LandingWorker(
        putio,
        repo,
        tmp_path,
        settings_repo=settings_repo,
        logger=logger,
        notify_event_send=notify_event_send,
    )
    worker.run_iteration()  # must not raise

    # Delete was attempted and failed, but the row is still marked landed —
    # a remote-delete failure must never turn a successful landing into a
    # reported failure.
    assert putio.delete_file_calls == [99]
    assert repo.mark_remote_cleared_calls == []
    result = repo.get(1)
    assert result is not None
    assert result.local_path == 'episode.mp4'
    assert result.remote_cleared_at is None

    # Delete failing must not fire bt_failed — 'bt_landed' still fires (a
    # 'bt_status_update' also fires for the None -> COMPLETED transition,
    # same as test_terminal_landed_event_fires_when_local_path_set).
    assert 'bt_landed' in [e['event'] for e in events]
    assert 'bt_failed' not in [e['event'] for e in events]
    assert any('遠端刪除失敗' in msg for msg in logger.error_messages)


# ---------------------------------------------------------------------------
# Post-landing remote status refresh (run_remote_refresh_iteration)
# ---------------------------------------------------------------------------


def test_remote_refresh_updates_status_on_transition(tmp_path: pathlib.Path) -> None:
    entry = _entry(1, transfer_id=42, local_path='episode.mp4')
    entry = entry.model_copy(update={'putio_status': 'SEEDING'})
    repo = FakeFeedEntryRepo([entry])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])

    worker = LandingWorker(putio, repo, tmp_path)
    worker.run_remote_refresh_iteration()

    assert putio.get_transfer_calls == [42]
    assert repo.status_calls == [(1, 'COMPLETED')]
    result = repo.get(1)
    assert result is not None
    assert result.putio_status == 'COMPLETED'
    assert result.remote_cleared_at is None  # a status transition is not a clear/removal


def test_remote_refresh_marks_removed_on_404(tmp_path: pathlib.Path) -> None:
    entry = _entry(1, transfer_id=42, local_path='episode.mp4')
    entry = entry.model_copy(update={'putio_status': 'SEEDING'})
    repo = FakeFeedEntryRepo([entry])
    putio = FakePutioClient(raise_on_get_transfer=PutioNotFoundError('transfer gone'))

    worker = LandingWorker(putio, repo, tmp_path)
    worker.run_remote_refresh_iteration()

    assert repo.mark_remote_removed_calls == [1]
    result = repo.get(1)
    assert result is not None
    assert result.putio_status == '遠端已移除'
    assert result.remote_cleared_at is not None
    assert result.local_path == 'episode.mp4'  # local file is untouched


def test_remote_refresh_skips_already_cleared_rows(tmp_path: pathlib.Path) -> None:
    cleared = _entry(1, transfer_id=42, local_path='episode.mp4')
    cleared = cleared.model_copy(update={'remote_cleared_at': '2026-01-01T00:00:00+00:00'})
    pending = _entry(2, transfer_id=43, local_path='episode2.mp4')
    repo = FakeFeedEntryRepo([cleared, pending])
    putio = FakePutioClient()
    putio.script_transfer(43, [{'status': 'COMPLETED', 'file_id': 100}])

    worker = LandingWorker(putio, repo, tmp_path)
    worker.run_remote_refresh_iteration()

    # Only the not-yet-cleared row (id=2, transfer_id=43) is polled.
    assert putio.get_transfer_calls == [43]


def test_remote_refresh_get_transfer_generic_failure_is_logged_and_isolated(tmp_path: pathlib.Path) -> None:
    entry = _entry(1, transfer_id=42, local_path='episode.mp4')
    repo = FakeFeedEntryRepo([entry])
    putio = FakePutioClient(raise_on_get_transfer=PutioClientError('unreachable'))
    logger = FakeLogger()

    worker = LandingWorker(putio, repo, tmp_path, logger=logger)
    worker.run_remote_refresh_iteration()  # must not raise

    assert repo.mark_remote_removed_calls == []
    assert repo.status_calls == []
    assert any('遠端狀態檢查失敗' in msg for msg in logger.error_messages)


def test_remote_refresh_handles_multiple_rows_independently(tmp_path: pathlib.Path) -> None:
    """One row 404ing must not prevent another row's status update from applying."""
    gone = _entry(1, transfer_id=42, local_path='gone.mp4')
    transitions = _entry(2, transfer_id=43, local_path='still-here.mp4')
    transitions = transitions.model_copy(update={'putio_status': 'SEEDING'})
    repo = FakeFeedEntryRepo([gone, transitions])

    responses = {42: PutioNotFoundError('gone'), 43: None}

    class _MultiPutioClient(FakePutioClient):
        def get_transfer(self, transfer_id: int) -> dict[str, T.Any]:
            outcome = responses[transfer_id]
            if isinstance(outcome, Exception):
                raise outcome
            return {'status': 'COMPLETED', 'file_id': 200}

    worker = LandingWorker(_MultiPutioClient(), repo, tmp_path)
    worker.run_remote_refresh_iteration()

    assert repo.mark_remote_removed_calls == [1]


# ---------------------------------------------------------------------------
# MEDIUM-4 security fix — remote refresh batch cap
# ---------------------------------------------------------------------------


def test_remote_refresh_respects_batch_size(tmp_path: pathlib.Path) -> None:
    entries = [
        _entry(i, transfer_id=100 + i, local_path=f'ep{i}.mp4').model_copy(
            update={'fetched_at': f'2026-01-01T00:00:{i:02d}+00:00'}
        )
        for i in range(5)
    ]
    repo = FakeFeedEntryRepo(entries)
    putio = FakePutioClient()
    for i in range(5):
        putio.script_transfer(100 + i, [{'status': 'SEEDING', 'file_id': 200 + i}])

    worker = LandingWorker(putio, repo, tmp_path)
    worker.run_remote_refresh_iteration(batch_size=2)

    # Newest fetched_at first (id=4, id=3) — the cap stops at 2 rows even
    # though 5 qualify.
    assert putio.get_transfer_calls == [104, 103]


def test_remote_refresh_batch_full_logs_a_note(tmp_path: pathlib.Path) -> None:
    entries = [_entry(i, transfer_id=100 + i, local_path=f'ep{i}.mp4') for i in range(3)]
    repo = FakeFeedEntryRepo(entries)
    putio = FakePutioClient()
    for i in range(3):
        putio.script_transfer(100 + i, [{'status': 'SEEDING', 'file_id': 200 + i}])
    logger = FakeLogger()

    worker = LandingWorker(putio, repo, tmp_path, logger=logger)
    worker.run_remote_refresh_iteration(batch_size=2)

    assert any('批次已滿' in msg for msg in logger.info_messages)


def test_remote_refresh_batch_not_full_does_not_log_a_note(tmp_path: pathlib.Path) -> None:
    entries = [_entry(i, transfer_id=100 + i, local_path=f'ep{i}.mp4') for i in range(2)]
    repo = FakeFeedEntryRepo(entries)
    putio = FakePutioClient()
    for i in range(2):
        putio.script_transfer(100 + i, [{'status': 'SEEDING', 'file_id': 200 + i}])
    logger = FakeLogger()

    worker = LandingWorker(putio, repo, tmp_path, logger=logger)
    worker.run_remote_refresh_iteration(batch_size=5)

    assert not any('批次已滿' in msg for msg in logger.info_messages)


def test_remote_refresh_rows_beyond_batch_are_picked_up_once_the_leader_clears(tmp_path: pathlib.Path) -> None:
    """Newest-fetched-first means the same leader row wins every tick while
    it stays in the pending set — once it clears (here: its transfer is
    gone, so it's marked remote-removed and drops out of the candidate set),
    the next tick's single slot moves on to the older row."""
    older = _entry(1, transfer_id=101, local_path='older.mp4').model_copy(
        update={'fetched_at': '2026-01-01T00:00:00+00:00'}
    )
    newer = _entry(2, transfer_id=102, local_path='newer.mp4').model_copy(
        update={'fetched_at': '2026-01-02T00:00:00+00:00'}
    )
    repo = FakeFeedEntryRepo([older, newer])

    class _NewerGoneClient(FakePutioClient):
        def get_transfer(self, transfer_id: int) -> dict[str, T.Any]:
            self.get_transfer_calls.append(transfer_id)
            if transfer_id == 102:
                raise PutioNotFoundError('gone')
            return {'status': 'SEEDING', 'file_id': 201}

    putio = _NewerGoneClient()
    worker = LandingWorker(putio, repo, tmp_path)

    worker.run_remote_refresh_iteration(batch_size=1)
    assert putio.get_transfer_calls == [102]  # the newer row wins the single slot
    assert repo.mark_remote_removed_calls == [2]  # ...and clears out of the pending set

    worker.run_remote_refresh_iteration(batch_size=1)
    assert putio.get_transfer_calls == [102, 101]  # now the older row gets its turn


# ---------------------------------------------------------------------------
# Retro-active auto-delete-remote-on-landed backfill (run_remote_refresh_iteration)
# ---------------------------------------------------------------------------


def test_refresh_retro_deletes_when_setting_enabled_and_status_landable(tmp_path: pathlib.Path) -> None:
    """Rows that landed before auto-delete-remote-on-landed existed (or while it
    was off) have remote_cleared_at IS NULL forever unless something retries the
    delete. run_remote_refresh_iteration is that retry: once it observes a
    landable status for such a row, it should fire the same delete + mark_cleared
    flow landing-time auto-delete uses."""
    entry = _entry(1, transfer_id=42, local_path='episode.mp4')
    entry = entry.model_copy(update={'putio_status': 'SEEDING'})
    repo = FakeFeedEntryRepo([entry])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    settings_repo = FakeSettingsRepo(auto_delete_remote_on_landed=True)

    worker = LandingWorker(putio, repo, tmp_path, settings_repo=settings_repo)
    worker.run_remote_refresh_iteration()

    assert putio.delete_file_calls == [99]
    assert repo.mark_remote_cleared_calls == [1]
    result = repo.get(1)
    assert result is not None
    assert result.putio_status == '遠端已清理'
    assert result.remote_cleared_at is not None


def test_refresh_retro_delete_skipped_when_setting_disabled(tmp_path: pathlib.Path) -> None:
    entry = _entry(1, transfer_id=42, local_path='episode.mp4')
    entry = entry.model_copy(update={'putio_status': 'SEEDING'})
    repo = FakeFeedEntryRepo([entry])
    putio = FakePutioClient()
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    settings_repo = FakeSettingsRepo(auto_delete_remote_on_landed=False)

    worker = LandingWorker(putio, repo, tmp_path, settings_repo=settings_repo)
    worker.run_remote_refresh_iteration()

    assert putio.delete_file_calls == []
    assert repo.mark_remote_cleared_calls == []
    result = repo.get(1)
    assert result is not None
    assert result.putio_status == 'COMPLETED'  # the status transition itself still applies
    assert result.remote_cleared_at is None


def test_refresh_retro_delete_failure_logs_warning_but_status_still_updated(tmp_path: pathlib.Path) -> None:
    entry = _entry(1, transfer_id=42, local_path='episode.mp4')
    entry = entry.model_copy(update={'putio_status': 'SEEDING'})
    repo = FakeFeedEntryRepo([entry])
    putio = FakePutioClient(raise_on_delete=PutioClientError('put.io rejected the delete'))
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    settings_repo = FakeSettingsRepo(auto_delete_remote_on_landed=True)
    logger = FakeLogger()

    worker = LandingWorker(putio, repo, tmp_path, settings_repo=settings_repo, logger=logger)
    worker.run_remote_refresh_iteration()  # must not raise

    assert putio.delete_file_calls == [99]
    assert repo.mark_remote_cleared_calls == []
    result = repo.get(1)
    assert result is not None
    # The SEEDING -> COMPLETED status transition is independent of the retro
    # delete attempt and must still be recorded even though the delete failed.
    assert result.putio_status == 'COMPLETED'
    assert result.remote_cleared_at is None
    # Distinct tag from the landing-time '遠端刪除失敗' so log grep can tell
    # a retro-cleanup failure apart from a landing-time delete failure.
    assert any('遠端補刪失敗' in msg for msg in logger.error_messages)
    assert not any('遠端刪除失敗' in msg for msg in logger.error_messages)


def test_refresh_retro_delete_gracefully_handles_already_deleted_race(tmp_path: pathlib.Path) -> None:
    """Put.io 404s the delete call because the file is already gone (removed
    externally, or an earlier crashed attempt actually succeeded) — both landed
    and remote-cleared are still the correct terminal state, so this should be
    treated as success (mark_remote_cleared fires) and logged at INFO, not as
    an error."""
    entry = _entry(1, transfer_id=42, local_path='episode.mp4')
    entry = entry.model_copy(update={'putio_status': 'SEEDING'})
    repo = FakeFeedEntryRepo([entry])
    putio = FakePutioClient(raise_on_delete=PutioNotFoundError('already gone'))
    putio.script_transfer(42, [{'status': 'COMPLETED', 'file_id': 99}])
    settings_repo = FakeSettingsRepo(auto_delete_remote_on_landed=True)
    logger = FakeLogger()

    worker = LandingWorker(putio, repo, tmp_path, settings_repo=settings_repo, logger=logger)
    worker.run_remote_refresh_iteration()  # must not raise

    assert putio.delete_file_calls == [99]
    assert repo.mark_remote_cleared_calls == [1]
    result = repo.get(1)
    assert result is not None
    assert result.putio_status == '遠端已清理'
    assert result.remote_cleared_at is not None
    assert not logger.error_messages  # raced-with-external-delete is INFO, not an error


# ---------------------------------------------------------------------------
# MEDIUM-5 security fix — Put.io 429 rate-limit backoff + retry-once
# ---------------------------------------------------------------------------


class _RateLimitedThenOkClient(FakePutioClient):
    """``get_transfer`` raises ``PutioRateLimitError`` once, then succeeds."""

    def __init__(self, *, response: dict[str, T.Any], retry_after: int = 5, always_rate_limited: bool = False) -> None:
        super().__init__()
        self._response = response
        self._retry_after = retry_after
        self._always_rate_limited = always_rate_limited
        self._calls = 0

    def get_transfer(self, transfer_id: int) -> dict[str, T.Any]:
        self._calls += 1
        self.get_transfer_calls.append(transfer_id)
        if self._always_rate_limited or self._calls == 1:
            raise PutioRateLimitError('rate limited', retry_after=self._retry_after)
        return self._response


def test_run_iteration_retries_once_after_429_and_succeeds(tmp_path: pathlib.Path) -> None:
    entry = _entry(1, transfer_id=42)
    repo = FakeFeedEntryRepo([entry])
    putio = _RateLimitedThenOkClient(response={'status': 'IN_QUEUE', 'file_id': None}, retry_after=3)
    logger = FakeLogger()

    with unittest.mock.patch('app.bt_downloader.landing_worker.time.sleep') as mock_sleep:
        worker = LandingWorker(putio, repo, tmp_path, logger=logger)
        worker.run_iteration()

    mock_sleep.assert_called_once_with(3)
    assert putio.get_transfer_calls == [42, 42]  # one retry
    assert repo.status_calls == [(1, 'IN_QUEUE')]  # the retry's result was applied
    assert any('rate limit' in msg.lower() or '429' in msg for msg in logger.info_messages)


def test_run_iteration_429_retry_delay_capped_at_60_seconds(tmp_path: pathlib.Path) -> None:
    entry = _entry(1, transfer_id=42)
    repo = FakeFeedEntryRepo([entry])
    putio = _RateLimitedThenOkClient(response={'status': 'IN_QUEUE', 'file_id': None}, retry_after=999)

    with unittest.mock.patch('app.bt_downloader.landing_worker.time.sleep') as mock_sleep:
        worker = LandingWorker(putio, repo, tmp_path)
        worker.run_iteration()

    mock_sleep.assert_called_once_with(60)  # capped, not the full 999s


def test_run_iteration_still_rate_limited_after_retry_defers_to_next_tick(tmp_path: pathlib.Path) -> None:
    """A second consecutive 429 must not fire bt_failed — it's deferred (logged only)."""
    entry = _entry(1, transfer_id=42)
    repo = FakeFeedEntryRepo([entry])
    putio = _RateLimitedThenOkClient(
        response={'status': 'IN_QUEUE', 'file_id': None}, retry_after=1, always_rate_limited=True
    )
    events: list[dict[str, object]] = []

    with unittest.mock.patch('app.bt_downloader.landing_worker.time.sleep'):
        worker = LandingWorker(putio, repo, tmp_path, notify_event_send=lambda **kw: events.append(kw['kwargs']))
        worker.run_iteration()

    assert putio.get_transfer_calls == [42, 42]
    assert 'bt_failed' not in [e['event'] for e in events]
    assert repo.status_calls == []  # never got far enough to record a status


def test_run_iteration_non_rate_limit_failure_after_429_retry_fires_bt_failed(tmp_path: pathlib.Path) -> None:
    """If the retry hits a *different* failure (not another 429), the normal bt_failed path still applies."""
    entry = _entry(1, transfer_id=42)
    repo = FakeFeedEntryRepo([entry])

    class _RateLimitThenBoom(FakePutioClient):
        def __init__(self) -> None:
            super().__init__()
            self._calls = 0

        def get_transfer(self, transfer_id: int) -> dict[str, T.Any]:
            self._calls += 1
            self.get_transfer_calls.append(transfer_id)
            if self._calls == 1:
                raise PutioRateLimitError('rate limited', retry_after=1)
            raise PutioClientError('boom')

    putio = _RateLimitThenBoom()
    events: list[dict[str, object]] = []

    with unittest.mock.patch('app.bt_downloader.landing_worker.time.sleep'):
        worker = LandingWorker(putio, repo, tmp_path, notify_event_send=lambda **kw: events.append(kw['kwargs']))
        worker.run_iteration()

    assert 'bt_failed' in [e['event'] for e in events]


# ---------------------------------------------------------------------------
# MEDIUM-5 security fix — auto-delete-remote rate note
# ---------------------------------------------------------------------------


def test_auto_delete_remote_logs_rate_note_past_threshold(tmp_path: pathlib.Path) -> None:
    entries = [_entry(i, transfer_id=100 + i) for i in range(21)]
    repo = FakeFeedEntryRepo(entries)
    putio = FakePutioClient()
    for i in range(21):
        putio.script_transfer(100 + i, [{'status': 'COMPLETED', 'file_id': 200 + i}])
        putio.script_files(200 + i, [{'id': 300 + i, 'name': f'ep{i}.mp4'}])
    logger = FakeLogger()
    settings_repo = FakeSettingsRepo(auto_delete_remote_on_landed=True)

    worker = LandingWorker(putio, repo, tmp_path, logger=logger, settings_repo=settings_repo)
    worker.run_iteration()

    assert len(putio.delete_file_calls) == 21
    assert any('20' in msg and ('速率' in msg or '偏高' in msg) for msg in logger.info_messages)


def test_auto_delete_remote_does_not_log_rate_note_under_threshold(tmp_path: pathlib.Path) -> None:
    entries = [_entry(i, transfer_id=100 + i) for i in range(5)]
    repo = FakeFeedEntryRepo(entries)
    putio = FakePutioClient()
    for i in range(5):
        putio.script_transfer(100 + i, [{'status': 'COMPLETED', 'file_id': 200 + i}])
        putio.script_files(200 + i, [{'id': 300 + i, 'name': f'ep{i}.mp4'}])
    logger = FakeLogger()
    settings_repo = FakeSettingsRepo(auto_delete_remote_on_landed=True)

    worker = LandingWorker(putio, repo, tmp_path, logger=logger, settings_repo=settings_repo)
    worker.run_iteration()

    assert len(putio.delete_file_calls) == 5
    assert not any('速率' in msg or '偏高' in msg for msg in logger.info_messages)

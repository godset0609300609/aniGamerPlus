"""Tests for :class:`DownloadWorker`.

Uses fake Anime + fake AnimeRepository + real ``ProgressBus`` + real
``TaskQueue`` so only the worker's control-flow is under test.
"""

from __future__ import annotations

import dataclasses
import datetime
import pathlib
import threading
from typing import Any

import pytest

from app.downloader import exceptions
from app.downloader.anime import DownloadResult
from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.models import AppSettings
from app.persistence.repositories import AnimeRow
from app.scheduler.queue_ import TaskInfo, TaskQueue
from app.scheduler.worker import DownloadWorker

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeAnime:
    """Stand-in for :class:`Anime` with adjustable ``download`` / ``upload``
    behaviour and a call log."""

    def __init__(
        self,
        sn: int,
        *,
        download_result: DownloadResult | None = None,
        download_raises: BaseException | None = None,
        upload_result: bool = True,
        upload_raises: BaseException | None = None,
        title: str = '《某某》 [01]',
        bangumi_name: str = '某某',
        episode: str = '01',
    ) -> None:
        self.sn = int(sn)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._download_result = download_result
        self._download_raises = download_raises
        self._upload_result = upload_result
        self._upload_raises = upload_raises
        self._title = title
        self._bangumi_name = bangumi_name
        self._episode = episode

    def load(self) -> None:
        self.calls.append(('load', {}))

    def get_title(self) -> str:
        return self._title

    def get_bangumi_name(self) -> str:
        return self._bangumi_name

    def get_episode(self) -> str:
        return self._episode

    def get_resolution(self) -> int:
        return 1080

    def download(self, **kwargs: Any) -> DownloadResult:
        self.calls.append(('download', kwargs))
        if self._download_raises is not None:
            raise self._download_raises
        assert self._download_result is not None
        return self._download_result

    def upload(self, **kwargs: Any) -> bool:
        self.calls.append(('upload', kwargs))
        if self._upload_raises is not None:
            raise self._upload_raises
        return self._upload_result


class FakeAnimeRepository:
    """In-memory :class:`AnimeRepository` stand-in."""

    def __init__(self) -> None:
        self._rows: dict[int, AnimeRow] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def read(self, sn: int) -> AnimeRow | None:
        return self._rows.get(int(sn))

    def insert(
        self,
        *,
        sn: int,
        title: str,
        anime_name: str,
        episode: str,
        resolution: int,
        file_size: int,
        local_file_path: str | None = None,
    ) -> None:
        self.calls.append(
            (
                'insert',
                {
                    'sn': sn,
                    'title': title,
                    'anime_name': anime_name,
                    'episode': episode,
                    'resolution': resolution,
                    'file_size': file_size,
                    'local_file_path': local_file_path,
                },
            )
        )
        self._rows[int(sn)] = AnimeRow(
            sn=int(sn),
            title=title,
            anime_name=anime_name,
            episode=episode,
            status=0,
            remote_status=0,
            resolution=resolution,
            file_size=file_size,
            local_file_path=local_file_path,
            created_time=datetime.datetime.now(),
        )

    def update(self, sn: int, **kwargs: Any) -> None:
        self.calls.append(('update', {'sn': sn, **kwargs}))
        existing = self._rows.get(int(sn))
        if existing is None:
            return
        new = dataclasses.replace(existing, **kwargs)
        self._rows[int(sn)] = new

    # Test helper
    def set_row(
        self,
        sn: int,
        *,
        status: int = 1,
        remote_status: int = 0,
    ) -> None:
        self._rows[int(sn)] = AnimeRow(
            sn=int(sn),
            title='t',
            anime_name='a',
            episode='01',
            status=status,
            remote_status=remote_status,
            resolution=1080,
            file_size=500,
            local_file_path='/tmp/a.mp4',
            created_time=datetime.datetime.now(),
        )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Harness:
    worker: DownloadWorker
    queue: TaskQueue
    progress: ProgressBus
    repo: FakeAnimeRepository
    settings: AppSettings
    anime: FakeAnime


def _build(
    tmp_path: pathlib.Path,
    *,
    fake_anime: FakeAnime,
    settings_overrides: dict[str, Any] | None = None,
) -> Harness:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings_kwargs: dict[str, Any] = {
        'download_resolution': '1080',
        'classify_bangumi': True,
    }
    settings_kwargs.update(settings_overrides or {})
    settings = AppSettings(**settings_kwargs)

    queue = TaskQueue(max_download=2, max_upload=1)
    progress = ProgressBus()
    repo = FakeAnimeRepository()

    worker = DownloadWorker(
        queue=queue,
        anime_factory=lambda sn: fake_anime,  # type: ignore[arg-type]
        anime_repo=repo,  # type: ignore[arg-type]
        progress=progress,
        settings_provider=lambda: settings,
        logger=logger,
    )
    return Harness(
        worker=worker,
        queue=queue,
        progress=progress,
        repo=repo,
        settings=settings,
        anime=fake_anime,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_downloads_persists_and_finishes(tmp_path: pathlib.Path) -> None:
    fake_anime = FakeAnime(
        sn=1,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
    )
    h = _build(tmp_path, fake_anime=fake_anime)
    h.queue.add(1, TaskInfo(sn=1, tag='', mode='single'))
    h.queue.mark_processing(1)
    # Seed a progress entry so we can confirm ``finish`` removed it.
    h.progress.start(1, 'pending', status='正在下載')

    h.worker.run(1)

    # Download was invoked.
    assert any(name == 'download' for name, _ in fake_anime.calls)
    # DB insert ran (no existing row).
    assert any(name == 'insert' for name, _ in h.repo.calls)
    # Then update with status=1.
    assert any(name == 'update' and kw.get('status') == 1 for name, kw in h.repo.calls)
    # Queue is empty, processing cleared, progress finished.
    assert not h.queue.contains(1)
    assert not h.queue.is_processing(1)
    # finish() keeps the entry with finished_at stamped (not deleted).
    snap = h.progress.snapshot()
    assert 1 in snap
    assert snap[1].finished_at is not None


def test_try_too_many_time_error_keeps_sn_in_queue(tmp_path: pathlib.Path) -> None:
    fake_anime = FakeAnime(
        sn=2,
        download_raises=exceptions.TryTooManyTimeError('retries exhausted'),
    )
    h = _build(tmp_path, fake_anime=fake_anime)
    h.queue.add(2, TaskInfo(sn=2, tag='', mode='single'))
    h.queue.mark_processing(2)
    h.progress.start(2, 'pending', status='正在下載')

    h.worker.run(2)

    # Still in queue for retry.
    assert h.queue.contains(2)
    assert not h.queue.is_processing(2)
    # Progress shows failure-waiting-restart (mark_retry sets this status).
    snap = h.progress.snapshot()
    assert 2 in snap
    assert snap[2].status == '失敗! 重啓中'
    assert snap[2].retries == 1


def test_no_available_stream_error_pops_and_finishes(tmp_path: pathlib.Path) -> None:
    fake_anime = FakeAnime(
        sn=3,
        download_raises=exceptions.NoAvailableStreamError('vip only'),
    )
    h = _build(tmp_path, fake_anime=fake_anime)
    h.queue.add(3, TaskInfo(sn=3, tag='', mode='single'))
    h.queue.mark_processing(3)
    h.progress.start(3, 'pending', status='正在下載')

    h.worker.run(3)

    # Gave up: popped from queue, progress finished.
    assert not h.queue.contains(3)
    assert not h.queue.is_processing(3)
    # finish() keeps the entry with finished_at stamped (not deleted).
    snap = h.progress.snapshot()
    assert 3 in snap
    assert snap[3].finished_at is not None


def test_upload_runs_when_upload_to_server(tmp_path: pathlib.Path) -> None:
    fake_anime = FakeAnime(
        sn=4,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
        upload_result=True,
    )
    h = _build(
        tmp_path,
        fake_anime=fake_anime,
        settings_overrides={'upload_to_server': True},
    )
    h.queue.add(4, TaskInfo(sn=4, tag='tag', mode='single'))
    h.queue.mark_processing(4)

    h.worker.run(4)

    assert any(name == 'upload' for name, _ in fake_anime.calls)
    # remote_status=1 was persisted.
    assert any(name == 'update' and kw.get('remote_status') == 1 for name, kw in h.repo.calls)


def test_skips_when_already_downloaded_and_no_upload_mode(
    tmp_path: pathlib.Path,
) -> None:
    fake_anime = FakeAnime(sn=5)  # download shouldn't be called
    h = _build(tmp_path, fake_anime=fake_anime)
    h.repo.set_row(5, status=1, remote_status=0)
    h.queue.add(5, TaskInfo(sn=5, tag='', mode='single'))
    h.queue.mark_processing(5)

    h.worker.run(5)

    assert not any(name == 'download' for name, _ in fake_anime.calls)
    assert not h.queue.contains(5)
    assert not h.queue.is_processing(5)


def test_permit_released_on_unexpected_exception(tmp_path: pathlib.Path) -> None:
    fake_anime = FakeAnime(
        sn=7,
        download_raises=RuntimeError('something weird'),
    )
    h = _build(tmp_path, fake_anime=fake_anime)
    h.queue.add(7, TaskInfo(sn=7, tag='', mode='single'))
    h.queue.mark_processing(7)

    with pytest.raises(RuntimeError):
        h.worker.run(7)

    # Permit must have been released despite the exception so another
    # acquire succeeds immediately.
    acquired = h.queue.download_limiter.acquire(timeout=0.5)
    assert acquired
    h.queue.download_limiter.release()


def test_unexpected_exception_still_finishes_progress(
    tmp_path: pathlib.Path,
) -> None:
    """The try/finally around ``_run_pipeline`` guarantees the progress
    entry is always dropped on an unexpected terminal exception."""
    fake_anime = FakeAnime(
        sn=70,
        download_raises=RuntimeError('unexpected'),
    )
    h = _build(tmp_path, fake_anime=fake_anime)
    h.queue.add(70, TaskInfo(sn=70, tag='', mode='single'))
    h.queue.mark_processing(70)
    h.progress.start(70, 'pending', status='正在下載')

    with pytest.raises(RuntimeError):
        h.worker.run(70)

    # Progress entry must be finished (finished_at stamped) by the finally block.
    snap = h.progress.snapshot()
    assert 70 in snap
    assert snap[70].finished_at is not None


def test_no_available_stream_during_load_pops_and_finishes(
    tmp_path: pathlib.Path,
) -> None:
    """``Anime.load()`` raising ``NoAvailableStreamError`` is unrecoverable
    and must finish + pop."""

    class _BrokenAnime(FakeAnime):
        def load(self) -> None:
            super().load()
            raise exceptions.NoAvailableStreamError('load failed')

    fake_anime = _BrokenAnime(sn=71)
    h = _build(tmp_path, fake_anime=fake_anime)
    h.queue.add(71, TaskInfo(sn=71, tag='', mode='single'))
    h.queue.mark_processing(71)
    h.progress.start(71, 'pending', status='正在解析')

    h.worker.run(71)

    assert not h.queue.contains(71)
    # finish() keeps the entry with finished_at stamped (not deleted).
    snap = h.progress.snapshot()
    assert 71 in snap
    assert snap[71].finished_at is not None


def test_try_too_many_time_during_load_leaves_entry(
    tmp_path: pathlib.Path,
) -> None:
    """Recoverable ``TryTooManyTimeError`` at load time must keep the
    progress entry visible with ``'任務失敗, 等待重啓'``."""

    class _FlakyAnime(FakeAnime):
        def load(self) -> None:
            super().load()
            raise exceptions.TryTooManyTimeError('load retries exhausted')

    fake_anime = _FlakyAnime(sn=72)
    h = _build(tmp_path, fake_anime=fake_anime)
    h.queue.add(72, TaskInfo(sn=72, tag='', mode='single'))
    h.queue.mark_processing(72)
    h.progress.start(72, 'pending', status='正在解析')

    h.worker.run(72)

    # Still in queue for retry; progress shows failure status from mark_retry.
    assert h.queue.contains(72)
    snap = h.progress.snapshot()
    assert 72 in snap
    assert snap[72].status == '失敗! 重啓中'
    assert snap[72].retries == 1


def test_upload_sets_uploading_status(tmp_path: pathlib.Path) -> None:
    """During FTP upload the progress entry shows ``'正在上傳'`` as the last
    status before the worker's terminal ``finish(sn)``."""
    fake_anime = FakeAnime(
        sn=73,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
        upload_result=True,
    )
    h = _build(
        tmp_path,
        fake_anime=fake_anime,
        settings_overrides={'upload_to_server': True},
    )
    h.queue.add(73, TaskInfo(sn=73, tag='tag', mode='single'))
    h.queue.mark_processing(73)
    h.progress.start(73, 'preview', status='正在解析')

    # Capture the sequence of status updates.
    seen: list[str] = []
    orig_update = h.progress.update_status

    def record_update(sn: int, status: str) -> None:
        seen.append(status)
        orig_update(sn, status)

    h.progress.update_status = record_update  # type: ignore[assignment]

    h.worker.run(73)

    assert '正在上傳' in seen
    assert '下載完成' not in seen
    # Progress entry finished (finished_at stamped) on terminal finish.
    snap = h.progress.snapshot()
    assert 73 in snap
    assert snap[73].finished_at is not None


def test_auto_mode_passes_include_resolution_false(tmp_path: pathlib.Path) -> None:
    """Worker (auto-mode) must pass include_resolution_in_filename=False to anime.download()."""
    fake_anime = FakeAnime(
        sn=80,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
    )
    h = _build(tmp_path, fake_anime=fake_anime)
    h.queue.add(80, TaskInfo(sn=80, tag='', mode='single'))
    h.queue.mark_processing(80)

    h.worker.run(80)

    download_calls = [kw for name, kw in fake_anime.calls if name == 'download']
    assert len(download_calls) == 1
    assert download_calls[0].get('include_resolution_in_filename') is False


def test_concurrent_workers_respect_download_limiter(tmp_path: pathlib.Path) -> None:
    """Spin up 3 workers against a queue with ``max_download=1``; verify
    at no point does more than one worker hold the permit."""
    in_flight = [0]
    max_in_flight = [0]
    lock = threading.Lock()

    def make_fake(sn: int) -> FakeAnime:
        fa = FakeAnime(
            sn=sn,
            download_result=DownloadResult(
                success=True,
                file_path=tmp_path / f'{sn}.mp4',
                size_mb=500,
            ),
        )
        real_download = fa.download

        def tracked_download(**kwargs: Any) -> DownloadResult:
            with lock:
                in_flight[0] += 1
                max_in_flight[0] = max(max_in_flight[0], in_flight[0])
            try:
                return real_download(**kwargs)
            finally:
                with lock:
                    in_flight[0] -= 1

        fa.download = tracked_download  # type: ignore[assignment]
        return fa

    fakes = [make_fake(sn) for sn in (11, 12, 13)]
    by_sn = {fa.sn: fa for fa in fakes}

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(download_resolution='1080')
    queue = TaskQueue(max_download=1, max_upload=1)
    progress = ProgressBus()
    repo = FakeAnimeRepository()

    worker = DownloadWorker(
        queue=queue,
        anime_factory=lambda sn: by_sn[int(sn)],  # type: ignore[arg-type]
        anime_repo=repo,  # type: ignore[arg-type]
        progress=progress,
        settings_provider=lambda: settings,
        logger=logger,
    )

    for sn in (11, 12, 13):
        queue.add(sn, TaskInfo(sn=sn, tag='', mode='single'))
        queue.mark_processing(sn)

    threads = [threading.Thread(target=worker.run, args=(sn,), daemon=True) for sn in (11, 12, 13)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert max_in_flight[0] == 1


def test_task_info_carries_custom_name(tmp_path: pathlib.Path) -> None:
    """TaskInfo stores custom_name and it defaults to None."""
    info_with = TaskInfo(sn=1, tag='', mode='single', custom_name='覆蓋名')
    assert info_with.custom_name == '覆蓋名'

    info_without = TaskInfo(sn=2, tag='', mode='single')
    assert info_without.custom_name is None


def test_worker_passes_custom_name_to_anime_download(tmp_path: pathlib.Path) -> None:
    """Worker forwards TaskInfo.custom_name to anime.download() as a kwarg."""
    fake_anime = FakeAnime(
        sn=90,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
    )
    h = _build(tmp_path, fake_anime=fake_anime)
    h.queue.add(90, TaskInfo(sn=90, tag='', mode='single', custom_name='自訂名稱'))
    h.queue.mark_processing(90)

    h.worker.run(90)

    download_calls = [kw for name, kw in fake_anime.calls if name == 'download']
    assert len(download_calls) == 1
    assert download_calls[0].get('custom_name') == '自訂名稱'


def test_worker_passes_none_custom_name_when_not_set(tmp_path: pathlib.Path) -> None:
    """When TaskInfo.custom_name is None, anime.download receives custom_name=None."""
    fake_anime = FakeAnime(
        sn=91,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
    )
    h = _build(tmp_path, fake_anime=fake_anime)
    h.queue.add(91, TaskInfo(sn=91, tag='', mode='single', custom_name=None))
    h.queue.mark_processing(91)

    h.worker.run(91)

    download_calls = [kw for name, kw in fake_anime.calls if name == 'download']
    assert len(download_calls) == 1
    assert download_calls[0].get('custom_name') is None


# ---------------------------------------------------------------------------
# notify_event_send integration tests
# ---------------------------------------------------------------------------


class FakeNotifyEventSend:
    """Captures send_with_options(kwargs={...}) calls without touching Telegram."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, kwargs: dict[str, Any]) -> None:
        self.calls.append(kwargs)

    def events_by_type(self, event: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c.get('event') == event]


def _build_with_notify(
    tmp_path: pathlib.Path,
    *,
    fake_anime: FakeAnime,
    notify_send: FakeNotifyEventSend,
    settings_overrides: dict[str, Any] | None = None,
) -> Harness:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings_kwargs: dict[str, Any] = {
        'download_resolution': '1080',
        'classify_bangumi': True,
    }
    settings_kwargs.update(settings_overrides or {})
    settings = AppSettings(**settings_kwargs)

    queue = TaskQueue(max_download=2, max_upload=1)
    progress = ProgressBus()
    repo = FakeAnimeRepository()

    worker = DownloadWorker(
        queue=queue,
        anime_factory=lambda sn: fake_anime,  # type: ignore[arg-type]
        anime_repo=repo,  # type: ignore[arg-type]
        progress=progress,
        settings_provider=lambda: settings,
        logger=logger,
        notify_event_send=notify_send,
    )
    return Harness(
        worker=worker,
        queue=queue,
        progress=progress,
        repo=repo,
        settings=settings,
        anime=fake_anime,
    )


def test_notify_event_send_started_and_completed_on_success(tmp_path: pathlib.Path) -> None:
    """Happy path fires 'started' (after load) then 'completed'."""
    fake_anime = FakeAnime(
        sn=200,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
        bangumi_name='某番',
        episode='05',
    )
    notify = FakeNotifyEventSend()
    h = _build_with_notify(tmp_path, fake_anime=fake_anime, notify_send=notify)
    h.queue.add(200, TaskInfo(sn=200, tag='', mode='single', owner_id='user1'))
    h.queue.mark_processing(200)

    h.worker.run(200)

    started = notify.events_by_type('started')
    completed = notify.events_by_type('completed')
    assert len(started) == 1
    assert len(completed) == 1
    call = completed[0]
    assert call['owner_id'] == 'user1'
    assert call['bangumi_name'] == '某番'
    assert call['episode'] == '05'
    assert call['file_size_mb'] == 500


def test_notify_event_send_failed_on_no_available_stream_during_download(
    tmp_path: pathlib.Path,
) -> None:
    fake_anime = FakeAnime(
        sn=201,
        download_raises=exceptions.NoAvailableStreamError('vip'),
        bangumi_name='某番2',
        episode='01',
    )
    notify = FakeNotifyEventSend()
    h = _build_with_notify(tmp_path, fake_anime=fake_anime, notify_send=notify)
    h.queue.add(201, TaskInfo(sn=201, tag='', mode='single', owner_id='user2'))
    h.queue.mark_processing(201)

    h.worker.run(201)

    failed = notify.events_by_type('failed')
    assert len(failed) == 1
    assert failed[0]['owner_id'] == 'user2'
    assert notify.events_by_type('completed') == []
    assert notify.events_by_type('cancelled') == []


def test_notify_event_send_failed_on_no_available_stream_during_load(
    tmp_path: pathlib.Path,
) -> None:
    class _BrokenLoad(FakeAnime):
        def load(self) -> None:
            raise exceptions.NoAvailableStreamError('load fail')

    fake_anime = _BrokenLoad(sn=202)
    notify = FakeNotifyEventSend()
    h = _build_with_notify(tmp_path, fake_anime=fake_anime, notify_send=notify)
    h.queue.add(202, TaskInfo(sn=202, tag='', mode='single', owner_id='user3'))
    h.queue.mark_processing(202)

    h.worker.run(202)

    failed = notify.events_by_type('failed')
    assert len(failed) == 1
    # Load failure: sn is used as bangumi_name placeholder.
    assert failed[0]['sn'] == 202
    assert failed[0]['owner_id'] == 'user3'
    assert notify.events_by_type('completed') == []
    # No 'started' — load failed before we could fire it.
    assert notify.events_by_type('started') == []


def test_notify_event_send_cancelled_on_task_cancelled(tmp_path: pathlib.Path) -> None:
    fake_anime = FakeAnime(
        sn=203,
        download_raises=exceptions.TaskCancelledError('user cancel'),
        bangumi_name='某番3',
        episode='02',
    )
    notify = FakeNotifyEventSend()
    h = _build_with_notify(tmp_path, fake_anime=fake_anime, notify_send=notify)
    h.queue.add(203, TaskInfo(sn=203, tag='', mode='single', owner_id='user4'))
    h.queue.mark_processing(203)
    h.progress.start(203, 'pending', status='正在下載')

    h.worker.run(203)

    cancelled = notify.events_by_type('cancelled')
    assert len(cancelled) == 1
    assert cancelled[0]['owner_id'] == 'user4'
    assert cancelled[0]['bangumi_name'] == '某番3'
    assert notify.events_by_type('completed') == []
    assert notify.events_by_type('failed') == []


def test_notify_event_send_owner_id_none_passed_through(tmp_path: pathlib.Path) -> None:
    """TaskInfo with no owner_id → event kwargs receive owner_id=None."""
    fake_anime = FakeAnime(
        sn=204,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=200,
        ),
    )
    notify = FakeNotifyEventSend()
    h = _build_with_notify(tmp_path, fake_anime=fake_anime, notify_send=notify)
    h.queue.add(204, TaskInfo(sn=204, tag='', mode='single', owner_id=None))
    h.queue.mark_processing(204)

    h.worker.run(204)

    completed = notify.events_by_type('completed')
    assert len(completed) == 1
    assert completed[0]['owner_id'] is None


# ---------------------------------------------------------------------------
# Notifier-metadata kwargs (custom_name / season / episode_number)
# ---------------------------------------------------------------------------


class FakeAnimeListRepo:
    """Minimal AnimeListEntryRepository stand-in."""

    def __init__(self, entries: dict[tuple[str, int], Any]) -> None:
        # entries keyed by (user_id, sn)
        self._entries = entries

    def get_by_user_sn(self, user_id: str, sn: int) -> Any:
        return self._entries.get((user_id, sn))


def _build_with_notify_and_list_repo(
    tmp_path: pathlib.Path,
    *,
    fake_anime: FakeAnime,
    notify_send: FakeNotifyEventSend,
    list_repo: FakeAnimeListRepo,
    settings_overrides: dict[str, Any] | None = None,
) -> Harness:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings_kwargs: dict[str, Any] = {
        'download_resolution': '1080',
        'classify_bangumi': True,
    }
    settings_kwargs.update(settings_overrides or {})
    settings = AppSettings(**settings_kwargs)

    queue = TaskQueue(max_download=2, max_upload=1)
    progress = ProgressBus()
    repo = FakeAnimeRepository()

    worker = DownloadWorker(
        queue=queue,
        anime_factory=lambda sn: fake_anime,  # type: ignore[arg-type]
        anime_repo=repo,  # type: ignore[arg-type]
        progress=progress,
        settings_provider=lambda: settings,
        logger=logger,
        notify_event_send=notify_send,
        anime_list_repo=list_repo,  # type: ignore[arg-type]
    )
    return Harness(
        worker=worker,
        queue=queue,
        progress=progress,
        repo=repo,
        settings=settings,
        anime=fake_anime,
    )


@dataclasses.dataclass
class FakeAnimeListEntry:
    custom_name: str | None
    season: int


def test_notify_event_send_completed_with_list_entry(tmp_path: pathlib.Path) -> None:
    """Entry exists with custom_name + season → passed through to 'completed' event."""
    fake_anime = FakeAnime(
        sn=300,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
        bangumi_name='某番',
        episode='05',
    )
    entry = FakeAnimeListEntry(custom_name='我的名字', season=2)
    list_repo = FakeAnimeListRepo({('user1', 300): entry})
    notify = FakeNotifyEventSend()
    h = _build_with_notify_and_list_repo(tmp_path, fake_anime=fake_anime, notify_send=notify, list_repo=list_repo)
    h.queue.add(300, TaskInfo(sn=300, tag='', mode='single', owner_id='user1'))
    h.queue.mark_processing(300)

    h.worker.run(300)

    completed = notify.events_by_type('completed')
    assert len(completed) == 1
    call = completed[0]
    assert call['custom_name'] == '我的名字'
    assert call['season'] == 2
    assert call['episode_number'] == 5


def test_notify_event_send_completed_no_entry_manual_task(tmp_path: pathlib.Path) -> None:
    """No matching entry (manual task, owner_id=None) → custom_name=None, season=1."""
    fake_anime = FakeAnime(
        sn=301,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
        bangumi_name='某番',
        episode='03',
    )
    list_repo = FakeAnimeListRepo({})
    notify = FakeNotifyEventSend()
    h = _build_with_notify_and_list_repo(tmp_path, fake_anime=fake_anime, notify_send=notify, list_repo=list_repo)
    h.queue.add(301, TaskInfo(sn=301, tag='', mode='single', owner_id=None))
    h.queue.mark_processing(301)

    h.worker.run(301)

    completed = notify.events_by_type('completed')
    assert len(completed) == 1
    call = completed[0]
    assert call['custom_name'] is None
    assert call['season'] == 1
    assert call['episode_number'] == 3


def test_notify_event_send_completed_non_numeric_episode(tmp_path: pathlib.Path) -> None:
    """Non-numeric episode string with digit → episode_number is parsed from digit."""
    fake_anime = FakeAnime(
        sn=302,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
        bangumi_name='某番',
        episode='SP1',
    )
    list_repo = FakeAnimeListRepo({})
    notify = FakeNotifyEventSend()
    h = _build_with_notify_and_list_repo(tmp_path, fake_anime=fake_anime, notify_send=notify, list_repo=list_repo)
    h.queue.add(302, TaskInfo(sn=302, tag='', mode='single', owner_id='user1'))
    h.queue.mark_processing(302)

    h.worker.run(302)

    completed = notify.events_by_type('completed')
    assert len(completed) == 1
    call = completed[0]
    # re.search(r'\d+', 'SP1') finds '1'.
    assert call['episode_number'] == 1


def test_notify_event_send_completed_pure_non_numeric_episode(tmp_path: pathlib.Path) -> None:
    """Episode with no digits at all → episode_number=None."""
    fake_anime = FakeAnime(
        sn=303,
        download_result=DownloadResult(
            success=True,
            file_path=tmp_path / 'a.mp4',
            size_mb=500,
        ),
        bangumi_name='某番',
        episode='OVA',
    )
    list_repo = FakeAnimeListRepo({})
    notify = FakeNotifyEventSend()
    h = _build_with_notify_and_list_repo(tmp_path, fake_anime=fake_anime, notify_send=notify, list_repo=list_repo)
    h.queue.add(303, TaskInfo(sn=303, tag='', mode='single', owner_id='user1'))
    h.queue.mark_processing(303)

    h.worker.run(303)

    completed = notify.events_by_type('completed')
    assert len(completed) == 1
    call = completed[0]
    assert call['episode_number'] is None

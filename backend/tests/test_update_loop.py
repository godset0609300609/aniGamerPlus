"""Tests for :class:`UpdateLoop`."""

from __future__ import annotations

import dataclasses
import datetime
import pathlib
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.downloader import exceptions
from app.downloader.metadata import AnimeMetadata
from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.models import AppSettings
from app.persistence.anime_list_repo import AnimeListEntryDTO
from app.persistence.repositories import AnimeRow
from app.scheduler.queue_ import TaskQueue
from app.scheduler.update_loop import UpdateLoop


class _FakeSettingsRepo:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def load(self) -> AppSettings:
        return self._settings


class _FakeSnListRepo:
    def __init__(self, sn_dict: dict[int, dict[str, str]] | None = None) -> None:
        self._sn_dict = sn_dict or {}

    def parse_legacy(self, default_mode: str) -> dict[int, dict[str, str]]:
        return {sn: dict(info) for sn, info in self._sn_dict.items()}


class _FakeAnimeListEntryRepo:
    """Fake repository for the ``anime_list_entries`` table."""

    def __init__(self, entries: list[AnimeListEntryDTO] | None = None) -> None:
        self._entries = list(entries or [])
        self.anime_name_updates: list[tuple[int, str, str | None]] = []

    def list_all(self) -> list[AnimeListEntryDTO]:
        return list(self._entries)

    def update_anime_name(self, sn: int, user_id: str, anime_name: str | None) -> None:
        self.anime_name_updates.append((sn, user_id, anime_name))


class _FakeAnimeRepo:
    def __init__(self) -> None:
        self._rows: dict[int, AnimeRow] = {}

    def read(self, sn: int) -> AnimeRow | None:
        return self._rows.get(int(sn))

    def insert(self, **kwargs: Any) -> None:
        sn = int(kwargs['sn'])
        self._rows[sn] = AnimeRow(
            sn=sn,
            title=kwargs['title'],
            anime_name=kwargs['anime_name'],
            episode=kwargs['episode'],
            status=0,
            remote_status=0,
            resolution=kwargs['resolution'],
            file_size=kwargs['file_size'],
            local_file_path=kwargs.get('local_file_path'),
            created_time=datetime.datetime.now(),
        )

    def update(self, sn: int, **kwargs: Any) -> None:
        existing = self._rows.get(int(sn))
        if existing is None:
            return
        self._rows[int(sn)] = dataclasses.replace(existing, **kwargs)

    def set_row(self, sn: int, *, status: int = 1, remote_status: int = 0) -> None:
        self._rows[int(sn)] = AnimeRow(
            sn=int(sn),
            title='t',
            anime_name='a',
            episode='01',
            status=status,
            remote_status=remote_status,
            resolution=1080,
            file_size=100,
            local_file_path='/tmp/x.mp4',
            created_time=datetime.datetime.now(),
        )


class _FakeMetadataExtractor:
    def __init__(
        self,
        by_sn: dict[int, AnimeMetadata],
        *,
        raises: BaseException | None = None,
    ) -> None:
        self._by_sn = by_sn
        self._raises = raises
        self.calls: list[int] = []

    def fetch(self, sn: int) -> AnimeMetadata:
        self.calls.append(int(sn))
        if self._raises is not None:
            raise self._raises
        return self._by_sn[int(sn)]


class _FakeCookieRepo:
    def __init__(self) -> None:
        self.invalidate_calls = 0

    def invalidate(self) -> None:
        self.invalidate_calls += 1


class _FakeWorker:
    def __init__(self) -> None:
        self.runs: list[int] = []

    def run(self, sn: int, **_kwargs: Any) -> None:
        self.runs.append(int(sn))


def _meta(sn: int, *, episode_list: dict[str, int] | None = None) -> AnimeMetadata:
    episode_list = episode_list or {'01': sn}
    return AnimeMetadata(
        sn=sn,
        title='某某 [01]',
        bangumi_name='某某',
        bangumi_name_orig='某某',
        episode='01',
        episode_list=episode_list,
    )


class _FakeParseCooldown:
    """Records how many times ``wait`` was called, without actually sleeping."""

    def __init__(self) -> None:
        self.wait_calls: int = 0

    def wait(self, **_kwargs: Any) -> None:
        self.wait_calls += 1


def _build(
    tmp_path: pathlib.Path,
    *,
    settings_overrides: dict[str, Any] | None = None,
    sn_dict: dict[int, dict[str, str]] | None = None,
    anime_list_entries: list[AnimeListEntryDTO] | None = None,
    metadata_by_sn: dict[int, AnimeMetadata] | None = None,
    metadata_raises: BaseException | None = None,
    progress_bus: ProgressBus | None = None,
    parse_cooldown: _FakeParseCooldown | None = None,
) -> tuple[
    UpdateLoop,
    _FakeWorker,
    TaskQueue,
    _FakeAnimeRepo,
    _FakeCookieRepo,
    _FakeSnListRepo,
    _FakeMetadataExtractor,
    _FakeAnimeListEntryRepo,
]:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings_kwargs: dict[str, Any] = {'check_frequency': 1, 'download_resolution': '1080'}
    settings_kwargs.update(settings_overrides or {})
    settings = AppSettings(**settings_kwargs)

    settings_repo = _FakeSettingsRepo(settings)
    sn_list_repo = _FakeSnListRepo(sn_dict or {})
    anime_list_entry_repo = _FakeAnimeListEntryRepo(anime_list_entries or [])
    anime_repo = _FakeAnimeRepo()
    queue = TaskQueue(max_download=5, max_upload=1)
    worker = _FakeWorker()
    metadata = _FakeMetadataExtractor(
        metadata_by_sn or {},
        raises=metadata_raises,
    )
    cookie_repo = _FakeCookieRepo()

    loop = UpdateLoop(
        settings_repo=settings_repo,  # type: ignore[arg-type]
        sn_list_repo=sn_list_repo,  # type: ignore[arg-type]
        anime_list_entry_repo=anime_list_entry_repo,  # type: ignore[arg-type]
        anime_repo=anime_repo,  # type: ignore[arg-type]
        queue=queue,
        worker=worker,  # type: ignore[arg-type]
        metadata_extractor=metadata,  # type: ignore[arg-type]
        logger=logger,
        cookie_repo=cookie_repo,  # type: ignore[arg-type]
        progress_bus=progress_bus,
        parse_cooldown=parse_cooldown,  # type: ignore[arg-type]
    )
    return loop, worker, queue, anime_repo, cookie_repo, sn_list_repo, metadata, anime_list_entry_repo


# ------------------------------------------------------------------ existing tests


def test_check_tasks_enqueues_single_mode(tmp_path: pathlib.Path) -> None:
    loop, worker, queue, _repo, _cookies, _sn_list, _md, _al = _build(
        tmp_path,
        metadata_by_sn={42: _meta(42)},
    )
    # Give threads a moment to start by waiting below.
    loop.check_tasks({42: {'mode': 'single', 'tag': ''}})

    # Let the worker thread run.
    for _ in range(20):
        if worker.runs:
            break
        threading.Event().wait(0.05)

    assert queue.contains(42)
    assert worker.runs == [42]


def test_check_tasks_all_mode_enqueues_every_episode(tmp_path: pathlib.Path) -> None:
    # episode_list contains 3 sns; all should be enqueued on a fresh DB.
    meta = _meta(
        100,
        episode_list={'01': 100, '02': 101, '03': 102},
    )
    loop, worker, queue, _repo, _cookies, _sn_list, _md, _al = _build(
        tmp_path,
        metadata_by_sn={100: meta},
    )
    loop.check_tasks({100: {'mode': 'all', 'tag': ''}})

    for _ in range(30):
        if len(worker.runs) >= 3:
            break
        threading.Event().wait(0.05)

    assert queue.contains(100)
    assert queue.contains(101)
    assert queue.contains(102)
    assert sorted(worker.runs) == [100, 101, 102]


def test_check_tasks_latest_mode_skips_if_already_downloaded(
    tmp_path: pathlib.Path,
) -> None:
    meta = _meta(
        200,
        episode_list={'01': 200, '02': 201},
    )
    loop, worker, queue, repo, _cookies, _sn_list, _md, _al = _build(
        tmp_path,
        metadata_by_sn={200: meta},
    )
    # Pre-populate the DB: the 'latest' (last inserted) is sn=201.
    repo.set_row(201, status=1, remote_status=0)

    loop.check_tasks({200: {'mode': 'latest', 'tag': ''}})

    threading.Event().wait(0.1)
    assert not queue.contains(201)
    assert worker.runs == []


def test_invalid_cookie_triggers_invalidate(tmp_path: pathlib.Path) -> None:
    loop, worker, queue, _repo, cookies, _sn_list, _md, _al = _build(
        tmp_path,
        metadata_raises=exceptions.InvalidCookieError('revoked'),
    )
    # The loop should call cookie_repo.invalidate and continue (no raise).
    loop.check_tasks({999: {'mode': 'single', 'tag': ''}})
    assert cookies.invalidate_calls == 1
    assert worker.runs == []
    assert not queue.contains(999)


def test_run_forever_honours_check_frequency(tmp_path: pathlib.Path) -> None:
    """``check_frequency`` minutes → ticks via monkeypatched sleep."""
    loop, _worker, _queue, _repo, _cookies, _sn_list, _md, _al = _build(
        tmp_path,
        settings_overrides={'check_frequency': 2},
    )
    ticks: list[float] = []

    def fake_sleep(seconds: float) -> None:
        ticks.append(seconds)
        if len(ticks) >= 3:
            loop.stop()

    loop._set_sleep(fake_sleep)
    loop.run_forever()
    # Three 1-second ticks before stop.
    assert ticks == [1.0, 1.0, 1.0]


def test_check_tasks_announces_waiting_status(tmp_path: pathlib.Path) -> None:
    """When a task is enqueued, the progress bus should immediately show
    ``'等待下載'`` so queued-but-not-started tasks are visible on the monitor.

    We inject a worker whose ``run`` is a no-op so we can inspect the bus
    state BEFORE any worker thread would mutate it.
    """

    class _NoopWorker:
        def run(self, sn: int, **_kwargs: Any) -> None:
            # Deliberately do nothing — we want to observe the ``'等待下載'``
            # entry as it stands immediately after queue-ingress.
            return None

    bus = ProgressBus()
    loop, _worker, queue, _repo, _cookies, _sn_list, _md, _al = _build(
        tmp_path,
        metadata_by_sn={321: _meta(321)},
        progress_bus=bus,
    )
    # Replace the fake worker with our no-op worker.
    loop._worker = _NoopWorker()  # type: ignore[assignment]

    loop.check_tasks({321: {'mode': 'single', 'tag': ''}})

    assert queue.contains(321)
    snap = bus.snapshot()
    assert 321 in snap
    assert snap[321].status == '等待下載'
    assert '《' in snap[321].filename and '》' in snap[321].filename


def test_check_tasks_without_progress_bus_still_works(
    tmp_path: pathlib.Path,
) -> None:
    """Backward-compat: callers that haven't wired a progress bus still
    enqueue work without error (the announcement is skipped)."""
    loop, worker, queue, _repo, _cookies, _sn_list, _md, _al = _build(
        tmp_path,
        metadata_by_sn={322: _meta(322)},
        # progress_bus left as None.
    )
    loop.check_tasks({322: {'mode': 'single', 'tag': ''}})
    assert queue.contains(322)


# ------------------------------------------------------------------ DB source tests


def test_update_loop_reads_from_anime_list_db(tmp_path: pathlib.Path) -> None:
    """_load_sn_dict_from_db returns exactly the 2 enabled entries, skipping
    the disabled one."""
    entries = [
        AnimeListEntryDTO(sn=10, enabled=True, mode='latest', tag='tag1', season=2, user_id='u1'),
        AnimeListEntryDTO(sn=20, enabled=True, mode='all', tag='tag2', season=1, user_id='u2'),
        AnimeListEntryDTO(sn=30, enabled=False, mode='single', tag='', user_id='u3'),
    ]
    loop, _w, _q, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        anime_list_entries=entries,
    )
    result = loop._load_sn_dict_from_db('latest')

    assert set(result.keys()) == {10, 20}
    assert result[10]['mode'] == 'latest'
    assert result[10]['season'] == '2'
    assert result[10]['tag'] == 'tag1'
    assert result[20]['mode'] == 'all'
    assert result[20]['season'] == '1'


def test_update_loop_skips_disabled_entries(tmp_path: pathlib.Path) -> None:
    """Disabled entries must not appear in sn_dict."""
    entries = [
        AnimeListEntryDTO(sn=99, enabled=False, mode='all', tag='', user_id='u1'),
    ]
    loop, _w, _q, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        anime_list_entries=entries,
    )
    result = loop._load_sn_dict_from_db('latest')
    assert result == {}


def test_update_loop_owner_id_propagates(tmp_path: pathlib.Path) -> None:
    """owner_id from DB row must reach _announce_waiting → ProgressBus.start."""

    class _NoopWorker:
        def run(self, sn: int, **_kwargs: Any) -> None:
            return None

    bus = ProgressBus()
    entries = [
        AnimeListEntryDTO(sn=55, enabled=True, mode='single', tag='', user_id='user123'),
    ]
    loop, _w, queue, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        anime_list_entries=entries,
        metadata_by_sn={55: _meta(55)},
        progress_bus=bus,
    )
    loop._worker = _NoopWorker()  # type: ignore[assignment]

    sn_dict = loop._load_sn_dict_from_db('latest')
    loop.check_tasks(sn_dict)

    snap = bus.snapshot()
    assert 55 in snap
    assert snap[55].owner_id == 'user123'


def test_update_loop_warns_when_db_empty_and_legacy_file_exists(
    tmp_path: pathlib.Path,
) -> None:
    """When DB is empty but legacy sn_list.txt has entries, a one-time error
    log is emitted suggesting migration."""
    # Seed the fake sn_list repo with 3 legacy entries.
    legacy_entries = {
        10: {'mode': 'latest', 'tag': ''},
        20: {'mode': 'all', 'tag': ''},
        30: {'mode': 'single', 'tag': ''},
    }
    # No DB entries (anime_list_entries=[]).
    loop, _w, _q, _r, _c, sn_list, _md, _al = _build(
        tmp_path,
        sn_dict=legacy_entries,
        anime_list_entries=[],
    )

    # Collect log messages via the real logger (spy approach).
    logged_errors: list[str] = []
    original_error = loop._logger.error

    def capturing_error(sn: Any, action: str, msg: str, **kwargs: Any) -> None:
        logged_errors.append(msg)
        original_error(sn, action, msg, **kwargs)

    loop._logger.error = capturing_error  # type: ignore[method-assign]

    # First call — should emit warning.
    loop._load_sn_dict_from_db('latest')
    assert any('sn_list.txt' in m and '3' in m for m in logged_errors), (
        f'Expected legacy migration warning, got: {logged_errors}'
    )
    count_after_first = len(logged_errors)

    # Second call — warning must NOT fire again.
    loop._load_sn_dict_from_db('latest')
    assert len(logged_errors) == count_after_first, 'Warning should be emitted only once per scheduler boot'


def test_announce_waiting_passes_episode_from_episode_list(
    tmp_path: pathlib.Path,
) -> None:
    """_announce_waiting must receive the target sn's episode label, not the
    root sn's episode.  With episode_list={"1": 100, "2": 200, "3": 300} and
    mode=all, progress.start must be called with episode="1" for sn=100,
    episode="2" for sn=200, and episode="3" for sn=300.
    """

    class _NoopWorker:
        def run(self, sn: int, **_kwargs: Any) -> None:
            return None

    bus = ProgressBus()
    meta = AnimeMetadata(
        sn=300,
        title='テスト [3]',
        bangumi_name='テスト',
        bangumi_name_orig='テスト',
        episode='3',
        episode_list={'1': 100, '2': 200, '3': 300},
    )
    loop, _w, _queue, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        metadata_by_sn={300: meta},
        progress_bus=bus,
    )
    loop._worker = _NoopWorker()  # type: ignore[assignment]

    loop.check_tasks({300: {'mode': 'all', 'tag': ''}})

    snap = bus.snapshot()
    assert 100 in snap, 'sn 100 should be in progress bus'
    assert 200 in snap, 'sn 200 should be in progress bus'
    assert 300 in snap, 'sn 300 should be in progress bus'
    assert snap[100].episode == '1', f"expected '1', got {snap[100].episode!r}"
    assert snap[200].episode == '2', f"expected '2', got {snap[200].episode!r}"
    assert snap[300].episode == '3', f"expected '3', got {snap[300].episode!r}"


def test_check_tasks_caches_anime_name_on_entry(tmp_path: pathlib.Path) -> None:
    """After metadata is fetched, update_anime_name is called on the entry repo
    so the UI can display the series title before any download completes."""
    entries = [
        AnimeListEntryDTO(sn=77, enabled=True, mode='single', tag='', user_id='user_abc'),
    ]
    loop, _w, _q, _r, _c, _sn, _md, al_repo = _build(
        tmp_path,
        anime_list_entries=entries,
        metadata_by_sn={77: _meta(77)},
    )

    sn_dict = loop._load_sn_dict_from_db('latest')
    loop.check_tasks(sn_dict)

    # update_anime_name must have been called with the bangumi name.
    assert len(al_repo.anime_name_updates) == 1
    sn_arg, uid_arg, name_arg = al_repo.anime_name_updates[0]
    assert sn_arg == 77
    assert uid_arg == 'user_abc'
    assert name_arg == '某某'


def test_sn_dict_includes_custom_name(tmp_path: pathlib.Path) -> None:
    """_load_sn_dict_from_db includes custom_name in the per-sn dict."""
    entries = [
        AnimeListEntryDTO(sn=88, enabled=True, mode='single', tag='', user_id='u1', custom_name='自訂名稱'),
    ]
    loop, _w, _q, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        anime_list_entries=entries,
    )
    result = loop._load_sn_dict_from_db('latest')

    assert 88 in result
    assert result[88]['custom_name'] == '自訂名稱'


def test_sn_dict_custom_name_empty_when_not_set(tmp_path: pathlib.Path) -> None:
    """_load_sn_dict_from_db sets custom_name to '' when the field is None."""
    entries = [
        AnimeListEntryDTO(sn=89, enabled=True, mode='single', tag='', user_id='u1', custom_name=None),
    ]
    loop, _w, _q, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        anime_list_entries=entries,
    )
    result = loop._load_sn_dict_from_db('latest')

    assert result[89]['custom_name'] == ''


def test_make_task_info_carries_custom_name(tmp_path: pathlib.Path) -> None:
    """_make_task_info reads custom_name out of info dict and puts it on TaskInfo."""
    loop, _w, _q, _r, _c, _sn, _md, _al = _build(tmp_path)
    info: dict[str, str] = {'mode': 'single', 'tag': '', 'season': '1', 'custom_name': '覆蓋名稱'}
    task = loop._make_task_info(50, info, 'single')

    assert task.custom_name == '覆蓋名稱'


def test_make_task_info_custom_name_none_when_empty(tmp_path: pathlib.Path) -> None:
    """_make_task_info maps empty-string custom_name to None on TaskInfo."""
    loop, _w, _q, _r, _c, _sn, _md, _al = _build(tmp_path)
    info: dict[str, str] = {'mode': 'single', 'tag': '', 'season': '1', 'custom_name': ''}
    task = loop._make_task_info(51, info, 'single')

    assert task.custom_name is None


# ------------------------------------------------------------------ parse cooldown tests


def test_check_tasks_parse_cooldown_called_n_minus_1_times(tmp_path: pathlib.Path) -> None:
    """With N sns, parse_cooldown.wait must be called N-1 times (skip last)."""
    cd = _FakeParseCooldown()
    meta_by_sn = {
        10: _meta(10),
        20: _meta(20),
        30: _meta(30),
    }
    loop, _w, _q, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        metadata_by_sn=meta_by_sn,
        parse_cooldown=cd,
    )
    loop.check_tasks({10: {'mode': 'single', 'tag': ''}, 20: {'mode': 'single', 'tag': ''}, 30: {'mode': 'single', 'tag': ''}})

    assert cd.wait_calls == 2, f'Expected 2 cooldown waits for 3 sns, got {cd.wait_calls}'


def test_check_tasks_no_cooldown_for_single_item(tmp_path: pathlib.Path) -> None:
    """With exactly 1 sn, parse_cooldown.wait must NOT be called."""
    cd = _FakeParseCooldown()
    loop, _w, _q, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        metadata_by_sn={42: _meta(42)},
        parse_cooldown=cd,
    )
    loop.check_tasks({42: {'mode': 'single', 'tag': ''}})

    assert cd.wait_calls == 0, f'Expected 0 cooldown waits for 1 sn, got {cd.wait_calls}'


def test_check_tasks_logs_name_before_fetch(tmp_path: pathlib.Path) -> None:
    """「更新資訊 正在檢查…」log must be emitted BEFORE the metadata fetch."""
    # Seed the entry repo with a cached name so the log shows it.
    entries = [
        AnimeListEntryDTO(sn=77, enabled=True, mode='single', tag='', user_id='u1', anime_name='テストアニメ'),
    ]
    log_events: list[tuple[str, str, str]] = []  # (tag, detail, phase)
    fetch_calls: list[int] = []

    class _TrackedExtractor:
        def fetch(self, sn: int) -> AnimeMetadata:
            fetch_calls.append(sn)
            return _meta(sn)

    loop, _w, _q, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        anime_list_entries=entries,
        metadata_by_sn={77: _meta(77)},
    )
    # Replace the metadata extractor with the tracking one.
    loop._metadata_extractor = _TrackedExtractor()  # type: ignore[assignment]

    original_info = loop._logger.info

    def capturing_info(sn: Any, tag: str, detail: str, **kwargs: Any) -> None:
        # Record fetch state at time of this log call.
        log_events.append((tag, detail, 'before_fetch' if not fetch_calls else 'after_fetch'))
        original_info(sn, tag, detail, **kwargs)

    loop._logger.info = capturing_info  # type: ignore[method-assign]

    loop.check_tasks({77: {'mode': 'single', 'tag': ''}})

    # Find the 「正在檢查」log entry.
    checking_events = [(tag, detail, phase) for tag, detail, phase in log_events if '正在檢查' in detail]
    assert checking_events, 'Expected a 正在檢查 log entry'
    assert checking_events[0][2] == 'before_fetch', (
        f'正在檢查 log was emitted {checking_events[0][2]}, expected before_fetch'
    )
    assert 'テストアニメ' in checking_events[0][1], (
        f'Expected cached name in log, got: {checking_events[0][1]!r}'
    )


# ------------------------------------------------------------------ summary log tests


def test_check_tasks_emits_summary_log(tmp_path: pathlib.Path) -> None:
    """After check_tasks: 3 sns, 2 newly added (1 already in queue), summary
    line must show '添加了 2 個新任務' and the correct total queue size."""
    meta_by_sn = {
        10: _meta(10),
        20: _meta(20),
        30: _meta(30),
    }
    loop, _w, queue, repo, _c, _sn, _md, _al = _build(
        tmp_path,
        metadata_by_sn=meta_by_sn,
    )
    # Pre-add sn=10 to the queue so it won't be counted as newly added.
    from app.scheduler.queue_ import TaskInfo

    queue.add(10, TaskInfo(sn=10, tag='', mode='single'))

    # Capture info log calls.
    info_messages: list[str] = []
    original_info = loop._logger.info

    def capturing_info(sn: Any, tag: str, detail: str, **kwargs: Any) -> None:
        info_messages.append(detail)
        original_info(sn, tag, detail, **kwargs)

    loop._logger.info = capturing_info  # type: ignore[method-assign]

    loop.check_tasks({
        10: {'mode': 'single', 'tag': ''},
        20: {'mode': 'single', 'tag': ''},
        30: {'mode': 'single', 'tag': ''},
    })

    summary_lines = [m for m in info_messages if '本次更新添加了' in m]
    assert summary_lines, f'Expected summary log line, got messages: {info_messages}'
    summary = summary_lines[0]
    assert '添加了 2 個新任務' in summary, f'Expected 2 newly added, got: {summary!r}'
    total = queue.size()
    assert f'共有 {total} 個任務' in summary, f'Unexpected queue total in: {summary!r}'


def test_check_tasks_summary_when_no_new_tasks(tmp_path: pathlib.Path) -> None:
    """When all target sns are already in queue, summary shows '添加了 0 個新任務'."""
    meta = _meta(42)
    loop, _w, queue, repo, _c, _sn, _md, _al = _build(
        tmp_path,
        metadata_by_sn={42: meta},
    )
    # Pre-add the sn so it is already in queue.
    from app.scheduler.queue_ import TaskInfo

    queue.add(42, TaskInfo(sn=42, tag='', mode='single'))

    info_messages: list[str] = []
    original_info = loop._logger.info

    def capturing_info(sn: Any, tag: str, detail: str, **kwargs: Any) -> None:
        info_messages.append(detail)
        original_info(sn, tag, detail, **kwargs)

    loop._logger.info = capturing_info  # type: ignore[method-assign]

    loop.check_tasks({42: {'mode': 'single', 'tag': ''}})

    summary_lines = [m for m in info_messages if '本次更新添加了' in m]
    assert summary_lines, f'Expected summary log line, got messages: {info_messages}'
    assert '添加了 0 個新任務' in summary_lines[0], f'Expected 0 newly added, got: {summary_lines[0]!r}'


# ------------------------------------------------------------------ watchdog beat tests


class _FakeWatchdog:
    """Records every beat() call without any side effects."""

    def __init__(self) -> None:
        self.beat_calls: int = 0

    def beat(self) -> None:
        self.beat_calls += 1


def test_check_tasks_beats_watchdog_per_item(tmp_path: pathlib.Path) -> None:
    """With 3 sns, check_tasks must call watchdog.beat() at least 3 times
    (once at scan-start + once after each per-sn iteration)."""
    watchdog = _FakeWatchdog()
    meta_by_sn = {
        10: _meta(10),
        20: _meta(20),
        30: _meta(30),
    }
    loop, _w, _q, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        metadata_by_sn=meta_by_sn,
    )
    loop._watchdog = watchdog  # type: ignore[assignment]

    loop.check_tasks({
        10: {'mode': 'single', 'tag': ''},
        20: {'mode': 'single', 'tag': ''},
        30: {'mode': 'single', 'tag': ''},
    })

    # 1 beat at scan-start + 3 beats after each item = 4 total; require >= 3.
    assert watchdog.beat_calls >= 3, (
        f'Expected at least 3 watchdog beats for 3 sns, got {watchdog.beat_calls}'
    )


def test_check_tasks_beats_watchdog_single_item(tmp_path: pathlib.Path) -> None:
    """Even with a single sn, watchdog.beat() fires (scan-start beat)."""
    watchdog = _FakeWatchdog()
    loop, _w, _q, _r, _c, _sn, _md, _al = _build(
        tmp_path,
        metadata_by_sn={42: _meta(42)},
    )
    loop._watchdog = watchdog  # type: ignore[assignment]

    loop.check_tasks({42: {'mode': 'single', 'tag': ''}})

    assert watchdog.beat_calls >= 1, (
        f'Expected at least 1 watchdog beat, got {watchdog.beat_calls}'
    )

"""Tests for BilibiliRunner multi-part download logic."""

from __future__ import annotations

import contextlib
import pathlib
import threading
import typing as T
import unittest.mock

import pytest
import yt_dlp.utils

from app.downloader.bilibili.runner import BilibiliRunner
from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.persistence.paths import WorkspacePaths


@pytest.fixture(autouse=True)
def _stub_ffmpeg_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``resolve_ffmpeg_path`` so these multi-part tests don't depend on
    whether a real ffmpeg binary happens to be on the machine running the
    suite. ``backend/ffmpeg.exe`` is gitignored (present on a local Windows
    dev checkout, absent on a fresh Linux CI checkout), so without this the
    runner would silently take its "ffmpeg missing" early-fail branch on CI
    instead of exercising the multi-part logic under test — see
    ``test_bilibili_runner_ffmpeg.py`` for the dedicated ffmpeg-missing tests.
    """
    monkeypatch.setattr('app.downloader.bilibili.runner.resolve_ffmpeg_path', lambda: '/fake/ffmpeg')


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTaskIdMapRepo:
    """Tracks allocate() calls; returns a deterministic sn per external_id."""

    def __init__(self) -> None:
        self._map: dict[str, int] = {}
        self._counter = 100
        self.allocate_calls: list[dict[str, str]] = []

    def allocate(self, *, source: str, external_id: str) -> int:
        self.allocate_calls.append({'source': source, 'external_id': external_id})
        if external_id not in self._map:
            self._counter += 1
            self._map[external_id] = self._counter
        return self._map[external_id]


class FakeYtdlpDownloader:
    def __init__(
        self,
        info: dict[str, T.Any] | None = None,
        raise_on_part: int | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._info = info or {'title': 'Test Video', 'entries': None}
        self._raise_on_part = raise_on_part
        self._raise_exc = raise_exc or yt_dlp.utils.DownloadCancelled()
        self.extract_calls: list[str] = []
        self.download_calls: list[dict[str, T.Any]] = []

    def extract_info(self, bvid: str) -> dict[str, T.Any]:
        self.extract_calls.append(bvid)
        return self._info

    def download(
        self,
        task_sn: int,
        bvid: str,
        *,
        resolution: str,
        classify: bool,
        part_idx: int | None = None,
        parent_sn: int | None = None,
    ) -> dict[str, T.Any]:
        self.download_calls.append(
            {
                'task_sn': task_sn,
                'bvid': bvid,
                'resolution': resolution,
                'part_idx': part_idx,
                'parent_sn': parent_sn,
            }
        )
        if self._raise_on_part is not None and part_idx == self._raise_on_part:
            raise self._raise_exc
        return self._info


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture
def progress_bus() -> ProgressBus:
    return ProgressBus()


def _make_settings() -> T.Any:
    from app.models import AppSettings

    return AppSettings()


def _make_runner(
    downloader: FakeYtdlpDownloader,
    progress_bus: ProgressBus,
    logger: Logger,
    *,
    notify_send: T.Any = None,
    task_id_map_repo: T.Any = None,
) -> BilibiliRunner:
    return BilibiliRunner(
        ytdlp_downloader=downloader,
        progress_bus=progress_bus,
        logger=logger,
        settings=_make_settings(),
        notify_event_send=notify_send,
        task_id_map_repo=task_id_map_repo,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_runner_multipart_allocates_one_child_sn_per_part(progress_bus: ProgressBus, logger: Logger) -> None:
    """allocate() is called once per part with the expected external_ids."""
    info = {
        'title': 'BV1aBsaeeE8W Title',
        'entries': [{}, {}, {}],
    }
    dl = FakeYtdlpDownloader(info=info)
    repo = FakeTaskIdMapRepo()
    runner = _make_runner(dl, progress_bus, logger, task_id_map_repo=repo)
    runner.run(1, bvid='BV1aBsaeeE8W', resolution='1080', classify=True)

    assert len(repo.allocate_calls) == 3
    external_ids = [c['external_id'] for c in repo.allocate_calls]
    assert external_ids == ['BV1aBsaeeE8W_p1', 'BV1aBsaeeE8W_p2', 'BV1aBsaeeE8W_p3']
    sources = [c['source'] for c in repo.allocate_calls]
    assert all(s == 'bilibili' for s in sources)


def test_runner_multipart_announces_all_children_as_waiting_first(progress_bus: ProgressBus, logger: Logger) -> None:
    """All N progress_bus.start() calls happen before the first download."""
    info = {
        'title': 'Multi Title',
        'entries': [{}, {}, {}],
    }
    call_log: list[str] = []

    class TrackingDownloader(FakeYtdlpDownloader):
        def download(self, task_sn: int, bvid: str, **kwargs: T.Any) -> dict[str, T.Any]:  # type: ignore[override]
            call_log.append(f'download:{kwargs.get("part_idx")}')
            return self._info

    dl = TrackingDownloader(info=info)

    original_start = progress_bus.start

    def tracking_start(sn: int, filename: str, **kwargs: T.Any) -> None:
        call_log.append(f'start:{sn}')
        original_start(sn, filename, **kwargs)

    progress_bus.start = tracking_start  # type: ignore[method-assign]

    runner = _make_runner(dl, progress_bus, logger)
    runner.run(2, bvid='BV_multi', resolution='1080', classify=True)

    # All start() calls must appear before any download() call.
    first_download_pos = next((i for i, e in enumerate(call_log) if e.startswith('download:')), len(call_log))
    start_positions = [i for i, e in enumerate(call_log) if e.startswith('start:')]
    assert len(start_positions) == 3
    assert all(pos < first_download_pos for pos in start_positions), (
        f'Not all start()s before first download. call_log={call_log}'
    )


def test_runner_multipart_downloads_all_parts(progress_bus: ProgressBus, logger: Logger) -> None:
    """download() is called N times, once per part (parallel order not guaranteed)."""
    info = {
        'title': 'Seq Title',
        'entries': [{}, {}, {}],
    }
    dl = FakeYtdlpDownloader(info=info)
    runner = _make_runner(dl, progress_bus, logger)
    runner.run(3, bvid='BV_seq', resolution='1080', classify=True)

    assert len(dl.download_calls) == 3
    part_indices = sorted(c['part_idx'] for c in dl.download_calls)
    assert part_indices == [1, 2, 3]


def test_runner_multipart_parent_sn_has_no_progress_entry(progress_bus: ProgressBus, logger: Logger) -> None:
    """In multi-part mode, progress_bus.start(parent_sn, ...) is NOT called."""
    info = {
        'title': 'No Parent Card',
        'entries': [{}, {}],
    }
    dl = FakeYtdlpDownloader(info=info)

    start_calls: list[int] = []
    original_start = progress_bus.start

    def tracking_start(sn: int, filename: str, **kwargs: T.Any) -> None:
        start_calls.append(sn)
        original_start(sn, filename, **kwargs)

    progress_bus.start = tracking_start  # type: ignore[method-assign]

    parent_sn = 4
    runner = _make_runner(dl, progress_bus, logger)
    runner.run(parent_sn, bvid='BV_noparent', resolution='1080', classify=True)

    assert parent_sn not in start_calls, f'parent_sn={parent_sn} should not appear in start_calls={start_calls}'
    snap = progress_bus.snapshot()
    assert parent_sn not in snap


def test_runner_multipart_cancel_part_raises_download_cancelled(progress_bus: ProgressBus, logger: Logger) -> None:
    """When a part raises DownloadCancelled the runner swallows it and continues.

    In parallel mode all parts are submitted; a single part cancelling must not
    prevent the others from completing.  After the executor drains, no child
    should be stuck in '等待下載' or '正在下載'.
    """
    info = {
        'title': 'Cancel Title',
        'entries': [{}, {}, {}],
    }
    parent_sn = 5

    import threading as _threading

    cancel_event = _threading.Event()

    class CancelOnPart2Downloader(FakeYtdlpDownloader):
        def download(  # type: ignore[override]
            self,
            task_sn: int,
            bvid: str,
            *,
            resolution: str,
            classify: bool,
            part_idx: int | None = None,
            parent_sn: int | None = None,
        ) -> dict[str, T.Any]:
            self.download_calls.append({'task_sn': task_sn, 'part_idx': part_idx})
            if part_idx == 2 and parent_sn is not None:
                ev = progress_bus.get_cancel_event(parent_sn)
                if ev is not None and ev.is_set():
                    raise yt_dlp.utils.DownloadCancelled()
            return self._info

    dl = CancelOnPart2Downloader(info=info)

    from app.downloader.progress import TaskProgress

    fake_parent_entry = TaskProgress(
        sn=parent_sn,
        rate=0.0,
        status='下載中',
        filename='fake_parent',
    )
    fake_parent_entry._cancel_event = cancel_event
    progress_bus._entries[parent_sn] = fake_parent_entry  # type: ignore[attr-defined]

    cancel_event.set()

    runner = _make_runner(dl, progress_bus, logger)
    runner.run(parent_sn, bvid='BV_cancel', resolution='1080', classify=True)

    snap = progress_bus.snapshot()
    for child_sn, entry in snap.items():
        if child_sn == parent_sn:
            continue
        assert entry.status not in ('等待下載', '正在下載'), (
            f'child_sn={child_sn} still in in-progress state {entry.status!r}'
        )


def test_runner_singlepart_unchanged_behavior(progress_bus: ProgressBus, logger: Logger) -> None:
    """When entries is absent or len==1, parent_sn is used (original path)."""
    for info, label in [
        ({'title': 'Single No Entries'}, 'no entries key'),
        ({'title': 'Single One Entry', 'entries': [{}]}, 'one entry'),
        ({'title': 'Single None Entries', 'entries': None}, 'entries=None'),
    ]:
        pb = ProgressBus()
        dl = FakeYtdlpDownloader(info=info)
        runner = _make_runner(dl, pb, logger)
        runner.run(10, bvid='BV_single', resolution='1080', classify=True)

        snap = pb.snapshot()
        assert 10 in snap, f'parent_sn must have a progress entry for: {label}'
        assert snap[10].finished_at is not None, f'task must be finished for: {label}'
        assert len(dl.download_calls) == 1, f'exactly one download for: {label}'
        assert dl.download_calls[0]['part_idx'] is None, f'part_idx must be None for: {label}'


def test_ytdlp_downloader_accepts_part_idx(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """playlist_items=str(idx) is added to yt-dlp opts when part_idx is set."""
    from app.downloader.bilibili.ytdlp_downloader import YtdlpDownloader
    from app.downloader.progress import ProgressBus
    from app.persistence.bilibili_cookie_repo import BilibiliCookieRepository

    log = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    bus = ProgressBus()
    cookie_repo = BilibiliCookieRepository(WorkspacePaths.detect(working_dir=tmp_path))

    dl = YtdlpDownloader(
        progress_bus=bus,
        cookie_repo=cookie_repo,
        bangumi_dir=tmp_path,
        logger=log,
        ffmpeg_location=None,
    )

    captured_opts: dict[str, T.Any] = {}

    class FakeYDL:
        def __init__(self, opts: dict[str, T.Any]) -> None:
            captured_opts.update(opts)

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *_: T.Any) -> None:
            pass

        def extract_info(self, url: str, *, download: bool) -> dict[str, T.Any]:
            return {}

    monkeypatch.setattr('app.downloader.bilibili.ytdlp_downloader.yt_dlp.YoutubeDL', FakeYDL)

    dl.download(99, 'BV1test', resolution='1080', classify=False, part_idx=2)

    assert captured_opts.get('playlist_items') == '2'


def test_ytdlp_downloader_no_playlist_items_when_part_idx_none(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """playlist_items key is absent when part_idx is not given."""
    from app.downloader.bilibili.ytdlp_downloader import YtdlpDownloader
    from app.downloader.progress import ProgressBus
    from app.persistence.bilibili_cookie_repo import BilibiliCookieRepository

    log = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    bus = ProgressBus()
    cookie_repo = BilibiliCookieRepository(WorkspacePaths.detect(working_dir=tmp_path))

    dl = YtdlpDownloader(
        progress_bus=bus,
        cookie_repo=cookie_repo,
        bangumi_dir=tmp_path,
        logger=log,
        ffmpeg_location=None,
    )

    captured_opts: dict[str, T.Any] = {}

    class FakeYDL:
        def __init__(self, opts: dict[str, T.Any]) -> None:
            captured_opts.update(opts)

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *_: T.Any) -> None:
            pass

        def extract_info(self, url: str, *, download: bool) -> dict[str, T.Any]:
            return {}

    monkeypatch.setattr('app.downloader.bilibili.ytdlp_downloader.yt_dlp.YoutubeDL', FakeYDL)

    dl.download(99, 'BV1test', resolution='1080', classify=False)

    assert 'playlist_items' not in captured_opts


def test_ytdlp_downloader_parent_sn_cancel_raises(
    tmp_path: pathlib.Path,
) -> None:
    """progress_hook raises DownloadCancelled when parent_sn cancel_event is set.

    The hook is extracted from the opts passed to YoutubeDL and invoked
    directly to simulate yt-dlp firing a 'downloading' progress event.
    """
    from app.downloader.bilibili.ytdlp_downloader import YtdlpDownloader
    from app.downloader.progress import ProgressBus, TaskProgress
    from app.persistence.bilibili_cookie_repo import BilibiliCookieRepository

    log = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    bus = ProgressBus()
    cookie_repo = BilibiliCookieRepository(WorkspacePaths.detect(working_dir=tmp_path))

    dl = YtdlpDownloader(
        progress_bus=bus,
        cookie_repo=cookie_repo,
        bangumi_dir=tmp_path,
        logger=log,
    )

    parent_sn = 500
    child_sn = 501

    # Register a child entry so the hook can look it up.
    bus.start(child_sn, 'child_file', status='下載中')

    # Register a fake parent entry with a pre-set cancel_event.
    cancel_event = threading.Event()
    parent_entry = TaskProgress(
        sn=parent_sn,
        rate=0.0,
        status='下載中',
        filename='parent',
    )
    parent_entry._cancel_event = cancel_event
    bus._entries[parent_sn] = parent_entry  # type: ignore[attr-defined]
    cancel_event.set()

    # Capture the progress_hooks list from opts, then invoke the hook manually.
    hook_ref: list[T.Any] = []

    class FakeYDL:
        def __init__(self, opts: dict[str, T.Any]) -> None:
            for h in opts.get('progress_hooks', []):
                hook_ref.append(h)

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *_: T.Any) -> None:
            pass

        def extract_info(self, url: str, *, download: bool) -> dict[str, T.Any]:
            # Call the hook here to simulate yt-dlp firing a progress event.
            for h in hook_ref:
                h({'status': 'downloading', 'downloaded_bytes': 0})
            return {}

    with (
        unittest.mock.patch('app.downloader.bilibili.ytdlp_downloader.yt_dlp.YoutubeDL', FakeYDL),
        pytest.raises(yt_dlp.utils.DownloadCancelled),
    ):
        dl.download(
            child_sn,
            'BV_parentcancel',
            resolution='1080',
            classify=False,
            part_idx=1,
            parent_sn=parent_sn,
        )


# ---------------------------------------------------------------------------
# bilibili_concurrent_parts — concurrency behaviour tests
# ---------------------------------------------------------------------------


def test_multipart_runs_parts_concurrently_up_to_limit(progress_bus: ProgressBus, logger: Logger) -> None:
    """With bilibili_concurrent_parts=2 and 3 parts, two windows must overlap."""
    import threading as _threading
    import time

    n = 3
    info = {
        'title': 'Concurrent Title',
        'entries': [{} for _ in range(n)],
    }

    start_times: dict[int, float] = {}
    end_times: dict[int, float] = {}
    gate = _threading.Barrier(2)

    class TimingDownloader(FakeYtdlpDownloader):
        def download(  # type: ignore[override]
            self,
            task_sn: int,
            bvid: str,
            *,
            resolution: str,
            classify: bool,
            part_idx: int | None = None,
            parent_sn: int | None = None,
        ) -> dict[str, T.Any]:
            start_times[part_idx or 0] = time.monotonic()
            if part_idx in (1, 2):
                with contextlib.suppress(_threading.BrokenBarrierError):
                    gate.wait(timeout=5.0)
            end_times[part_idx or 0] = time.monotonic()
            self.download_calls.append({'task_sn': task_sn, 'part_idx': part_idx})
            return self._info

    dl = TimingDownloader(info=info)

    from app.models import AppSettings

    settings = AppSettings.model_validate({'bilibili-concurrent-parts': 2})
    runner = BilibiliRunner(
        ytdlp_downloader=dl,
        progress_bus=progress_bus,
        logger=logger,
        settings=settings,
    )
    runner.run(6, bvid='BV_concurrent', resolution='1080', classify=True)

    assert len(dl.download_calls) == n, 'all parts must have been downloaded'
    # Parts 1 and 2 must have overlapping windows.
    assert start_times[1] < end_times[2] and start_times[2] < end_times[1], (
        f'Parts 1 and 2 did not overlap. starts={start_times} ends={end_times}'
    )


def test_multipart_concurrent_parts_1_falls_back_to_sequential(progress_bus: ProgressBus, logger: Logger) -> None:
    """With bilibili_concurrent_parts=1 parts must run without overlap."""
    import time

    n = 3
    info = {
        'title': 'Sequential Title',
        'entries': [{} for _ in range(n)],
    }

    start_times: dict[int, float] = {}
    end_times: dict[int, float] = {}

    class TimingDownloader(FakeYtdlpDownloader):
        def download(  # type: ignore[override]
            self,
            task_sn: int,
            bvid: str,
            *,
            resolution: str,
            classify: bool,
            part_idx: int | None = None,
            parent_sn: int | None = None,
        ) -> dict[str, T.Any]:
            start_times[part_idx or 0] = time.monotonic()
            time.sleep(0.02)
            end_times[part_idx or 0] = time.monotonic()
            self.download_calls.append({'task_sn': task_sn, 'part_idx': part_idx})
            return self._info

    dl = TimingDownloader(info=info)

    from app.models import AppSettings

    settings = AppSettings.model_validate({'bilibili-concurrent-parts': 1})
    runner = BilibiliRunner(
        ytdlp_downloader=dl,
        progress_bus=progress_bus,
        logger=logger,
        settings=settings,
    )
    runner.run(7, bvid='BV_sequential', resolution='1080', classify=True)

    assert len(dl.download_calls) == n
    for i in range(1, n + 1):
        for j in range(1, n + 1):
            if i == j:
                continue
            # With max_workers=1, no two windows can overlap.
            overlap = start_times[i] < end_times[j] and start_times[j] < end_times[i]
            assert not overlap, (
                f'Parts {i} and {j} overlapped despite max_workers=1. starts={start_times} ends={end_times}'
            )


def test_multipart_cancel_with_parallel_marks_pending_as_cancelled(progress_bus: ProgressBus, logger: Logger) -> None:
    """After cancel, no child remains in '等待下載' or '正在下載'."""
    import threading as _threading

    n = 5
    info = {
        'title': 'Cancel Parallel',
        'entries': [{} for _ in range(n)],
    }

    cancel_event = _threading.Event()
    parent_sn = 20

    from app.downloader.progress import TaskProgress

    fake_parent = TaskProgress(sn=parent_sn, rate=0.0, status='下載中', filename='p')
    fake_parent._cancel_event = cancel_event
    progress_bus._entries[parent_sn] = fake_parent  # type: ignore[attr-defined]

    barrier = _threading.Barrier(2, timeout=5.0)

    class CancelAfterPart1Downloader(FakeYtdlpDownloader):
        def download(  # type: ignore[override]
            self,
            task_sn: int,
            bvid: str,
            *,
            resolution: str,
            classify: bool,
            part_idx: int | None = None,
            parent_sn: int | None = None,
        ) -> dict[str, T.Any]:
            self.download_calls.append({'task_sn': task_sn, 'part_idx': part_idx})
            if part_idx == 1:
                cancel_event.set()
                with contextlib.suppress(_threading.BrokenBarrierError):
                    barrier.wait()
            else:
                with contextlib.suppress(_threading.BrokenBarrierError):
                    barrier.wait()
                if parent_sn is not None:
                    ev = progress_bus.get_cancel_event(parent_sn)
                    if ev is not None and ev.is_set():
                        raise yt_dlp.utils.DownloadCancelled()
            return self._info

    dl = CancelAfterPart1Downloader(info=info)

    from app.models import AppSettings

    settings = AppSettings.model_validate({'bilibili-concurrent-parts': 2})
    runner = BilibiliRunner(
        ytdlp_downloader=dl,
        progress_bus=progress_bus,
        logger=logger,
        settings=settings,
    )
    runner.run(parent_sn, bvid='BV_cancel5', resolution='1080', classify=True)

    snap = progress_bus.snapshot()
    for child_sn, entry in snap.items():
        if child_sn == parent_sn:
            continue
        assert entry.status not in ('等待下載', '正在下載'), f'child_sn={child_sn} stuck in {entry.status!r}'


def test_settings_default_bilibili_concurrent_parts_is_2() -> None:
    """Fresh AppSettings defaults bilibili_concurrent_parts to 2."""
    from app.models import AppSettings

    s = AppSettings()
    assert s.bilibili_concurrent_parts == 2


def test_settings_clamps_bilibili_concurrent_parts_to_max(
    tmp_path: pathlib.Path,
) -> None:
    """Settings repo clamps bilibili_concurrent_parts > 5 down to 5."""
    import json

    from app.models import AppSettings
    from app.persistence.paths import WorkspacePaths
    from app.persistence.settings_repo import SettingsRepository

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    log = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = SettingsRepository(paths=paths, logger=log)

    defaults = AppSettings()
    blob = defaults.model_dump(by_alias=True)
    blob['bilibili-concurrent-parts'] = 99
    paths.config_path.write_text(json.dumps(blob), encoding='utf-8')

    loaded = repo.load()
    assert loaded.bilibili_concurrent_parts == 5

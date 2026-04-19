"""Tests for :class:`ManualRunner`."""

from __future__ import annotations

import pathlib
import threading
from typing import Any

from app.downloader.anime import DownloadResult
from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.models import AppSettings
from app.scheduler.manual_runner import ManualRunner


class _FakeAnime:
    def __init__(
        self,
        sn: int,
        *,
        episode_list: dict[str, int] | None = None,
    ) -> None:
        self.sn = int(sn)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._episode_list = episode_list or {'01': sn}
        self._info_shown = False
        self._danmu_enabled = False

    def load(self) -> None:
        self.calls.append(('load', {}))

    def get_episode_list(self) -> dict[str, int]:
        return dict(self._episode_list)

    def enable_danmu(self) -> None:
        self.calls.append(('enable_danmu', {}))
        self._danmu_enabled = True

    def get_info(self) -> None:
        self.calls.append(('get_info', {}))
        self._info_shown = True

    def download(self, **kwargs: Any) -> DownloadResult:
        self.calls.append(('download', kwargs))
        return DownloadResult(
            success=True,
            file_path=pathlib.Path(f'/tmp/{self.sn}.mp4'),
            size_mb=500,
        )


class _FakeRepo:
    def read(self, sn: int) -> None:
        return None


def _runner(
    tmp_path: pathlib.Path,
    anime_map: dict[int, _FakeAnime],
    *,
    progress_bus: ProgressBus | None = None,
) -> ManualRunner:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(download_resolution='1080')
    return ManualRunner(
        anime_factory=lambda sn: anime_map[int(sn)],  # type: ignore[arg-type]
        anime_repo=_FakeRepo(),  # type: ignore[arg-type]
        settings=settings,
        logger=logger,
        progress_bus=progress_bus,
    )


def test_single_mode_calls_download_once(tmp_path: pathlib.Path) -> None:
    fa = _FakeAnime(1)
    r = _runner(tmp_path, {1: fa})
    r.run(1, mode='single', resolution='1080')
    download_calls = [c for c in fa.calls if c[0] == 'download']
    assert len(download_calls) == 1


def test_manual_mode_does_not_pass_include_resolution_false(
    tmp_path: pathlib.Path,
) -> None:
    """Manual runner must NOT pass include_resolution_in_filename=False.

    The kwarg is absent from the call (defaults to True inside anime.download),
    so manual downloads keep the resolution suffix in the filename.
    """
    fa = _FakeAnime(2)
    r = _runner(tmp_path, {2: fa})
    r.run(2, mode='single', resolution='360')
    download_calls = [kw for name, kw in fa.calls if name == 'download']
    assert len(download_calls) == 1
    # False must NOT appear — absent key or True are both acceptable.
    assert download_calls[0].get('include_resolution_in_filename') is not False


def test_latest_picks_last_episode(tmp_path: pathlib.Path) -> None:
    fa_root = _FakeAnime(10, episode_list={'01': 10, '02': 11, '03': 12})
    fa_target = _FakeAnime(12)
    r = _runner(tmp_path, {10: fa_root, 12: fa_target})
    r.run(10, mode='latest')
    # The picked sn is 12 (last inserted).
    assert any(name == 'download' for name, _ in fa_target.calls)
    assert not any(name == 'download' for name, _ in fa_root.calls)


def test_largest_sn_picks_highest_sn(tmp_path: pathlib.Path) -> None:
    fa_root = _FakeAnime(20, episode_list={'01': 22, '02': 20, '03': 21})
    fa_target = _FakeAnime(22)
    r = _runner(tmp_path, {20: fa_root, 22: fa_target})
    r.run(20, mode='largest-sn')
    assert any(name == 'download' for name, _ in fa_target.calls)


def test_all_downloads_every_episode_in_parallel(tmp_path: pathlib.Path) -> None:
    sns = [30, 31, 32, 33]
    fakes = {sn: _FakeAnime(sn) for sn in sns}
    fa_root = _FakeAnime(30, episode_list={f'{i:02d}': s for i, s in enumerate(sns, start=1)})
    fakes[30] = fa_root

    # Count download concurrency.
    in_flight = [0]
    max_in_flight = [0]
    lock = threading.Lock()

    for sn, fa in list(fakes.items()):
        if sn == 30:
            continue
        real_download = fa.download

        def tracked_download(fa_ref: _FakeAnime, real_cb: Any) -> Any:
            def _inner(**kwargs: Any) -> Any:
                with lock:
                    in_flight[0] += 1
                    max_in_flight[0] = max(max_in_flight[0], in_flight[0])
                try:
                    threading.Event().wait(0.02)
                    return real_cb(**kwargs)
                finally:
                    with lock:
                        in_flight[0] -= 1

            return _inner

        fa.download = tracked_download(fa, real_download)  # type: ignore[assignment]

    r = _runner(tmp_path, fakes)
    r.run(30, mode='all', thread_limit=3)

    # Every episode got downloaded.
    for sn in sns:
        assert any(name == 'download' for name, _ in fakes[sn].calls)
    # Parallelism actually happened (at least 2 in flight at once).
    assert max_in_flight[0] >= 2


def test_range_mode_downloads_specified_range(tmp_path: pathlib.Path) -> None:
    episodes = {'1': 101, '2': 102, '3': 103, '4': 104, '5': 105}
    fa_root = _FakeAnime(100, episode_list=episodes)
    fakes: dict[int, _FakeAnime] = {100: fa_root}
    for sn in (102, 103, 104):
        fakes[sn] = _FakeAnime(sn)

    r = _runner(tmp_path, fakes)
    r.run(100, mode='range', ep_range=['2-4'], thread_limit=1)

    for sn in (102, 103, 104):
        assert any(name == 'download' for name, _ in fakes[sn].calls)
    # Un-requested eps not in the fake map — but we can confirm the root
    # never triggered a download on its own sn.
    assert not any(name == 'download' for name, _ in fa_root.calls)


def test_get_info_skips_download(tmp_path: pathlib.Path) -> None:
    fa = _FakeAnime(200)
    r = _runner(tmp_path, {200: fa})
    r.run(200, mode='single', get_info=True)
    assert any(name == 'get_info' for name, _ in fa.calls)
    assert not any(name == 'download' for name, _ in fa.calls)


def test_cui_danmu_enables_danmu_before_download(tmp_path: pathlib.Path) -> None:
    fa = _FakeAnime(300)
    r = _runner(tmp_path, {300: fa})
    r.run(300, mode='single', cui_danmu=True)

    # Both calls recorded; ``enable_danmu`` must come first.
    names = [c[0] for c in fa.calls]
    assert 'enable_danmu' in names
    assert 'download' in names
    assert names.index('enable_danmu') < names.index('download')


def test_progress_entry_seeded_before_anime_load(tmp_path: pathlib.Path) -> None:
    """The manual runner seeds a ``'等待下載'`` progress entry BEFORE the
    real ``Anime.load()`` / ``download()`` pipeline begins, so the UI shows
    the task as queued immediately even when dispatch is slow."""
    observed: list[tuple[int, str]] = []

    class _ObservingAnime(_FakeAnime):
        def __init__(self, sn: int, bus: ProgressBus) -> None:
            super().__init__(sn)
            self._bus = bus

        def load(self) -> None:
            # Capture the progress state AS load begins — ``_announce_waiting``
            # must have run already.
            snap = self._bus.snapshot()
            entry = snap.get(self.sn)
            observed.append((self.sn, entry.status if entry is not None else '<absent>'))
            super().load()

    bus = ProgressBus()
    fa = _ObservingAnime(400, bus)
    r = _runner(tmp_path, {400: fa}, progress_bus=bus)
    r.run(400, mode='single')

    assert observed == [(400, '等待下載')]


# ---------------------------------------------------------------------------
# Bug (3) — mode='all' announces every episode as waiting upfront
# ---------------------------------------------------------------------------


def test_mode_all_announces_waiting_for_every_episode(
    tmp_path: pathlib.Path,
) -> None:
    """mode='all' must seed a '等待下載' progress card for EVERY episode BEFORE
    submitting any work to the thread pool, so the Monitor shows N cards
    immediately rather than trickling them in as workers pick up tasks.

    The root sn (499) is deliberately distinct from the episode sns (500, 501,
    502) so that the root anime's load() call — which happens before
    _download_many — does not interfere with the observation of the episode
    announce-before-load invariant.
    """
    root_sn = 499
    episode_sns = [500, 501, 502]
    episode_list = {f'{i:02d}': sn for i, sn in enumerate(episode_sns, start=1)}

    fa_root = _FakeAnime(root_sn, episode_list=episode_list)
    fakes: dict[int, _FakeAnime] = {root_sn: fa_root}
    for sn in episode_sns:
        fakes[sn] = _FakeAnime(sn)

    announced_before_any_load: list[int] = []

    class _ObservingAnime(_FakeAnime):
        """Capture the bus snapshot when load() is first called."""

        def __init__(self, sn: int, bus: ProgressBus, orig: _FakeAnime) -> None:
            super().__init__(sn, episode_list=orig._episode_list)
            self._bus = bus
            self._captured = False

        def load(self) -> None:
            if not self._captured:
                self._captured = True
                snap = self._bus.snapshot()
                # Record which SNs are already in "等待下載" at this point.
                announced_before_any_load.extend(s for s, e in snap.items() if e.status == '等待下載')
            super().load()

    bus = ProgressBus()
    observing: dict[int, _FakeAnime] = {}
    for sn, fa in fakes.items():
        observing[sn] = _ObservingAnime(sn, bus, fa)

    r = _runner(tmp_path, observing, progress_bus=bus)
    r.run(root_sn, mode='all', thread_limit=1)

    # All three episode SNs must have been announced before the first episode load().
    for sn in episode_sns:
        assert sn in announced_before_any_load, (
            f"sn={sn} was not announced as '等待下載' before any download began; "
            f'only {announced_before_any_load} were visible'
        )


# ---------------------------------------------------------------------------
# Bug (2) — finish() is always called even when download raises
# ---------------------------------------------------------------------------


def test_manual_runner_always_calls_finish_on_exception(
    tmp_path: pathlib.Path,
) -> None:
    """If Anime.download() raises an unexpected exception, _download_one must
    still call progress_bus.finish(sn) so the DB row is closed and the UI card
    eventually disappears."""

    class _BrokenAnime(_FakeAnime):
        def download(self, **kwargs: Any) -> DownloadResult:
            raise RuntimeError('simulated catastrophic failure')

    finish_calls: list[int] = []

    class _SpyBus(ProgressBus):
        def finish(self, sn: int) -> None:
            finish_calls.append(sn)
            super().finish(sn)

    bus = _SpyBus()
    fa = _BrokenAnime(600)
    r = _runner(tmp_path, {600: fa}, progress_bus=bus)

    # The exception must be swallowed inside _download_one (not re-raised).
    r.run(600, mode='single')

    # finish() must have been called at least once for sn=600.
    assert 600 in finish_calls, f'finish() was not called for sn=600; finish_calls={finish_calls}'
    # The status must be '失敗' so the DB row reflects the real outcome.
    snap = bus.snapshot()
    if 600 in snap:
        # Entry may still be visible (not yet pruned).
        assert snap[600].status == '失敗', f"Expected '失敗' after unexpected exception, got {snap[600].status!r}"


def test_manual_runner_finish_called_with_failed_status_on_no_stream(
    tmp_path: pathlib.Path,
) -> None:
    """When Anime.download() raises NoAvailableStreamError (e.g. deleted episode),
    _download_one must update status to '失敗' BEFORE calling finish() so the DB
    row gets '失敗' instead of being normalised to '中斷'.
    """
    from app.downloader import exceptions as exc_mod

    class _NoStreamAnime(_FakeAnime):
        def download(self, **kwargs: Any) -> DownloadResult:
            raise exc_mod.NoAvailableStreamError('page has no title — episode may be deleted')

    status_at_finish: list[str] = []

    class _SpyBus(ProgressBus):
        def finish(self, sn: int) -> None:
            snap = self.snapshot()
            entry = snap.get(sn)
            if entry is not None:
                status_at_finish.append(entry.status)
            super().finish(sn)

    bus = _SpyBus()
    fa = _NoStreamAnime(601)
    r = _runner(tmp_path, {601: fa}, progress_bus=bus)

    r.run(601, mode='single')

    assert status_at_finish, 'finish() was never called'
    assert status_at_finish[0] == '失敗', f"Expected '失敗' at finish() time, got {status_at_finish[0]!r}"


# ---------------------------------------------------------------------------
# Cooldown note:
# Cooldown is now applied inside Anime.download() (after metadata parse,
# before segment/ffmpeg download begins). ManualRunner no longer holds a
# cooldown reference. Cooldown integration tests live in test_anime_orchestrator.py.
# ---------------------------------------------------------------------------


def test_manual_runner_without_cooldown_still_downloads(
    tmp_path: pathlib.Path,
) -> None:
    """ManualRunner with no cooldown wired must not raise and must still download."""
    fa = _FakeAnime(501)
    r = _runner(tmp_path, {501: fa})  # no cooldown
    r.run(501, mode='single')
    assert any(name == 'download' for name, _ in fa.calls)


# ---------------------------------------------------------------------------
# Pre-parse: parallel metadata fetch before download
# ---------------------------------------------------------------------------


def _runner_with_extractor(
    tmp_path: pathlib.Path,
    anime_map: dict[int, _FakeAnime],
    *,
    progress_bus: ProgressBus,
    metadata_extractor: Any,
) -> ManualRunner:
    """Build a ManualRunner with a metadata_extractor wired for pre-parse tests."""
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(download_resolution='1080')
    return ManualRunner(
        anime_factory=lambda sn: anime_map[int(sn)],  # type: ignore[arg-type]
        anime_repo=_FakeRepo(),  # type: ignore[arg-type]
        settings=settings,
        logger=logger,
        progress_bus=progress_bus,
        metadata_extractor=metadata_extractor,  # type: ignore[arg-type]
    )


def test_pre_parse_updates_metadata_before_download(
    tmp_path: pathlib.Path,
) -> None:
    """With multi_thread=1 and 2 sns, pre-parse must call update_metadata for
    both sns before _download_one completes for sn 1.

    The test uses a blocking barrier inside the first sn's download() to
    ensure the pre-parse pool has had a chance to run.  Both update_metadata
    calls must have been observed by the time the barrier releases.
    """
    import dataclasses

    @dataclasses.dataclass
    class _FakeMeta:
        bangumi_name: str
        episode: str
        episode_list: dict[str, int]

    class _FakeExtractor:
        """Fake MetadataExtractor — returns hardcoded metadata per sn."""

        def fetch(self, sn: int) -> _FakeMeta:
            return _FakeMeta(
                bangumi_name=f'番劇_{sn}',
                episode=f'0{sn}',
                episode_list={f'0{sn}': sn},
            )

    update_metadata_calls: list[int] = []

    class _SpyBus(ProgressBus):
        def update_metadata(
            self,
            sn: int,
            *,
            bangumi_name: str | None = None,
            episode: str | None = None,
            resolution: str | None = None,
            filename: str | None = None,
        ) -> None:
            update_metadata_calls.append(sn)
            super().update_metadata(
                sn,
                bangumi_name=bangumi_name,
                episode=episode,
                resolution=resolution,
                filename=filename,
            )

    sns = [700, 701]
    fakes: dict[int, _FakeAnime] = {}

    # For sn=700, use a download() that blocks until both pre-parses have run.
    pre_parse_done = threading.Event()

    class _BlockingAnime(_FakeAnime):
        def download(self, **kwargs: Any) -> DownloadResult:
            # Wait for both pre-parse calls to arrive (up to 5 s).
            pre_parse_done.wait(timeout=5)
            return super().download(**kwargs)

    fakes[700] = _BlockingAnime(700)
    fakes[701] = _FakeAnime(701)

    bus = _SpyBus()
    extractor = _FakeExtractor()
    runner = _runner_with_extractor(tmp_path, fakes, progress_bus=bus, metadata_extractor=extractor)

    import time

    def _set_pre_parse_done() -> None:
        # Poll until both sns have been pre-parsed (or timeout).
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if len(update_metadata_calls) >= 2:
                pre_parse_done.set()
                return
            time.sleep(0.02)
        pre_parse_done.set()  # unblock download even on timeout

    watcher = threading.Thread(target=_set_pre_parse_done, daemon=True)
    watcher.start()

    runner.run(None, mode='multi', ep_range=[str(s) for s in sns], thread_limit=1)

    # Both sns must have been pre-parsed (update_metadata called).
    assert 700 in update_metadata_calls, f'sn=700 was not pre-parsed; calls={update_metadata_calls}'
    assert 701 in update_metadata_calls, f'sn=701 was not pre-parsed; calls={update_metadata_calls}'

    # The progress bus must show bangumi names for both.
    snap = bus.snapshot()
    for sn in sns:
        entry = snap.get(sn)
        assert entry is not None, f'sn={sn} not in snapshot'
        assert entry.bangumi_name == f'番劇_{sn}', f'sn={sn} bangumi_name={entry.bangumi_name!r}'


# ---------------------------------------------------------------------------
# Bug fix — worker must skip a cancelled task without re-announcing it
# ---------------------------------------------------------------------------


def test_worker_skips_cancelled_task_without_announcing(
    tmp_path: pathlib.Path,
) -> None:
    """When _download_one is called for a sn whose cancel_event is already set,
    it must return immediately without calling _announce_waiting / progress.start.

    Scenario reproduced:
    1. _download_many announces sn A and sn B as '等待下載', submits futures.
    2. User cancels sn B → cancel_event for B is set, Timer fires finish(B).
    3. Worker slot opens → worker picks up sn B's future → _download_one(B).
    4. Without the fix, _announce_waiting(B) → start(B, '等待下載') re-creates
       the card. With the fix, _download_one returns immediately.

    The test uses thread_limit=1 so we can control execution order:
    - sn A runs first (blocks on a barrier inside download()).
    - While A is blocked, we cancel B's entry on the bus directly.
    - A finishes, freeing the slot; B's future runs next.
    - We verify B's start_calls count has NOT increased beyond the initial
      _announce_waiting call made by _download_many.
    """
    import time as _time

    start_calls: list[int] = []

    class _SpyBus(ProgressBus):
        def start(self, sn: int, filename: str, **kwargs: Any) -> None:
            start_calls.append(sn)
            super().start(sn, filename, **kwargs)

    bus = _SpyBus()

    # sn A: blocks until we release the barrier.
    a_running = threading.Event()
    a_release = threading.Event()

    class _BlockingAnime(_FakeAnime):
        def download(self, **kwargs: Any) -> DownloadResult:
            a_running.set()
            a_release.wait(timeout=5)
            return super().download(**kwargs)

    sn_a, sn_b = 800, 801
    fa_a = _BlockingAnime(sn_a)
    fa_b = _FakeAnime(sn_b)
    fakes = {sn_a: fa_a, sn_b: fa_b}

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(download_resolution='1080')
    runner = ManualRunner(
        anime_factory=lambda sn: fakes[int(sn)],  # type: ignore[arg-type]
        anime_repo=_FakeRepo(),  # type: ignore[arg-type]
        settings=settings,
        logger=logger,
        progress_bus=bus,
    )

    def _run_download() -> None:
        runner.run(None, mode='multi', ep_range=[str(sn_a), str(sn_b)], thread_limit=1)

    t = threading.Thread(target=_run_download, daemon=True)
    t.start()

    # Wait until sn A's download() is running.
    a_running.wait(timeout=5)

    # Both sns were announced by _download_many; record counts at this point.
    initial_a_count = start_calls.count(sn_a)
    initial_b_count = start_calls.count(sn_b)

    # Cancel sn B while it's still queued (A holds the slot).
    bus.cancel(sn_b)

    # Let A finish so the slot opens and B's future executes.
    a_release.set()
    t.join(timeout=10)

    # sn A must have been started exactly once (from _announce_waiting in _download_many).
    assert start_calls.count(sn_a) == initial_a_count, f'sn_a start_calls changed after cancel: {start_calls}'
    # sn B must NOT have gotten an extra start() call when the worker woke up.
    assert start_calls.count(sn_b) == initial_b_count, (
        f'Cancelled sn {sn_b} had start() called {start_calls.count(sn_b)} times '
        f'(expected {initial_b_count}); start_calls={start_calls}'
    )
    # sn B's status must still be '已取消' (or finished_at stamped), not '等待下載'.
    snap = bus.snapshot()
    if sn_b in snap:
        assert snap[sn_b].status != '等待下載', f"Cancelled sn {sn_b} reappeared with status '等待下載'"
        assert snap[sn_b].status == '已取消', f"Expected '已取消' but got {snap[sn_b].status!r}"


# ---------------------------------------------------------------------------
# Task 2 bug fixes — stuck at 等待下載 paths
# ---------------------------------------------------------------------------


def test_download_one_unknown_exception_in_load_calls_finish_with_failed(
    tmp_path: pathlib.Path,
) -> None:
    """Bug B: if anime.load() raises an unexpected exception, _download_one
    must set status '失敗' and call finish() so the DB row is closed and the
    UI card does not stay stuck at '等待下載'."""

    class _BrokenLoadAnime(_FakeAnime):
        def load(self) -> None:
            raise RuntimeError('unexpected load error')

    finish_calls: list[int] = []
    status_calls: list[tuple[int, str]] = []

    class _SpyBus(ProgressBus):
        def finish(self, sn: int) -> None:
            finish_calls.append(sn)
            super().finish(sn)

        def update_status(self, sn: int, status: str) -> None:
            status_calls.append((sn, status))
            super().update_status(sn, status)

    bus = _SpyBus()
    fa = _BrokenLoadAnime(900)
    r = _runner(tmp_path, {900: fa}, progress_bus=bus)

    r.run(900, mode='single')

    assert 900 in finish_calls, f'finish() was not called for sn=900; finish_calls={finish_calls}'
    failed_statuses = [s for (sn, s) in status_calls if sn == 900 and s == '失敗']
    assert failed_statuses, f"update_status(900, '失敗') never called; status_calls={status_calls}"


def test_download_one_cancelled_before_start_calls_finish(
    tmp_path: pathlib.Path,
) -> None:
    """Bug A: when cancel_event is set before _download_one executes,
    the function must call finish() before returning so the DB row is
    not left dangling."""
    finish_calls: list[int] = []

    class _SpyBus(ProgressBus):
        def finish(self, sn: int) -> None:
            finish_calls.append(sn)
            super().finish(sn)

    bus = _SpyBus()
    fa = _FakeAnime(910)

    # Seed an entry and cancel it before _download_one runs.
    bus.start(910, '《test》', status='等待下載')
    bus.cancel(910)  # sets the cancel_event for sn=910

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(download_resolution='1080')
    runner = ManualRunner(
        anime_factory=lambda sn: {910: fa}[int(sn)],  # type: ignore[arg-type]
        anime_repo=_FakeRepo(),  # type: ignore[arg-type]
        settings=settings,
        logger=logger,
        progress_bus=bus,
    )

    # Call _download_one directly; the cancel guard must trigger.
    runner._download_one(  # type: ignore[attr-defined]
        910,
        resolution='1080',
        save_dir=None,
        classify=True,
        get_info=False,
        cui_danmu=False,
        realtime_show_file_size=False,
    )

    assert 910 in finish_calls, f'finish() was not called after early cancel; finish_calls={finish_calls}'


def test_download_one_get_info_mode_calls_finish(
    tmp_path: pathlib.Path,
) -> None:
    """Bug C: get_info=True path must call finish() before returning so the
    UI card is closed after metadata display."""
    finish_calls: list[int] = []

    class _SpyBus(ProgressBus):
        def finish(self, sn: int) -> None:
            finish_calls.append(sn)
            super().finish(sn)

    bus = _SpyBus()
    fa = _FakeAnime(920)
    r = _runner(tmp_path, {920: fa}, progress_bus=bus)

    r.run(920, mode='single', get_info=True)

    assert any(name == 'get_info' for name, _ in fa.calls), 'get_info() was never called'
    assert 920 in finish_calls, f'finish() was not called after get_info; finish_calls={finish_calls}'


# ---------------------------------------------------------------------------
# Task 1 — parse cooldown wired into _pre_parse
# ---------------------------------------------------------------------------


def test_parse_cooldown_wait_called_before_metadata_fetch(
    tmp_path: pathlib.Path,
) -> None:
    """parse_cooldown.wait() must be called once per _pre_parse invocation
    before metadata_extractor.fetch(sn)."""
    import dataclasses

    @dataclasses.dataclass
    class _FakeMeta:
        bangumi_name: str
        episode: str
        episode_list: dict[str, int]

    fetch_order: list[str] = []

    class _FakeExtractor:
        def fetch(self, sn: int) -> _FakeMeta:
            fetch_order.append(f'fetch:{sn}')
            return _FakeMeta(
                bangumi_name=f'番劇_{sn}',
                episode=f'0{sn}',
                episode_list={f'0{sn}': sn},
            )

    class _FakeCooldown:
        def __init__(self) -> None:
            self.wait_calls: int = 0

        def wait(self, *, progress_bus: object = None, sn: object = None) -> None:
            fetch_order.append('wait')
            self.wait_calls += 1

    cooldown = _FakeCooldown()

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(download_resolution='1080')
    bus = ProgressBus()
    sn = 930
    fa = _FakeAnime(sn)

    runner = ManualRunner(
        anime_factory=lambda s: {sn: fa}[int(s)],  # type: ignore[arg-type]
        anime_repo=_FakeRepo(),  # type: ignore[arg-type]
        settings=settings,
        logger=logger,
        progress_bus=bus,
        metadata_extractor=_FakeExtractor(),  # type: ignore[arg-type]
        parse_cooldown=cooldown,  # type: ignore[arg-type]
    )

    bus.start(sn, f'《{sn}》', status='等待下載')
    runner._pre_parse(sn)  # type: ignore[attr-defined]

    assert cooldown.wait_calls == 1, f'expected wait() called once, got {cooldown.wait_calls}'
    # wait must precede fetch
    wait_idx = fetch_order.index('wait')
    fetch_idx = fetch_order.index(f'fetch:{sn}')
    assert wait_idx < fetch_idx, f'wait() came after fetch(); order={fetch_order}'

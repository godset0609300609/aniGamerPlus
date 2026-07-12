"""Tests for BtProgressReconciler — boot-time ghost-task reconciliation.

Repos, the task_id_map allocator and the Redis progress reader are all
hand-written fakes so these stay pure unit tests of the reconciliation
*logic* (which rows get force-finished and which don't) rather than
integration tests of the real DB/Redis wiring.
"""

from __future__ import annotations

import datetime
import typing as T

import pytest

from app.downloader.progress import ProgressBus, TaskProgress
from app.services.bt_progress_reconciler import BtProgressReconciler

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


_STALE_GHOST_TERMINAL_PUTIO_STATUSES = ('遠端已清理', '遠端已移除', '失敗', 'ERROR')


class _FakeBtRow:
    def __init__(
        self,
        entry_id: int,
        *,
        local_path: str | None = None,
        putio_status: str | None = None,
        title: str = 'stale.mp4',
        dispatched_at: datetime.datetime | None = None,
    ) -> None:
        self.id = entry_id
        self.local_path = local_path
        self.putio_status = putio_status
        self.title = title
        self.dispatched_at = dispatched_at


class FakeBtFeedEntryRepo:
    def __init__(
        self,
        landed: list[_FakeBtRow] | None = None,
        terminal_unlanded: list[_FakeBtRow] | None = None,
        stale_candidates: list[_FakeBtRow] | None = None,
    ) -> None:
        self._landed = landed or []
        self._terminal_unlanded = terminal_unlanded or []
        self._stale_candidates = stale_candidates or []
        # Records every cutoff_hours the reconciler asked for — lets tests
        # assert the env-var-derived cutoff was actually plumbed through.
        self.stale_ghost_calls: list[int] = []

    def list_landed(self) -> list[_FakeBtRow]:
        return list(self._landed)

    def list_terminal_unlanded(self, statuses: T.Sequence[str]) -> list[_FakeBtRow]:
        return [r for r in self._terminal_unlanded if r.putio_status in statuses]

    def list_stale_in_flight_ghosts(self, cutoff_hours: int = 1) -> list[_FakeBtRow]:
        """Mirrors the real repo's SQL filter so tests can exercise genuine
        cutoff-hour boundary behaviour, not just call-delegation."""
        self.stale_ghost_calls.append(cutoff_hours)
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=cutoff_hours)
        return [
            r
            for r in self._stale_candidates
            if r.dispatched_at is not None
            and r.dispatched_at < cutoff
            and r.local_path is None
            and r.putio_status not in _STALE_GHOST_TERMINAL_PUTIO_STATUSES
        ]


class _FakeTgRow:
    def __init__(self, progress_sn: int | None, file_name: str) -> None:
        self.progress_sn = progress_sn
        self.file_name = file_name


class FakeTgDownloadedMediaRepo:
    def __init__(self, rows: list[_FakeTgRow] | None = None) -> None:
        self._rows = rows or []

    def list_landed_with_progress_sn(self) -> list[_FakeTgRow]:
        return list(self._rows)


class _FakeHistoryRow:
    def __init__(self, sn: int, *, filename: str, started_at: datetime.datetime | None) -> None:
        self.sn = sn
        self.filename = filename
        self.started_at = started_at


class FakeTaskHistoryRepo:
    """Mirrors TaskHistoryRepository.list_stale_in_progress's real filter —
    only 'tg'-sourced rows are ever seeded in these tests, so source is
    recorded (and asserted) but not itself filtered on."""

    def __init__(self, rows: list[_FakeHistoryRow] | None = None) -> None:
        self._rows = rows or []
        self.stale_calls: list[tuple[str, int]] = []

    def list_stale_in_progress(self, source: str, cutoff_hours: int = 1) -> list[_FakeHistoryRow]:
        self.stale_calls.append((source, cutoff_hours))
        assert source == 'tg'
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=cutoff_hours)
        return [r for r in self._rows if r.started_at is not None and r.started_at < cutoff]


class FakeTaskIdMapRepo:
    """Deterministic sn allocation: sn = 1000 + int(external_id), only for source='bt'."""

    def __init__(self) -> None:
        self.allocate_calls: list[tuple[str, str]] = []

    def allocate(self, source: str, external_id: str) -> int:
        self.allocate_calls.append((source, external_id))
        assert source == 'bt'
        return 1000 + int(external_id)


class FakeRedisProgressReader:
    """Static snapshot — does not observe writes made through a mirror."""

    def __init__(self, snapshot: dict[int, TaskProgress]) -> None:
        self._snapshot = snapshot

    async def snapshot(self) -> dict[int, TaskProgress]:
        return dict(self._snapshot)


class FakeRedisStore:
    """In-memory stand-in for the Redis progress hash store.

    Doubles as both a ``_ProgressMirror`` (write side, wired into a real
    ``ProgressBus``) and a ``RedisProgressReader`` (read side, passed to the
    reconciler) so tests can exercise a real force_finish -> mirror ->
    reader round trip and prove idempotency across repeated boots.
    """

    def __init__(self) -> None:
        self.entries: dict[int, TaskProgress] = {}

    def publish(self, sn: int, entry: TaskProgress) -> None:
        self.entries[sn] = entry

    def publish_finish(self, sn: int, entry: TaskProgress) -> None:
        self.entries[sn] = entry

    async def snapshot(self) -> dict[int, TaskProgress]:
        return dict(self.entries)


class FakeLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[T.Any, ...]] = []

    def info(self, sn: object, tag: str, detail: str = '', **kwargs: object) -> None:
        self.info_calls.append((sn, tag, detail))


def _live_entry(sn: int, *, finished: bool) -> TaskProgress:
    return TaskProgress(
        sn=sn,
        rate=0.0 if not finished else 1.0,
        status='落地中' if not finished else '下載完成',
        filename='stale.mp4',
        finished_at=datetime.datetime.now(datetime.UTC) if finished else None,
    )


def _make_reconciler(
    *,
    bt_repo: FakeBtFeedEntryRepo | None = None,
    tg_repo: FakeTgDownloadedMediaRepo | None = None,
    task_id_map_repo: FakeTaskIdMapRepo | None = None,
    bt_progress_bus: ProgressBus | None = None,
    progress_bus: ProgressBus | None = None,
    redis_progress_reader: object | None = None,
    task_history_repo: FakeTaskHistoryRepo | None = None,
    logger: FakeLogger | None = None,
) -> BtProgressReconciler:
    return BtProgressReconciler(
        bt_repo or FakeBtFeedEntryRepo(),
        tg_repo or FakeTgDownloadedMediaRepo(),
        task_id_map_repo or FakeTaskIdMapRepo(),
        bt_progress_bus or ProgressBus(),
        progress_bus or ProgressBus(),
        redis_progress_reader,  # type: ignore[arg-type]
        task_history_repo=task_history_repo,  # type: ignore[arg-type]
        logger=logger,
    )


# ---------------------------------------------------------------------------
# The five required scenarios
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconcile_finishes_landed_bt_entries_that_have_stale_progress_bus_entries() -> None:
    """A landed BT entry whose ProgressBus entry is stuck non-terminal gets force-finished."""
    entry_id = 42
    sn = 1000 + entry_id
    bt_repo = FakeBtFeedEntryRepo(landed=[_FakeBtRow(entry_id, local_path='EP01.mp4')])
    bt_progress_bus = ProgressBus()
    redis_reader = FakeRedisProgressReader({sn: _live_entry(sn, finished=False)})

    reconciler = _make_reconciler(bt_repo=bt_repo, bt_progress_bus=bt_progress_bus, redis_progress_reader=redis_reader)
    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert bt_fixed == 1
    assert tg_fixed == 0
    snap = bt_progress_bus.snapshot()
    assert sn in snap
    assert snap[sn].finished_at is not None
    assert snap[sn].status == '下載完成'
    assert snap[sn].filename == 'EP01.mp4'
    assert snap[sn].rate == 1.0


@pytest.mark.anyio
async def test_reconcile_finishes_failed_bt_entries_with_terminal_putio_status_but_no_landing() -> None:
    """A dispatched-but-never-landed BT entry that ended in a terminal Put.io status is closed
    out as a failure, not a success."""
    entry_id = 7
    sn = 1000 + entry_id
    bt_repo = FakeBtFeedEntryRepo(terminal_unlanded=[_FakeBtRow(entry_id, local_path=None, putio_status='ERROR')])
    bt_progress_bus = ProgressBus()
    redis_reader = FakeRedisProgressReader({sn: _live_entry(sn, finished=False)})

    reconciler = _make_reconciler(bt_repo=bt_repo, bt_progress_bus=bt_progress_bus, redis_progress_reader=redis_reader)
    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert bt_fixed == 1
    assert tg_fixed == 0
    snap = bt_progress_bus.snapshot()
    assert snap[sn].status == '失敗'
    assert snap[sn].finished_at is not None


@pytest.mark.anyio
async def test_reconcile_no_op_when_progress_bus_already_terminal_for_row() -> None:
    """A row whose Redis entry already has finished_at set must not be touched again."""
    entry_id = 3
    sn = 1000 + entry_id
    bt_repo = FakeBtFeedEntryRepo(landed=[_FakeBtRow(entry_id, local_path='EP01.mp4')])
    bt_progress_bus = ProgressBus()
    redis_reader = FakeRedisProgressReader({sn: _live_entry(sn, finished=True)})

    reconciler = _make_reconciler(bt_repo=bt_repo, bt_progress_bus=bt_progress_bus, redis_progress_reader=redis_reader)
    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert bt_fixed == 0
    assert tg_fixed == 0
    # Nothing was created locally — force_finish was never called.
    assert bt_progress_bus.snapshot() == {}


@pytest.mark.anyio
async def test_reconcile_no_op_when_no_progress_bus_entry_exists_for_row() -> None:
    """A landed row with no corresponding Redis entry at all (nothing ever tracked it) is left alone."""
    entry_id = 9
    bt_repo = FakeBtFeedEntryRepo(landed=[_FakeBtRow(entry_id, local_path='EP01.mp4')])
    bt_progress_bus = ProgressBus()
    redis_reader = FakeRedisProgressReader({})

    reconciler = _make_reconciler(bt_repo=bt_repo, bt_progress_bus=bt_progress_bus, redis_progress_reader=redis_reader)
    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert bt_fixed == 0
    assert tg_fixed == 0
    assert bt_progress_bus.snapshot() == {}


@pytest.mark.anyio
async def test_reconcile_handles_tg_downloaded_media_landed() -> None:
    """A landed TG download whose ProgressBus entry is stuck non-terminal gets force-finished,
    using the shared (history_repo-backed) progress_bus rather than bt_progress_bus."""
    sn = 555
    tg_repo = FakeTgDownloadedMediaRepo(rows=[_FakeTgRow(progress_sn=sn, file_name='movie.mkv')])
    progress_bus = ProgressBus()
    bt_progress_bus = ProgressBus()
    redis_reader = FakeRedisProgressReader({sn: _live_entry(sn, finished=False)})

    reconciler = _make_reconciler(
        tg_repo=tg_repo,
        bt_progress_bus=bt_progress_bus,
        progress_bus=progress_bus,
        redis_progress_reader=redis_reader,
    )
    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert bt_fixed == 0
    assert tg_fixed == 1
    # Must land on the TG (shared) bus, not the BT bus.
    assert bt_progress_bus.snapshot() == {}
    snap = progress_bus.snapshot()
    assert snap[sn].finished_at is not None
    assert snap[sn].status == '下載完成'
    assert snap[sn].filename == 'movie.mkv'


# ---------------------------------------------------------------------------
# Additional coverage
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconcile_returns_zero_when_no_redis_progress_reader_configured() -> None:
    """Without a Redis mirror there is no cross-process ghost state to clean up."""
    bt_repo = FakeBtFeedEntryRepo(landed=[_FakeBtRow(1, local_path='EP01.mp4')])
    reconciler = _make_reconciler(bt_repo=bt_repo, redis_progress_reader=None)

    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert (bt_fixed, tg_fixed) == (0, 0)


@pytest.mark.anyio
async def test_reconcile_skips_tg_rows_with_no_progress_sn() -> None:
    """Legacy rows written before the progress_sn column existed are silently skipped."""
    tg_repo = FakeTgDownloadedMediaRepo(rows=[_FakeTgRow(progress_sn=None, file_name='legacy.mkv')])
    redis_reader = FakeRedisProgressReader({})

    reconciler = _make_reconciler(tg_repo=tg_repo, redis_progress_reader=redis_reader)
    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert (bt_fixed, tg_fixed) == (0, 0)


@pytest.mark.anyio
async def test_reconcile_swallows_snapshot_failure_without_raising() -> None:
    """A Redis hiccup during the snapshot fetch must not propagate — boot must not be blocked."""

    class _ExplodingReader:
        async def snapshot(self) -> dict[int, TaskProgress]:
            raise ConnectionError('redis unreachable')

    bt_repo = FakeBtFeedEntryRepo(landed=[_FakeBtRow(1, local_path='EP01.mp4')])
    reconciler = _make_reconciler(bt_repo=bt_repo, redis_progress_reader=_ExplodingReader())

    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert (bt_fixed, tg_fixed) == (0, 0)


@pytest.mark.anyio
async def test_reconcile_one_bad_bt_row_does_not_abort_the_rest_of_the_pass() -> None:
    """A row whose sn allocation blows up must not prevent other rows from being reconciled."""

    class _FlakyTaskIdMapRepo:
        def allocate(self, source: str, external_id: str) -> int:
            if external_id == 'boom':
                raise RuntimeError('db error')
            return 1000 + int(external_id)

    good_id = 5
    sn = 1005
    bt_repo = FakeBtFeedEntryRepo(
        landed=[
            _FakeBtRow('boom', local_path='bad.mp4'),  # type: ignore[arg-type]
            _FakeBtRow(good_id, local_path='EP05.mp4'),
        ]
    )
    bt_progress_bus = ProgressBus()
    redis_reader = FakeRedisProgressReader({sn: _live_entry(sn, finished=False)})

    reconciler = _make_reconciler(
        bt_repo=bt_repo,
        task_id_map_repo=_FlakyTaskIdMapRepo(),  # type: ignore[arg-type]
        bt_progress_bus=bt_progress_bus,
        redis_progress_reader=redis_reader,
    )
    bt_fixed, _tg_fixed = await reconciler.reconcile_on_boot()

    assert bt_fixed == 1
    assert bt_progress_bus.snapshot()[sn].finished_at is not None


@pytest.mark.anyio
async def test_reconcile_logs_summary_line_when_rows_fixed() -> None:
    entry_id = 1
    sn = 1000 + entry_id
    bt_repo = FakeBtFeedEntryRepo(landed=[_FakeBtRow(entry_id, local_path='EP01.mp4')])
    redis_reader = FakeRedisProgressReader({sn: _live_entry(sn, finished=False)})
    logger = FakeLogger()

    reconciler = _make_reconciler(bt_repo=bt_repo, redis_progress_reader=redis_reader, logger=logger)
    await reconciler.reconcile_on_boot()

    assert len(logger.info_calls) == 1
    _sn, tag, detail = logger.info_calls[0]
    assert tag == 'BT/TG啟動對帳'
    assert '1 個 BT' in detail
    assert '0 個 TG' in detail


@pytest.mark.anyio
async def test_reconcile_does_not_log_when_nothing_fixed() -> None:
    redis_reader = FakeRedisProgressReader({})
    logger = FakeLogger()

    reconciler = _make_reconciler(redis_progress_reader=redis_reader, logger=logger)
    await reconciler.reconcile_on_boot()

    assert logger.info_calls == []


@pytest.mark.anyio
async def test_reconcile_on_boot_is_idempotent_across_repeated_calls() -> None:
    """A second reconcile pass (as would happen on the next scheduler restart) must be a no-op
    once the first pass has written the terminal state back through the mirror."""
    entry_id = 11
    sn = 1000 + entry_id
    bt_repo = FakeBtFeedEntryRepo(landed=[_FakeBtRow(entry_id, local_path='EP11.mp4')])
    store = FakeRedisStore()
    store.entries[sn] = _live_entry(sn, finished=False)
    bt_progress_bus = ProgressBus(mirror=store)

    reconciler = _make_reconciler(bt_repo=bt_repo, bt_progress_bus=bt_progress_bus, redis_progress_reader=store)

    first_bt_fixed, _ = await reconciler.reconcile_on_boot()
    assert first_bt_fixed == 1
    assert store.entries[sn].finished_at is not None

    second_bt_fixed, _ = await reconciler.reconcile_on_boot()
    assert second_bt_fixed == 0


# ---------------------------------------------------------------------------
# Stale in-flight ghosts — rows the scheduler restarted mid-flight, with no
# terminal outcome recorded either way (see the module docstring's "Stale
# in-flight ghosts" section).
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconcile_finishes_stale_in_flight_ghosts_older_than_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A BT entry dispatched well before the cutoff, still unlanded and non-terminal,
    is force-finished as '中斷' (not '下載完成'/'失敗' — the real outcome is unknown)."""
    monkeypatch.setenv('ANIGAMERPLUS_BT_STALE_GHOST_HOURS', '1')
    entry_id = 21
    sn = 1000 + entry_id
    old_dispatch = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=8)
    bt_repo = FakeBtFeedEntryRepo(
        stale_candidates=[
            _FakeBtRow(entry_id, putio_status='IN_QUEUE', title='StuckAtQueue.mp4', dispatched_at=old_dispatch)
        ]
    )
    bt_progress_bus = ProgressBus()
    redis_reader = FakeRedisProgressReader({sn: _live_entry(sn, finished=False)})

    reconciler = _make_reconciler(bt_repo=bt_repo, bt_progress_bus=bt_progress_bus, redis_progress_reader=redis_reader)
    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert bt_fixed == 1
    assert tg_fixed == 0
    assert bt_repo.stale_ghost_calls == [1]
    snap = bt_progress_bus.snapshot()
    assert snap[sn].status == '中斷'
    assert snap[sn].finished_at is not None
    assert snap[sn].filename == 'StuckAtQueue.mp4'


@pytest.mark.anyio
async def test_reconcile_leaves_fresh_in_flight_rows_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A BT entry dispatched moments ago (within the cutoff window) is merely
    slow, not stuck — the reconciler must not touch it."""
    monkeypatch.setenv('ANIGAMERPLUS_BT_STALE_GHOST_HOURS', '1')
    entry_id = 22
    sn = 1000 + entry_id
    recent_dispatch = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
    bt_repo = FakeBtFeedEntryRepo(
        stale_candidates=[
            _FakeBtRow(entry_id, putio_status='IN_QUEUE', title='StillGoing.mp4', dispatched_at=recent_dispatch)
        ]
    )
    bt_progress_bus = ProgressBus()
    redis_reader = FakeRedisProgressReader({sn: _live_entry(sn, finished=False)})

    reconciler = _make_reconciler(bt_repo=bt_repo, bt_progress_bus=bt_progress_bus, redis_progress_reader=redis_reader)
    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert bt_fixed == 0
    assert tg_fixed == 0
    assert bt_repo.stale_ghost_calls == [1]
    # Nothing was force-finished — the live (non-terminal) entry is untouched.
    assert bt_progress_bus.snapshot() == {}


@pytest.mark.anyio
async def test_reconcile_finishes_stale_tg_in_progress_rows_via_task_history_repo() -> None:
    """A TG download started long ago with no landed row yet (tg_downloaded_media
    cannot represent an in-flight download — see TaskHistoryRepository.list_stale_in_progress's
    docstring) is force-finished as '中斷' via the task_history-backed sweep."""
    sn = 777
    old_start = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=5)
    task_history_repo = FakeTaskHistoryRepo(
        rows=[_FakeHistoryRow(sn, filename='stuck-download.mkv', started_at=old_start)]
    )
    progress_bus = ProgressBus()
    redis_reader = FakeRedisProgressReader({sn: _live_entry(sn, finished=False)})

    reconciler = _make_reconciler(
        progress_bus=progress_bus,
        task_history_repo=task_history_repo,
        redis_progress_reader=redis_reader,
    )
    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert bt_fixed == 0
    assert tg_fixed == 1
    assert task_history_repo.stale_calls == [('tg', 1)]
    snap = progress_bus.snapshot()
    assert snap[sn].status == '中斷'
    assert snap[sn].finished_at is not None
    assert snap[sn].filename == 'stuck-download.mkv'


@pytest.mark.anyio
async def test_reconcile_tg_stale_sweep_is_a_noop_when_task_history_repo_not_wired() -> None:
    """Backward compatibility: task_history_repo is an optional constructor arg —
    omitting it (as every pre-existing caller in this test file does) must not error,
    it just skips the TG stale-ghost sweep."""
    redis_reader = FakeRedisProgressReader({777: _live_entry(777, finished=False)})

    reconciler = _make_reconciler(redis_progress_reader=redis_reader)
    bt_fixed, tg_fixed = await reconciler.reconcile_on_boot()

    assert (bt_fixed, tg_fixed) == (0, 0)


@pytest.mark.anyio
async def test_reconcile_stale_ghost_cutoff_env_var_default_is_one_hour() -> None:
    """With no ANIGAMERPLUS_BT_STALE_GHOST_HOURS set, the reconciler asks the
    repo for a 1-hour cutoff by default."""
    bt_repo = FakeBtFeedEntryRepo()
    redis_reader = FakeRedisProgressReader({})

    reconciler = _make_reconciler(bt_repo=bt_repo, redis_progress_reader=redis_reader)
    await reconciler.reconcile_on_boot()

    assert bt_repo.stale_ghost_calls == [1]


@pytest.mark.anyio
async def test_reconcile_stale_ghost_cutoff_env_var_is_clamped_to_24(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator-set value above the documented 0-24 range is clamped, not passed through raw."""
    monkeypatch.setenv('ANIGAMERPLUS_BT_STALE_GHOST_HOURS', '999')
    bt_repo = FakeBtFeedEntryRepo()
    redis_reader = FakeRedisProgressReader({})

    reconciler = _make_reconciler(bt_repo=bt_repo, redis_progress_reader=redis_reader)
    await reconciler.reconcile_on_boot()

    assert bt_repo.stale_ghost_calls == [24]


@pytest.mark.anyio
async def test_reconcile_stale_ghost_log_line_breaks_out_stale_count_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boot-summary log line reports landed/terminal fixes and stale-ghost
    fixes as separate counts, per the '... 已完成任務 + Z 個 stale 中斷任務' format."""
    monkeypatch.setenv('ANIGAMERPLUS_BT_STALE_GHOST_HOURS', '1')
    entry_id = 23
    sn = 1000 + entry_id
    old_dispatch = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)
    bt_repo = FakeBtFeedEntryRepo(
        stale_candidates=[_FakeBtRow(entry_id, putio_status='IN_QUEUE', dispatched_at=old_dispatch)]
    )
    redis_reader = FakeRedisProgressReader({sn: _live_entry(sn, finished=False)})
    logger = FakeLogger()

    reconciler = _make_reconciler(bt_repo=bt_repo, redis_progress_reader=redis_reader, logger=logger)
    await reconciler.reconcile_on_boot()

    assert len(logger.info_calls) == 1
    _sn, tag, detail = logger.info_calls[0]
    assert tag == 'BT/TG啟動對帳'
    assert '0 個 BT' in detail
    assert '0 個 TG' in detail
    assert '1 個 stale' in detail

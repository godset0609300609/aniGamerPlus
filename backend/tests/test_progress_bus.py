"""Tests for the :class:`ProgressBus`."""

from __future__ import annotations

import datetime
import threading

from app.downloader.progress import ProgressBus, TaskProgress, get_progress_bus


def test_start_adds_entry_with_waiting_status() -> None:
    bus = ProgressBus()
    bus.start(100, 'EP01.mp4')
    snap = bus.snapshot()
    assert 100 in snap
    assert snap[100].status == '等待下載'
    assert snap[100].rate == 0.0
    assert snap[100].filename == 'EP01.mp4'


def test_update_rate_and_status_mutate_only_that_field() -> None:
    bus = ProgressBus()
    bus.start(200, 'EP02.mp4', status='正在下載')
    bus.update_rate(200, 42.5)
    bus.update_status(200, '正在解密合并')

    snap = bus.snapshot()
    assert snap[200].rate == 42.5
    assert snap[200].status == '正在解密合并'
    assert snap[200].filename == 'EP02.mp4'


def test_finish_keeps_entry_with_finished_at() -> None:
    """finish() must retain the entry and stamp finished_at (not delete it)."""
    bus = ProgressBus()
    bus.start(300, 'EP03.mp4')
    before = datetime.datetime.now(datetime.UTC)
    bus.finish(300)
    after = datetime.datetime.now(datetime.UTC)

    snap = bus.snapshot()
    assert 300 in snap
    entry = snap[300]
    assert entry.finished_at is not None
    assert entry.finished_at.tzinfo is not None
    assert before <= entry.finished_at <= after


def test_snapshot_is_decoupled_from_internal_state() -> None:
    bus = ProgressBus()
    bus.start(400, 'EP04.mp4')
    snap = bus.snapshot()
    # Mutating the returned dict or values must NOT affect the bus.
    snap[400].rate = 999.0
    snap[999] = TaskProgress(sn=999, rate=1.0, status='x', filename='y')
    del snap[400]

    fresh = bus.snapshot()
    assert 400 in fresh
    assert fresh[400].rate == 0.0
    assert 999 not in fresh


def test_concurrent_updates_produce_consistent_snapshot() -> None:
    bus = ProgressBus()
    # Seed 50 entries so update_rate has something to hit.
    for sn in range(50):
        bus.start(sn, f'EP{sn:02d}.mp4')

    def _hammer(sn: int) -> None:
        for i in range(20):
            bus.update_rate(sn, float(i))

    threads = [threading.Thread(target=_hammer, args=(sn,)) for sn in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = bus.snapshot()
    # No missing entries, no partials — all 50 still present, each with a
    # valid final rate (the last _hammer call wrote 19.0 but interleaving
    # could mean other threads' writes were the "last" — any float 0..19 is
    # acceptable).
    assert len(snap) == 50
    for sn in range(50):
        assert sn in snap
        assert 0.0 <= snap[sn].rate <= 19.0


def test_get_progress_bus_returns_singleton() -> None:
    assert get_progress_bus() is get_progress_bus()


# ---------------------------------------------------------------------------
# New tests for Batch G expanded fields
# ---------------------------------------------------------------------------


def test_start_with_bangumi_metadata() -> None:
    """start() with bangumi_name/episode/resolution propagates to snapshot."""
    bus = ProgressBus()
    bus.start(
        500,
        'EP05.mp4',
        bangumi_name='進擊的巨人',
        episode='第01話',
        resolution='1080p',
    )
    snap = bus.snapshot()
    assert 500 in snap
    entry = snap[500]
    assert entry.bangumi_name == '進擊的巨人'
    assert entry.episode == '第01話'
    assert entry.resolution == '1080p'
    assert entry.speed_mbps is None
    assert entry.eta_seconds is None
    assert entry.retries == 0


def test_update_stats_atomic() -> None:
    """update_stats updates only the fields given; omitted args are unchanged."""
    bus = ProgressBus()
    bus.start(600, 'EP06.mp4')

    # First call: set speed + eta.
    bus.update_stats(600, speed_mbps=3.2, eta_seconds=120)
    snap = bus.snapshot()
    assert snap[600].speed_mbps == 3.2
    assert snap[600].eta_seconds == 120
    assert snap[600].rate == 0.0  # untouched

    # Second call: update only rate — speed/eta must be preserved.
    bus.update_stats(600, rate=55.0)
    snap2 = bus.snapshot()
    assert snap2[600].rate == 55.0
    assert snap2[600].speed_mbps == 3.2  # unchanged
    assert snap2[600].eta_seconds == 120  # unchanged

    # Third call: update only speed_mbps — eta/rate unchanged.
    bus.update_stats(600, speed_mbps=5.0)
    snap3 = bus.snapshot()
    assert snap3[600].speed_mbps == 5.0
    assert snap3[600].eta_seconds == 120
    assert snap3[600].rate == 55.0


def test_mark_retry_increments_and_sets_status() -> None:
    """mark_retry increments the retry counter and sets a visible failure status."""
    bus = ProgressBus()
    bus.start(700, 'EP07.mp4', status='正在下載')

    bus.mark_retry(700)
    snap = bus.snapshot()
    assert snap[700].retries == 1
    assert snap[700].status == '失敗! 重啓中'

    # Second retry — cumulative.
    bus.mark_retry(700)
    snap2 = bus.snapshot()
    assert snap2[700].retries == 2
    assert snap2[700].status == '失敗! 重啓中'


def test_mark_retry_noop_on_missing_sn() -> None:
    """mark_retry on an unknown sn must not raise."""
    bus = ProgressBus()
    bus.mark_retry(999)  # should be silent no-op
    assert bus.snapshot() == {}


def test_started_at_set_on_start() -> None:
    """start() must populate started_at with a UTC-aware datetime."""
    before = datetime.datetime.now(datetime.UTC)
    bus = ProgressBus()
    bus.start(800, 'EP08.mp4')
    after = datetime.datetime.now(datetime.UTC)

    snap = bus.snapshot()
    started = snap[800].started_at
    assert started is not None
    assert started.tzinfo is not None  # must be timezone-aware
    assert before <= started <= after


def test_started_at_reset_on_second_start() -> None:
    """Calling start() a second time for the same sn resets started_at."""
    bus = ProgressBus()
    bus.start(900, 'EP09.mp4')
    first_started = bus.snapshot()[900].started_at

    bus.start(900, 'EP09.mp4')
    second_started = bus.snapshot()[900].started_at

    assert second_started is not None
    assert first_started is not None
    # Both are valid UTC timestamps; second must be >= first.
    assert second_started >= first_started


def test_update_resolution_sets_field() -> None:
    """update_resolution updates the resolution field on an existing entry."""
    bus = ProgressBus()
    bus.start(1000, 'EP10.mp4')
    assert bus.snapshot()[1000].resolution is None

    bus.update_resolution(1000, '1080p')
    assert bus.snapshot()[1000].resolution == '1080p'


def test_update_resolution_noop_on_missing_sn() -> None:
    """update_resolution on an unknown sn must not raise."""
    bus = ProgressBus()
    bus.update_resolution(999, '720p')  # silent no-op
    assert bus.snapshot() == {}


# ---------------------------------------------------------------------------
# Batch H — cancel event tests
# ---------------------------------------------------------------------------


def test_cancel_sets_event_and_finishes() -> None:
    """cancel() returns True, sets the event, marks status='已取消', and
    schedules finish() within ~1.5 seconds which stamps finished_at."""
    bus = ProgressBus()
    bus.start(1001, 'ep01.mp4', status='正在下載')

    event = bus.get_cancel_event(1001)
    assert event is not None
    assert not event.is_set()

    result = bus.cancel(1001)
    assert result is True

    # Status should be set immediately.
    snap = bus.snapshot()
    assert snap[1001].status == '已取消'

    # The event must be set.
    assert event.is_set()

    # finish() is scheduled via Timer(1.0, ...). Wait up to 2.5 s for
    # finished_at to be stamped on the entry.
    import time as _time

    deadline = datetime.datetime.now(datetime.UTC).timestamp() + 2.5
    while datetime.datetime.now(datetime.UTC).timestamp() < deadline:
        snap2 = bus.snapshot()
        if snap2.get(1001) is not None and snap2[1001].finished_at is not None:
            break
        _time.sleep(0.05)

    final_snap = bus.snapshot()
    assert 1001 in final_snap
    assert final_snap[1001].finished_at is not None


def test_cancel_returns_false_on_unknown_sn() -> None:
    """cancel() returns False when the sn is not being tracked."""
    bus = ProgressBus()
    assert bus.cancel(9999) is False


def test_get_cancel_event_returns_none_for_unknown() -> None:
    """get_cancel_event() returns None for a sn that is not tracked."""
    bus = ProgressBus()
    assert bus.get_cancel_event(8888) is None


def test_cancel_event_not_in_snapshot() -> None:
    """snapshot() must not expose _cancel_event — it should be None or absent."""
    bus = ProgressBus()
    bus.start(1002, 'ep02.mp4')
    snap = bus.snapshot()
    entry = snap[1002]
    # The _cancel_event should be stripped in the snapshot copy.
    assert entry._cancel_event is None


def test_get_cancel_event_returns_event_after_start() -> None:
    """get_cancel_event() returns the threading.Event created by start()."""
    bus = ProgressBus()
    bus.start(1003, 'ep03.mp4')
    event = bus.get_cancel_event(1003)
    assert isinstance(event, threading.Event)
    assert not event.is_set()


# ---------------------------------------------------------------------------
# Batch I — finish / pruning tests
# ---------------------------------------------------------------------------


def test_finish_noop_on_missing_sn() -> None:
    """finish() on an unknown sn must not raise."""
    bus = ProgressBus()
    bus.finish(9999)  # silent no-op
    assert bus.snapshot() == {}


def test_snapshot_includes_completed_entries() -> None:
    """snapshot() must include entries that have been finished (finished_at set)."""
    bus = ProgressBus()
    bus.start(2001, 'ep01.mp4', status='正在下載')
    bus.update_status(2001, '下載完成')
    bus.finish(2001)

    snap = bus.snapshot()
    assert 2001 in snap
    assert snap[2001].finished_at is not None
    assert snap[2001].status == '下載完成'


def test_prune_removes_entries_older_than_7d() -> None:
    """_prune_stale must delete entries whose finished_at is beyond 7 days."""
    from app.downloader.progress import _COMPLETION_TTL_SECONDS

    bus = ProgressBus()
    bus.start(2002, 'ep02.mp4')
    bus.finish(2002)

    # Backdate finished_at past the TTL threshold.
    expired_ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=_COMPLETION_TTL_SECONDS + 1)
    with bus._lock:
        bus._entries[2002].finished_at = expired_ts

    snap = bus.snapshot()
    assert 2002 not in snap


def test_prune_keeps_entries_within_7d() -> None:
    """Entries finished recently must survive _prune_stale."""
    bus = ProgressBus()
    bus.start(2003, 'ep03.mp4')
    bus.finish(2003)

    snap = bus.snapshot()
    assert 2003 in snap
    assert snap[2003].finished_at is not None


def test_prune_does_not_affect_active_entries() -> None:
    """Active entries (finished_at is None) must never be pruned."""
    bus = ProgressBus()
    bus.start(2004, 'ep04.mp4', status='正在下載')

    snap = bus.snapshot()
    assert 2004 in snap
    assert snap[2004].finished_at is None


# ---------------------------------------------------------------------------
# Bug (1) — duplicate DB insert prevention
# ---------------------------------------------------------------------------


def test_start_twice_does_not_double_insert_history() -> None:
    """A second start() call for the same sn while the DB row is still open
    must NOT call record_start again — only the in-memory state is updated.

    This exercises the fix for the duplicate ``(in_progress)`` row bug where
    ``_announce_waiting`` and ``Anime.download`` both called start() for the
    same sn, producing two DB rows.
    """
    # Use a fake repo to count record_start calls.
    import dataclasses as _dc
    import typing as _T

    @_dc.dataclass
    class _FakeRepo:
        start_calls: list[int] = _dc.field(default_factory=list)
        _next_id: int = 1

        def record_start(self, sn: int, filename: str, **_: _T.Any) -> int:
            self.start_calls.append(sn)
            rid = self._next_id
            self._next_id += 1
            return rid

        def record_finish(self, *_: _T.Any, **__: _T.Any) -> None:
            pass

        def mark_interrupted_on_boot(self) -> int:
            return 0

    fake_repo = _FakeRepo()
    bus = ProgressBus(history_repo=fake_repo)  # type: ignore[arg-type]

    # First call — announce waiting (like _announce_waiting in ManualRunner).
    bus.start(3001, '《番劇》', status='等待下載')
    assert len(fake_repo.start_calls) == 1

    # Second call — Anime.download starts the real pipeline.
    bus.start(3001, '《番劇 EP01》', status='正在解析')
    # Must still be only ONE record_start call.
    assert len(fake_repo.start_calls) == 1

    # In-memory entry reflects the updated status and filename.
    snap = bus.snapshot()
    assert snap[3001].status == '正在解析'
    assert snap[3001].filename == '《番劇 EP01》'


def test_update_metadata_merges_fields() -> None:
    """update_metadata must only overwrite fields whose argument is not None.

    Passing ``None`` for a field must leave the existing value unchanged.
    """
    bus = ProgressBus()
    bus.start(4001, 'ep01.mp4', bangumi_name='舊名', episode='01', resolution='720p')

    # Update only bangumi_name — episode and resolution must be preserved.
    bus.update_metadata(4001, bangumi_name='新名')
    snap = bus.snapshot()
    assert snap[4001].bangumi_name == '新名'
    assert snap[4001].episode == '01'  # unchanged
    assert snap[4001].resolution == '720p'  # unchanged

    # Update only episode — bangumi_name and resolution must be preserved.
    bus.update_metadata(4001, episode='02')
    snap2 = bus.snapshot()
    assert snap2[4001].bangumi_name == '新名'  # unchanged
    assert snap2[4001].episode == '02'
    assert snap2[4001].resolution == '720p'  # unchanged

    # Update filename — other fields preserved.
    bus.update_metadata(4001, filename='new_ep.mp4')
    snap3 = bus.snapshot()
    assert snap3[4001].filename == 'new_ep.mp4'
    assert snap3[4001].bangumi_name == '新名'  # unchanged


def test_update_metadata_noop_on_missing_sn() -> None:
    """update_metadata on an unknown sn must not raise."""
    bus = ProgressBus()
    bus.update_metadata(9999, bangumi_name='番劇')  # silent no-op
    assert bus.snapshot() == {}


def test_update_metadata_all_none_leaves_entry_unchanged() -> None:
    """Calling update_metadata with all-None arguments is a no-op on the entry."""
    bus = ProgressBus()
    bus.start(4002, 'ep02.mp4', bangumi_name='A', episode='01', resolution='1080p')
    before = bus.snapshot()[4002]

    bus.update_metadata(4002)  # all kwargs default to None

    after = bus.snapshot()[4002]
    assert after.bangumi_name == before.bangumi_name
    assert after.episode == before.episode
    assert after.resolution == before.resolution
    assert after.filename == before.filename


# ---------------------------------------------------------------------------
# Note: "start() after cancel/finish is ignored" behaviour has been removed.
# The cancel-reappear case is now handled at the worker layer:
# _download_one checks get_cancel_event() before calling _announce_waiting,
# so a zombie worker never reaches start() for a cancelled sn.
# start() itself unconditionally accepts re-submissions so that a user who
# manually queues the same sn after it has finished gets a fresh entry.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cooldown field tests
# ---------------------------------------------------------------------------


def test_set_cooldown_stores_future_timestamp() -> None:
    """set_cooldown stores a UTC-aware datetime in the future."""
    bus = ProgressBus()
    bus.start(5001, 'ep01.mp4')
    before = datetime.datetime.now(datetime.timezone.utc)
    bus.set_cooldown(5001, 30.0)
    after = datetime.datetime.now(datetime.timezone.utc)

    snap = bus.snapshot()
    ts = snap[5001].cooldown_until
    assert ts is not None
    assert ts.tzinfo is not None
    assert ts > before
    assert ts <= after + datetime.timedelta(seconds=30.0 + 1)  # generous upper bound


def test_clear_cooldown_removes_it() -> None:
    """clear_cooldown sets cooldown_until back to None."""
    bus = ProgressBus()
    bus.start(5002, 'ep02.mp4')
    bus.set_cooldown(5002, 10.0)
    assert bus.snapshot()[5002].cooldown_until is not None

    bus.clear_cooldown(5002)
    assert bus.snapshot()[5002].cooldown_until is None


def test_set_cooldown_noop_on_missing_sn() -> None:
    """set_cooldown on an unknown sn must not raise."""
    bus = ProgressBus()
    bus.set_cooldown(9998, 10.0)  # silent no-op
    assert bus.snapshot() == {}


def test_clear_cooldown_noop_on_missing_sn() -> None:
    """clear_cooldown on an unknown sn must not raise."""
    bus = ProgressBus()
    bus.clear_cooldown(9997)  # silent no-op
    assert bus.snapshot() == {}


def test_finished_entry_appears_in_snapshot_within_grace_period() -> None:
    """finish() keeps the entry in snapshot with status='下載完成' and finished_at set.

    This guards against the regression where the progress service would strip
    terminal-status entries before they reached the frontend, causing a visible
    delay until the 60-second DB history poll fired.
    """
    bus = ProgressBus()
    bus.start(9001, 'ep01.mp4', status='正在下載')
    bus.update_status(9001, '下載完成')
    before = datetime.datetime.now(datetime.UTC)
    bus.finish(9001)
    after = datetime.datetime.now(datetime.UTC)

    snap = bus.snapshot()
    # Entry must still be in the snapshot — not popped by finish().
    assert 9001 in snap, 'finished entry must remain in snapshot for grace period'
    entry = snap[9001]
    assert entry.status == '下載完成'
    assert entry.finished_at is not None
    assert before <= entry.finished_at <= after


def test_finish_is_idempotent() -> None:
    """Calling finish() twice for the same sn must not double-update the DB
    and must not change finished_at after the first call."""
    import dataclasses as _dc
    import typing as _T

    @_dc.dataclass
    class _FakeRepo:
        finish_calls: list[int] = _dc.field(default_factory=list)
        _next_id: int = 1

        def record_start(self, sn: int, filename: str, **_: _T.Any) -> int:
            rid = self._next_id
            self._next_id += 1
            return rid

        def record_finish(self, row_id: int, **_: _T.Any) -> None:
            self.finish_calls.append(row_id)

        def mark_interrupted_on_boot(self) -> int:
            return 0

    fake_repo = _FakeRepo()
    bus = ProgressBus(history_repo=fake_repo)  # type: ignore[arg-type]
    bus.start(3002, 'ep.mp4')
    bus.finish(3002)
    first_finished_at = bus.snapshot()[3002].finished_at

    # Second finish() — must be a no-op.
    bus.finish(3002)
    second_finished_at = bus.snapshot()[3002].finished_at

    assert len(fake_repo.finish_calls) == 1  # only one DB UPDATE
    assert first_finished_at == second_finished_at  # timestamp unchanged


# ---------------------------------------------------------------------------
# Batch J — start() resets transient progress fields (cases 1 & 2)
# ---------------------------------------------------------------------------


def test_start_case1_resets_rate_when_db_row_already_open() -> None:
    """Case 1: DB row still open → start() must zero rate/speed/eta/cooldown.

    This guards against the bug where a task card shows '正在解析' but the
    progress bar reads >0% because rate carried over from a previous attempt.
    """
    import dataclasses as _dc
    import typing as _T

    @_dc.dataclass
    class _FakeRepo:
        _next_id: int = 1

        def record_start(self, sn: int, filename: str, **_: _T.Any) -> int:
            rid = self._next_id
            self._next_id += 1
            return rid

        def record_finish(self, *_: _T.Any, **__: _T.Any) -> None:
            pass

        def mark_interrupted_on_boot(self) -> int:
            return 0

    fake_repo = _FakeRepo()
    bus = ProgressBus(history_repo=fake_repo)  # type: ignore[arg-type]

    # First start — opens a DB row.
    bus.start(6001, 'ep01.mp4', status='等待下載')
    # Simulate progress from a prior download attempt.
    bus.update_stats(6001, speed_mbps=5.0, eta_seconds=90, rate=50.0)
    bus.set_cooldown(6001, 30.0)
    assert bus.snapshot()[6001].rate == 50.0
    assert bus.snapshot()[6001].speed_mbps == 5.0
    assert bus.snapshot()[6001].eta_seconds == 90
    assert bus.snapshot()[6001].cooldown_until is not None

    # Second start() — hits Case 1 (DB row still open).
    bus.start(6001, 'ep01.mp4', status='正在解析')

    snap = bus.snapshot()
    entry = snap[6001]
    assert entry.rate == 0.0, 'rate must be reset to 0 on re-enter'
    assert entry.speed_mbps is None, 'speed_mbps must be cleared'
    assert entry.eta_seconds is None, 'eta_seconds must be cleared'
    assert entry.cooldown_until is None, 'cooldown_until must be cleared'
    assert entry.status == '正在解析'


def test_start_case2_resets_rate_when_in_memory_entry_active_without_db_row() -> None:
    """Case 2: active in-memory entry, no DB row → start() must zero transient fields."""
    # No history_repo wired → no DB rows ever open; Case 2 applies on second call.
    bus = ProgressBus()

    bus.start(6002, 'ep02.mp4', status='等待下載')
    bus.update_stats(6002, speed_mbps=3.0, eta_seconds=60, rate=25.0)
    bus.set_cooldown(6002, 15.0)
    assert bus.snapshot()[6002].rate == 25.0

    # Second start() — hits Case 2 (active entry, no DB row).
    bus.start(6002, 'ep02.mp4', status='正在解析')

    snap = bus.snapshot()
    entry = snap[6002]
    assert entry.rate == 0.0
    assert entry.speed_mbps is None
    assert entry.eta_seconds is None
    assert entry.cooldown_until is None
    assert entry.status == '正在解析'


def test_start_preserves_retries_across_case1() -> None:
    """retries must NOT be reset by a Case 1 re-enter — they are cumulative."""
    import dataclasses as _dc
    import typing as _T

    @_dc.dataclass
    class _FakeRepo:
        _next_id: int = 1

        def record_start(self, sn: int, filename: str, **_: _T.Any) -> int:
            rid = self._next_id
            self._next_id += 1
            return rid

        def record_finish(self, *_: _T.Any, **__: _T.Any) -> None:
            pass

        def mark_interrupted_on_boot(self) -> int:
            return 0

    fake_repo = _FakeRepo()
    bus = ProgressBus(history_repo=fake_repo)  # type: ignore[arg-type]

    bus.start(6003, 'ep03.mp4', status='等待下載')
    bus.mark_retry(6003)
    bus.mark_retry(6003)
    bus.mark_retry(6003)
    assert bus.snapshot()[6003].retries == 3

    # Re-enter via Case 1.
    bus.start(6003, 'ep03.mp4', status='正在解析')
    assert bus.snapshot()[6003].retries == 3, 'retries must be preserved across start()'


def test_start_rate_resets_repeatedly() -> None:
    """start() → update_rate(50) → start() → rate==0 (no regression)."""
    bus = ProgressBus()

    bus.start(6004, 'ep04.mp4')
    bus.update_rate(6004, 50.0)
    assert bus.snapshot()[6004].rate == 50.0

    # Second start — Case 2.
    bus.start(6004, 'ep04.mp4', status='正在解析')
    assert bus.snapshot()[6004].rate == 0.0

    bus.update_rate(6004, 75.0)
    assert bus.snapshot()[6004].rate == 75.0

    # Third start — Case 2 again.
    bus.start(6004, 'ep04.mp4', status='等待下載')
    assert bus.snapshot()[6004].rate == 0.0


def test_start_preserves_metadata_from_pre_parse() -> None:
    """bangumi_name/episode/resolution from pre-parse survive a subsequent start().

    A pre-parse thread calls update_metadata() to populate those fields.
    A later start() in the download pipeline passes None for those args,
    so the None-guard must prevent overwriting the pre-parse values.
    Only the transient progress fields should be reset.
    """
    bus = ProgressBus()

    bus.start(6005, 'ep05.mp4', status='等待下載')
    # Simulate pre-parse populating metadata.
    bus.update_metadata(6005, bangumi_name='進擊的巨人', episode='第01話', resolution='1080p')
    bus.update_rate(6005, 10.0)

    # Download pipeline calls start() — bangumi_name/episode/resolution are None.
    bus.start(6005, 'ep05.mp4', status='正在解析')

    snap = bus.snapshot()
    entry = snap[6005]
    # Progress fields reset.
    assert entry.rate == 0.0
    # Metadata preserved (args were None → None-guard kept existing values).
    assert entry.bangumi_name == '進擊的巨人'
    assert entry.episode == '第01話'
    assert entry.resolution == '1080p'

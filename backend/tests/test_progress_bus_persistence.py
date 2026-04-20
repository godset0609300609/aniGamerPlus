"""Tests verifying that ProgressBus correctly delegates to TaskHistoryRepository.

Uses a spy/fake repo rather than a real DB so the tests are fast and
deterministic.
"""

from __future__ import annotations

import dataclasses
import datetime
import typing as T

import pytest

from app.downloader.progress import ProgressBus

# ---------------------------------------------------------------------------
# Fake TaskHistoryRepository
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _StartCall:
    sn: int
    filename: str
    owner_id: str | None
    bangumi_name: str | None
    episode: str | None
    resolution: str | None
    started_at: datetime.datetime | None


@dataclasses.dataclass
class _FinishCall:
    row_id: int
    final_status: str
    finished_at: datetime.datetime
    retries: int
    bangumi_name: str | None = None
    episode: str | None = None
    resolution: str | None = None
    filename: str | None = None


class FakeHistoryRepo:
    """In-memory fake that captures every call to record_start / record_finish."""

    def __init__(self) -> None:
        self.start_calls: list[_StartCall] = []
        self.finish_calls: list[_FinishCall] = []
        self._next_id = 1

    def record_start(
        self,
        sn: int,
        filename: str,
        *,
        owner_id: str | None = None,
        bangumi_name: str | None = None,
        episode: str | None = None,
        resolution: str | None = None,
        started_at: datetime.datetime | None = None,
    ) -> int:
        self.start_calls.append(
            _StartCall(
                sn=sn,
                filename=filename,
                owner_id=owner_id,
                bangumi_name=bangumi_name,
                episode=episode,
                resolution=resolution,
                started_at=started_at,
            )
        )
        row_id = self._next_id
        self._next_id += 1
        return row_id

    def record_finish(
        self,
        row_id: int,
        *,
        final_status: str,
        finished_at: datetime.datetime,
        retries: int = 0,
        bangumi_name: str | None = None,
        episode: str | None = None,
        resolution: str | None = None,
        filename: str | None = None,
    ) -> None:
        self.finish_calls.append(
            _FinishCall(
                row_id=row_id,
                final_status=final_status,
                finished_at=finished_at,
                retries=retries,
                bangumi_name=bangumi_name,
                episode=episode,
                resolution=resolution,
                filename=filename,
            )
        )

    def mark_interrupted_on_boot(self) -> int:
        return 0

    def list_recent(self, days: int = 7, user_id: str | None = None) -> list[T.Any]:
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_repo() -> FakeHistoryRepo:
    return FakeHistoryRepo()


@pytest.fixture
def bus(fake_repo: FakeHistoryRepo) -> ProgressBus:
    return ProgressBus(history_repo=fake_repo)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests — start() persists to history_repo
# ---------------------------------------------------------------------------


def test_start_calls_record_start(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    bus.start(100, 'ep01.mp4')
    assert len(fake_repo.start_calls) == 1
    call = fake_repo.start_calls[0]
    assert call.sn == 100
    assert call.filename == 'ep01.mp4'


def test_start_propagates_metadata(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    bus.start(
        101,
        'ep02.mp4',
        owner_id='u1',
        bangumi_name='AoT',
        episode='第01話',
        resolution='1080p',
    )
    call = fake_repo.start_calls[0]
    assert call.owner_id == 'u1'
    assert call.bangumi_name == 'AoT'
    assert call.episode == '第01話'
    assert call.resolution == '1080p'


def test_start_stores_row_id_for_later_finish(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    bus.start(102, 'ep03.mp4')
    # The bus must hold the row_id for later use by finish().
    with bus._lock:
        assert 102 in bus._row_ids
        assert bus._row_ids[102] == 1  # first call → id=1


def test_start_started_at_is_utc_aware(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    before = datetime.datetime.now(datetime.UTC)
    bus.start(103, 'ep04.mp4')
    after = datetime.datetime.now(datetime.UTC)
    call = fake_repo.start_calls[0]
    assert call.started_at is not None
    assert call.started_at.tzinfo is not None
    assert before <= call.started_at <= after


# ---------------------------------------------------------------------------
# Tests — finish() persists to history_repo
# ---------------------------------------------------------------------------


def test_finish_calls_record_finish(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    bus.start(200, 'ep05.mp4')
    bus.update_status(200, '下載完成')
    before = datetime.datetime.now(datetime.UTC)
    bus.finish(200)
    after = datetime.datetime.now(datetime.UTC)

    assert len(fake_repo.finish_calls) == 1
    call = fake_repo.finish_calls[0]
    assert call.row_id == 1  # first start → row_id=1
    assert call.final_status == '下載完成'
    assert before <= call.finished_at <= after


def test_finish_passes_retries(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    bus.start(201, 'ep06.mp4')
    bus.mark_retry(201)
    bus.mark_retry(201)
    bus.finish(201)

    call = fake_repo.finish_calls[0]
    assert call.retries == 2


def test_finish_clears_row_id_from_internal_map(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    bus.start(202, 'ep07.mp4')
    bus.finish(202)
    with bus._lock:
        assert 202 not in bus._row_ids


def test_finish_noop_when_no_history_repo() -> None:
    """ProgressBus without a history_repo must not raise on finish()."""
    plain_bus = ProgressBus()
    plain_bus.start(300, 'ep08.mp4')
    plain_bus.finish(300)  # should not raise


def test_finish_noop_on_unknown_sn(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """finish() on an sn not tracked must be a silent no-op."""
    bus.finish(9999)
    assert len(fake_repo.finish_calls) == 0


# ---------------------------------------------------------------------------
# Tests — cancel() path also persists finish
# ---------------------------------------------------------------------------


def test_cancel_eventually_calls_record_finish(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """cancel() schedules finish() after 1 s; the finish must also persist."""
    import time

    bus.start(400, 'ep09.mp4')
    bus.cancel(400)

    # finish() is scheduled via a 1-second Timer; wait up to 2.5 s.
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        if fake_repo.finish_calls:
            break
        time.sleep(0.05)

    assert len(fake_repo.finish_calls) == 1
    assert fake_repo.finish_calls[0].final_status == '已取消'


# ---------------------------------------------------------------------------
# Tests — second start() for same sn gets a fresh row_id
# ---------------------------------------------------------------------------


def test_second_start_no_duplicate_insert_while_row_exists(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """A second start() for the same sn while the DB row is still open (sn in
    _row_ids) must NOT insert a second row — only update in-memory state."""
    bus.start(500, 'ep10.mp4')
    bus.start(500, 'ep10_v2.mp4', status='正在解析')  # same sn, still in-progress

    # Only one DB insert — the second call is in-memory only.
    assert len(fake_repo.start_calls) == 1
    with bus._lock:
        assert bus._row_ids[500] == 1  # still the original row_id

    # In-memory state reflects the second call.
    snap = bus.snapshot()
    assert snap[500].status == '正在解析'
    assert snap[500].filename == 'ep10_v2.mp4'


def test_second_start_after_finish_inserts_new_row(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """After finish() clears the row_id, a new start() for the same sn IS
    expected to insert a fresh DB row (genuine retry / second download)."""
    bus.start(501, 'ep11.mp4')
    bus.finish(501)

    # Now row_ids[501] has been popped by finish().
    bus.start(501, 'ep11_retry.mp4')

    # Two record_start calls — one before finish, one after.
    assert len(fake_repo.start_calls) == 2
    with bus._lock:
        assert bus._row_ids[501] == 2  # second id


# ---------------------------------------------------------------------------
# Bug (1) fix — finish() normalises non-terminal status → '中斷'
# ---------------------------------------------------------------------------


def test_finish_normalizes_non_terminal_status_to_interrupted(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """If the in-memory status at finish() time is not a recognised terminal
    value (e.g. '正在解析', '正在下載'), it means the task died mid-flight.
    finish() must coerce it to '中斷' before writing to the DB so the history
    table always contains semantically correct final statuses.
    """
    bus.start(600, 'ep12.mp4', status='正在解析')
    bus.finish(600)

    assert len(fake_repo.finish_calls) == 1
    assert fake_repo.finish_calls[0].final_status == '中斷'

    # In-memory entry must also reflect the normalised status.
    snap = bus.snapshot()
    assert snap[600].status == '中斷'


def test_finish_normalizes_in_download_status_to_interrupted(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """'正在下載' mid-flight → finish() → DB final_status='中斷'."""
    bus.start(601, 'ep13.mp4', status='正在下載')
    # Simulate update_status as the downloader would call it.
    bus.update_status(601, '正在下載')
    bus.finish(601)

    assert fake_repo.finish_calls[0].final_status == '中斷'


def test_finish_preserves_already_terminal_download_complete(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """'下載完成' is a terminal status — finish() must NOT overwrite it."""
    bus.start(602, 'ep14.mp4')
    bus.update_status(602, '下載完成')
    bus.finish(602)

    assert fake_repo.finish_calls[0].final_status == '下載完成'
    snap = bus.snapshot()
    assert snap[602].status == '下載完成'


def test_finish_preserves_task_complete_status(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """'任務完成' (e.g. after upload) is a terminal status — must not be changed."""
    bus.start(603, 'ep15.mp4')
    bus.update_status(603, '任務完成')
    bus.finish(603)

    assert fake_repo.finish_calls[0].final_status == '任務完成'


def test_finish_preserves_cancelled_status(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """'已取消' is already a terminal status — finish() must preserve it as-is."""
    bus.start(604, 'ep16.mp4')
    bus.update_status(604, '已取消')
    bus.finish(604)

    assert fake_repo.finish_calls[0].final_status == '已取消'


def test_finish_preserves_interrupted_status(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """'中斷' is already a terminal status — finish() must preserve it as-is."""
    bus.start(605, 'ep17.mp4')
    bus.update_status(605, '中斷')
    bus.finish(605)

    assert fake_repo.finish_calls[0].final_status == '中斷'


def test_finish_normalizes_retry_waiting_status(bus: ProgressBus, fake_repo: FakeHistoryRepo) -> None:
    """'任務失敗, 等待重啓' is not a terminal status in ALREADY_TERMINAL;
    if finish() is called while this status is set (e.g. outer safety-net),
    it must be coerced to '中斷'."""
    bus.start(606, 'ep18.mp4')
    bus.update_status(606, '任務失敗, 等待重啓')
    bus.finish(606)

    assert fake_repo.finish_calls[0].final_status == '中斷'

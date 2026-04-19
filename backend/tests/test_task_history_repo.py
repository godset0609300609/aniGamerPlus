"""Tests for :class:`TaskHistoryRepository`.

Uses an in-memory SQLite database with migrations applied so every test
gets a clean schema without hitting the real ``aniGamer.db``.
"""

from __future__ import annotations

import datetime
import logging
import pathlib

import collections.abc

import pytest

from app.persistence.db import Database
from app.persistence.task_history_repo import TaskHistoryRepository, _IN_PROGRESS_SENTINEL


@pytest.fixture
def db(tmp_path: pathlib.Path) -> collections.abc.Iterator[Database]:
    """In-memory-backed database with baseline migrations applied.

    The engine is disposed on teardown so sqlite3 connection-finaliser
    warnings do not leak into pytest's unraisable-exception hook.
    """
    logger = logging.getLogger('test_task_history_repo')
    database = Database(f'sqlite:///{tmp_path / "test.db"}', logger)  # type: ignore[arg-type]
    database.run_baseline_migrations()
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def repo(db: Database) -> TaskHistoryRepository:
    return TaskHistoryRepository(db)


# ---------------------------------------------------------------------------
# record_start
# ---------------------------------------------------------------------------


def test_record_start_returns_auto_id(repo: TaskHistoryRepository) -> None:
    row_id = repo.record_start(sn=100, filename='ep01.mp4')
    assert isinstance(row_id, int)
    assert row_id > 0


def test_record_start_creates_in_progress_row(repo: TaskHistoryRepository) -> None:
    row_id = repo.record_start(sn=101, filename='ep02.mp4', owner_id='u1')
    # list_recent with no filter should find nothing (finished_at is None).
    entries = repo.list_recent(days=7)
    assert not any(e.id == row_id for e in entries)


def test_record_start_stores_metadata(repo: TaskHistoryRepository, db: Database) -> None:
    """record_start persists all optional metadata fields."""
    import sqlalchemy
    from app.persistence.models import TaskHistoryRow

    started = datetime.datetime(2026, 4, 18, 10, 0, 0, tzinfo=datetime.UTC)
    row_id = repo.record_start(
        sn=102,
        filename='ep03.mp4',
        owner_id='u2',
        bangumi_name='進擊的巨人',
        episode='第01話',
        resolution='1080p',
        started_at=started,
    )

    with db.session() as session:
        row = session.get(TaskHistoryRow, row_id)
        assert row is not None
        assert row.sn == 102
        assert row.owner_id == 'u2'
        assert row.bangumi_name == '進擊的巨人'
        assert row.episode == '第01話'
        assert row.resolution == '1080p'
        assert row.final_status == _IN_PROGRESS_SENTINEL
        assert row.started_at == started.isoformat()
        assert row.finished_at is None


# ---------------------------------------------------------------------------
# record_finish
# ---------------------------------------------------------------------------


def test_record_finish_updates_row(repo: TaskHistoryRepository, db: Database) -> None:
    from app.persistence.models import TaskHistoryRow

    row_id = repo.record_start(sn=200, filename='ep04.mp4')
    finished = datetime.datetime(2026, 4, 18, 12, 0, 0, tzinfo=datetime.UTC)
    repo.record_finish(row_id, final_status='下載完成', finished_at=finished, retries=2)

    with db.session() as session:
        row = session.get(TaskHistoryRow, row_id)
        assert row is not None
        assert row.final_status == '下載完成'
        assert row.finished_at == finished.isoformat()
        assert row.retries == 2


def test_record_finish_noop_on_missing_id(repo: TaskHistoryRepository) -> None:
    """record_finish with an unknown id must not raise."""
    finished = datetime.datetime.now(datetime.UTC)
    repo.record_finish(9999, final_status='下載完成', finished_at=finished)


def test_record_finish_updates_metadata(repo: TaskHistoryRepository, db: Database) -> None:
    """record_finish must overwrite bangumi_name/episode/resolution/filename even
    if they were absent at record_start time (e.g. parse not yet done)."""
    from app.persistence.models import TaskHistoryRow

    # Simulate: record_start called before metadata is available.
    row_id = repo.record_start(sn=900, filename='sn900.mp4', bangumi_name=None)

    finished = datetime.datetime(2026, 4, 18, 15, 0, 0, tzinfo=datetime.UTC)
    repo.record_finish(
        row_id,
        final_status='下載完成',
        finished_at=finished,
        retries=0,
        bangumi_name='進擊的巨人',
        episode='第01話',
        resolution='1080p',
        filename='進擊的巨人 [01][1080p].mp4',
    )

    # list_recent should now return the row with resolved metadata.
    entries = repo.list_recent(days=7)
    match = next((e for e in entries if e.sn == 900), None)
    assert match is not None, 'finished row must appear in list_recent'
    assert match.bangumi_name == '進擊的巨人'
    assert match.episode == '第01話'
    assert match.resolution == '1080p'
    assert match.filename == '進擊的巨人 [01][1080p].mp4'

    # Verify via ORM directly as well.
    with db.session() as session:
        row = session.get(TaskHistoryRow, row_id)
        assert row is not None
        assert row.bangumi_name == '進擊的巨人'
        assert row.episode == '第01話'


# ---------------------------------------------------------------------------
# list_recent
# ---------------------------------------------------------------------------


def _seed(
    repo: TaskHistoryRepository,
    sn: int,
    final_status: str = '下載完成',
    days_ago: float = 1.0,
    owner_id: str | None = None,
) -> int:
    """Insert a completed row with finished_at = now - days_ago."""
    row_id = repo.record_start(sn=sn, filename=f'ep{sn}.mp4', owner_id=owner_id)
    finished = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)
    repo.record_finish(row_id, final_status=final_status, finished_at=finished)
    return row_id


def test_list_recent_returns_completed_rows(repo: TaskHistoryRepository) -> None:
    _seed(repo, sn=300, days_ago=1.0)
    entries = repo.list_recent(days=7)
    assert len(entries) == 1
    assert entries[0].sn == 300


def test_list_recent_excludes_old_rows(repo: TaskHistoryRepository) -> None:
    _seed(repo, sn=301, days_ago=8.0)  # older than 7 days
    _seed(repo, sn=302, days_ago=1.0)  # within 7 days
    entries = repo.list_recent(days=7)
    sns = {e.sn for e in entries}
    assert 301 not in sns
    assert 302 in sns


def test_list_recent_excludes_in_progress_rows(repo: TaskHistoryRepository) -> None:
    """Rows with NULL finished_at must not appear in list_recent."""
    repo.record_start(sn=303, filename='ep303.mp4')  # not finished
    entries = repo.list_recent(days=7)
    assert not any(e.sn == 303 for e in entries)


def test_list_recent_filters_by_user_id(repo: TaskHistoryRepository) -> None:
    _seed(repo, sn=304, owner_id='alice')
    _seed(repo, sn=305, owner_id='bob')
    alice_entries = repo.list_recent(days=7, user_id='alice')
    assert all(e.owner_id == 'alice' for e in alice_entries)
    assert not any(e.sn == 305 for e in alice_entries)


def test_list_recent_no_filter_returns_all_users(repo: TaskHistoryRepository) -> None:
    _seed(repo, sn=306, owner_id='alice')
    _seed(repo, sn=307, owner_id='bob')
    entries = repo.list_recent(days=7)
    sns = {e.sn for e in entries}
    assert 306 in sns
    assert 307 in sns


def test_list_recent_excludes_cancelled(repo: TaskHistoryRepository) -> None:
    """'已取消' rows must not be returned by list_recent (audit-only)."""
    _seed(repo, sn=310, final_status='已取消', days_ago=1.0)
    _seed(repo, sn=311, final_status='下載完成', days_ago=1.0)
    entries = repo.list_recent(days=7)
    sns = {e.sn for e in entries}
    assert 310 not in sns, "'已取消' row must be hidden from list_recent"
    assert 311 in sns, "'下載完成' row must still be returned"


def test_list_recent_excludes_cancelled_and_interrupted(
    repo: TaskHistoryRepository,
) -> None:
    """'已取消' and '中斷' rows must both be hidden from list_recent (audit-only).

    Only rows with genuinely completed statuses (e.g. '下載完成', '任務完成')
    should be returned.
    """
    _seed(repo, sn=320, final_status='下載完成', days_ago=1.0)
    _seed(repo, sn=321, final_status='任務完成', days_ago=1.0)
    _seed(repo, sn=322, final_status='已取消', days_ago=1.0)
    _seed(repo, sn=323, final_status='中斷', days_ago=1.0)

    entries = repo.list_recent(days=7)
    sns = {e.sn for e in entries}

    assert 320 in sns, "'下載完成' must be returned"
    assert 321 in sns, "'任務完成' must be returned"
    assert 322 not in sns, "'已取消' must be hidden from list_recent"
    assert 323 not in sns, "'中斷' must be hidden from list_recent"


def test_list_recent_ordered_by_finished_at_desc(repo: TaskHistoryRepository) -> None:
    _seed(repo, sn=400, days_ago=3.0)
    _seed(repo, sn=401, days_ago=1.0)  # more recent
    _seed(repo, sn=402, days_ago=2.0)
    entries = repo.list_recent(days=7)
    # Most recent should come first.
    assert entries[0].sn == 401


def test_list_recent_returns_parsed_datetimes(repo: TaskHistoryRepository) -> None:
    """finished_at and started_at must be parsed back to UTC-aware datetimes."""
    started = datetime.datetime(2026, 4, 17, 0, 0, 0, tzinfo=datetime.UTC)
    row_id = repo.record_start(sn=500, filename='ep500.mp4', started_at=started)
    finished = datetime.datetime(2026, 4, 18, 0, 0, 0, tzinfo=datetime.UTC)
    repo.record_finish(row_id, final_status='任務完成', finished_at=finished)

    entries = repo.list_recent(days=7)
    assert len(entries) == 1
    e = entries[0]
    assert e.started_at is not None
    assert e.started_at.tzinfo is not None
    assert e.finished_at is not None
    assert e.finished_at.tzinfo is not None


# ---------------------------------------------------------------------------
# mark_interrupted_on_boot
# ---------------------------------------------------------------------------


def test_mark_interrupted_on_boot_updates_in_progress_rows(repo: TaskHistoryRepository, db: Database) -> None:
    from app.persistence.models import TaskHistoryRow

    row_id = repo.record_start(sn=600, filename='ep600.mp4')
    count = repo.mark_interrupted_on_boot()
    assert count == 1

    with db.session() as session:
        row = session.get(TaskHistoryRow, row_id)
        assert row is not None
        assert row.final_status == '中斷'
        assert row.finished_at is not None


def test_mark_interrupted_on_boot_ignores_completed_rows(
    repo: TaskHistoryRepository,
) -> None:
    _seed(repo, sn=601, final_status='下載完成')
    count = repo.mark_interrupted_on_boot()
    assert count == 0


def test_mark_interrupted_on_boot_returns_zero_when_nothing_pending(
    repo: TaskHistoryRepository,
) -> None:
    count = repo.mark_interrupted_on_boot()
    assert count == 0


def test_mark_interrupted_on_boot_multiple_rows(repo: TaskHistoryRepository, db: Database) -> None:
    from app.persistence.models import TaskHistoryRow

    for sn in range(700, 705):
        repo.record_start(sn=sn, filename=f'ep{sn}.mp4')
    count = repo.mark_interrupted_on_boot()
    assert count == 5

    # Verify DB directly — list_recent filters out '中斷' rows (UI-only view),
    # so we query the ORM model directly to confirm the UPDATE was applied.
    with db.session() as session:
        rows = session.query(TaskHistoryRow).filter(TaskHistoryRow.final_status == '中斷').all()
        assert len(rows) == 5


# ---------------------------------------------------------------------------
# Migration downgrade/upgrade round-trip
# ---------------------------------------------------------------------------


def test_migration_0005_table_exists(db: Database) -> None:
    """The task_history table must be present after migrations run."""
    import sqlalchemy

    with db.session() as session:
        result = session.execute(
            sqlalchemy.text("SELECT name FROM sqlite_master WHERE type='table' AND name='task_history'")
        )
        row = result.fetchone()
    assert row is not None, 'task_history table was not created by migration 0005'


# ---------------------------------------------------------------------------
# normalize_legacy_statuses
# ---------------------------------------------------------------------------


def test_normalize_legacy_statuses_coerces_bogus_entries(repo: TaskHistoryRepository, db: Database) -> None:
    """Rows with non-terminal final_status values (e.g. '正在解析') that were
    written by old buggy code must be coerced to '中斷' by
    normalize_legacy_statuses()."""
    from app.persistence.models import TaskHistoryRow

    # Seed bogus rows by inserting directly via the ORM (bypassing the
    # record_start / record_finish API which would not produce these values).
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    bogus_statuses = ['正在解析', '正在下載', '等待下載', '失敗! 重啓中']
    bogus_ids: list[int] = []
    with db.session() as session:
        for idx, status in enumerate(bogus_statuses):
            row = TaskHistoryRow(
                sn=9000 + idx,
                owner_id=None,
                filename='ghost.mp4',
                bangumi_name=None,
                episode=None,
                resolution=None,
                final_status=status,
                started_at=now_iso,
                finished_at=now_iso,  # already closed, but with bogus status
                retries=0,
            )
            session.add(row)
            session.flush()
            bogus_ids.append(row.id)

    count = repo.normalize_legacy_statuses()
    assert count == len(bogus_statuses), f'Expected {len(bogus_statuses)} rows updated, got {count}'

    with db.session() as session:
        for row_id in bogus_ids:
            row = session.get(TaskHistoryRow, row_id)
            assert row is not None
            assert row.final_status == '中斷', f'row {row_id} still has bogus status after normalize'


def test_normalize_legacy_statuses_leaves_terminal_rows_untouched(
    repo: TaskHistoryRepository,
) -> None:
    """Rows that already have a recognised terminal final_status must not be
    touched by normalize_legacy_statuses()."""
    good_statuses = ['下載完成', '任務完成', '已取消', '中斷', '失敗']
    for i, status in enumerate(good_statuses):
        _seed(repo, sn=9100 + i, final_status=status, days_ago=1.0)

    count = repo.normalize_legacy_statuses()
    assert count == 0, 'No rows should be updated when all statuses are already terminal'


def test_normalize_legacy_statuses_ignores_in_progress_rows(
    repo: TaskHistoryRepository,
) -> None:
    """In-progress rows (finished_at IS NULL, final_status = '(in_progress)')
    must be left alone by normalize_legacy_statuses(); they are the
    responsibility of mark_interrupted_on_boot()."""
    repo.record_start(sn=9200, filename='ep_in_progress.mp4')  # not finished

    count = repo.normalize_legacy_statuses()
    assert count == 0


def test_normalize_legacy_statuses_returns_zero_when_nothing_to_fix(
    repo: TaskHistoryRepository,
) -> None:
    """Returns 0 when there are no rows at all or all are already clean."""
    count = repo.normalize_legacy_statuses()
    assert count == 0


def test_normalize_legacy_statuses_is_idempotent(
    repo: TaskHistoryRepository,
) -> None:
    """Calling normalize_legacy_statuses() twice must not change counts on
    the second call (all rows are already normalised after the first call)."""
    from app.persistence.models import TaskHistoryRow

    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    with db_from_repo(repo).session() as session:
        row = TaskHistoryRow(
            sn=9300,
            owner_id=None,
            filename='ghost2.mp4',
            bangumi_name=None,
            episode=None,
            resolution=None,
            final_status='正在下載',
            started_at=now_iso,
            finished_at=now_iso,
            retries=0,
        )
        session.add(row)

    first = repo.normalize_legacy_statuses()
    assert first == 1

    second = repo.normalize_legacy_statuses()
    assert second == 0


def db_from_repo(repo: TaskHistoryRepository) -> 'Database':
    """Extract the Database from a repository (white-box helper for tests only)."""
    return repo._db  # type: ignore[attr-defined]

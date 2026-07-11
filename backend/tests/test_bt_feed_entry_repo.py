"""Tests for BtFeedEntryRepository."""

from __future__ import annotations

import collections.abc
import datetime
import pathlib

import pytest
import sqlalchemy

from app.logging_ import Logger
from app.models import BtFeedCreate
from app.persistence.bt_feed_entry_repo import BtFeedEntryRepository
from app.persistence.bt_feed_repo import BtFeedRepository
from app.persistence.db import Database
from app.persistence.models import BtFeedEntryRow
from app.persistence.paths import WorkspacePaths


@pytest.fixture
def db(tmp_path: pathlib.Path) -> collections.abc.Iterator[Database]:
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)
    database = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    database.run_baseline_migrations()
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def feed_id(db: Database) -> int:
    feed_repo = BtFeedRepository(db)
    feed = feed_repo.create(BtFeedCreate(name='dmhy', url='https://dmhy.example/rss'))
    assert feed.id is not None
    return feed.id


@pytest.fixture
def repo(db: Database) -> BtFeedEntryRepository:
    return BtFeedEntryRepository(db)


# ---------------------------------------------------------------------------
# insert_if_new
# ---------------------------------------------------------------------------


def test_insert_if_new_returns_the_persisted_entry(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Some Title', 'https://link/1', 'AuthorName', '2026-07-01')
    assert entry is not None
    assert entry.id is not None
    assert entry.feed_id == feed_id
    assert entry.guid == 'guid-1'
    assert entry.title == 'Some Title'
    assert entry.link == 'https://link/1'
    assert entry.author == 'AuthorName'
    assert entry.published_at == '2026-07-01'
    assert entry.fetched_at
    assert entry.matched_filter_id is None
    assert entry.putio_transfer_id is None
    assert entry.local_path is None


def test_insert_if_new_dedupes_on_feed_id_and_guid(repo: BtFeedEntryRepository, feed_id: int) -> None:
    first = repo.insert_if_new(feed_id, 'guid-1', 'Title A', 'https://link/1')
    assert first is not None
    duplicate = repo.insert_if_new(feed_id, 'guid-1', 'Title A (updated)', 'https://link/1-updated')
    assert duplicate is None


def test_insert_if_new_allows_same_guid_across_different_feeds(
    repo: BtFeedEntryRepository, db: Database, feed_id: int
) -> None:
    feed_repo = BtFeedRepository(db)
    other_feed = feed_repo.create(BtFeedCreate(name='other', url='https://other.example/rss'))
    assert other_feed.id is not None

    first = repo.insert_if_new(feed_id, 'shared-guid', 'A', 'https://link/a')
    second = repo.insert_if_new(other_feed.id, 'shared-guid', 'B', 'https://link/b')
    assert first is not None
    assert second is not None
    assert first.id != second.id


def test_insert_if_new_allows_different_guids_same_feed(repo: BtFeedEntryRepository, feed_id: int) -> None:
    first = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    second = repo.insert_if_new(feed_id, 'guid-2', 'B', 'https://link/b')
    assert first is not None
    assert second is not None
    assert first.id != second.id


# ---------------------------------------------------------------------------
# list_recent
# ---------------------------------------------------------------------------


def test_list_recent_returns_inserted_entries(repo: BtFeedEntryRepository, feed_id: int) -> None:
    repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    repo.insert_if_new(feed_id, 'guid-2', 'B', 'https://link/b')
    result = repo.list_recent(days=7)
    assert {e.guid for e in result} == {'guid-1', 'guid-2'}


def test_list_recent_excludes_entries_older_than_cutoff(
    repo: BtFeedEntryRepository, db: Database, feed_id: int
) -> None:
    import datetime

    entry = repo.insert_if_new(feed_id, 'guid-old', 'Old', 'https://link/old')
    assert entry is not None
    stale = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)).isoformat()
    _set_fetched_at(db, entry.id, stale)

    assert repo.list_recent(days=7) == []


def test_list_recent_filters_by_filter_id_when_provided(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry_a = repo.insert_if_new(feed_id, 'guid-a', 'A', 'https://link/a')
    entry_b = repo.insert_if_new(feed_id, 'guid-b', 'B', 'https://link/b')
    assert entry_a is not None
    assert entry_b is not None
    repo.mark_dispatched(entry_a.id, filter_id=1, transfer_id=100)
    repo.mark_dispatched(entry_b.id, filter_id=2, transfer_id=200)

    result = repo.list_recent(days=7, filter_id=1)
    assert [e.id for e in result] == [entry_a.id]


def test_list_recent_no_filter_returns_all(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry_a = repo.insert_if_new(feed_id, 'guid-a', 'A', 'https://link/a')
    entry_b = repo.insert_if_new(feed_id, 'guid-b', 'B', 'https://link/b')
    assert entry_a is not None
    assert entry_b is not None
    repo.mark_dispatched(entry_a.id, filter_id=1, transfer_id=100)

    result = repo.list_recent(days=7)
    assert {e.id for e in result} == {entry_a.id, entry_b.id}


# ---------------------------------------------------------------------------
# list_paginated
# ---------------------------------------------------------------------------


def test_list_paginated_default_page_size_returns_first_50(
    repo: BtFeedEntryRepository, db: Database, feed_id: int
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    for i in range(60):
        entry = repo.insert_if_new(feed_id, f'guid-{i}', f'Title {i:02d}', f'https://link/{i}')
        assert entry is not None
        _set_fetched_at(db, entry.id, (now - datetime.timedelta(minutes=60 - i)).isoformat())

    items, total = repo.list_paginated(days=1)
    assert total == 60
    assert len(items) == 50


def test_list_paginated_page_2_skips_first_50(repo: BtFeedEntryRepository, db: Database, feed_id: int) -> None:
    now = datetime.datetime.now(datetime.UTC)
    ids: list[int] = []
    for i in range(60):
        entry = repo.insert_if_new(feed_id, f'guid-{i}', f'Title {i:02d}', f'https://link/{i}')
        assert entry is not None
        ids.append(entry.id)
        _set_fetched_at(db, entry.id, (now - datetime.timedelta(minutes=60 - i)).isoformat())

    items, total = repo.list_paginated(days=1, page=2, size=50)
    assert total == 60
    # Newest-first: page 1 holds ids[59..10] (indices 10-59), page 2 holds ids[9..0].
    assert [e.id for e in items] == list(reversed(ids))[50:60]


def test_list_paginated_q_filter_ilike_substring_case_insensitive(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    repo.insert_if_new(feed_id, 'guid-1', 'Attack on Titan - 01', 'https://link/1')
    repo.insert_if_new(feed_id, 'guid-2', 'One Piece - 900', 'https://link/2')

    items, total = repo.list_paginated(days=7, q='ATTACK')
    assert total == 1
    assert [e.title for e in items] == ['Attack on Titan - 01']


def test_list_paginated_combines_filter_id_and_q(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry_a = repo.insert_if_new(feed_id, 'guid-a', 'Attack on Titan - 01', 'https://link/a')
    entry_b = repo.insert_if_new(feed_id, 'guid-b', 'Attack on Titan - 02', 'https://link/b')
    assert entry_a is not None
    assert entry_b is not None
    repo.mark_dispatched(entry_a.id, filter_id=1, transfer_id=100)
    repo.mark_dispatched(entry_b.id, filter_id=2, transfer_id=200)

    items, total = repo.list_paginated(days=7, filter_id=1, q='attack')
    assert total == 1
    assert [e.id for e in items] == [entry_a.id]


def test_list_paginated_total_reflects_where_clause_not_offset(repo: BtFeedEntryRepository, feed_id: int) -> None:
    for i in range(5):
        repo.insert_if_new(feed_id, f'guid-{i}', f'Title {i:02d}', f'https://link/{i}')

    items, total = repo.list_paginated(days=7, page=3, size=2)
    assert total == 5
    assert len(items) == 1


def test_list_paginated_putio_status_filters_to_matching_rows(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry_a = repo.insert_if_new(feed_id, 'guid-a', 'Entry A', 'https://link/a')
    entry_b = repo.insert_if_new(feed_id, 'guid-b', 'Entry B', 'https://link/b')
    assert entry_a is not None
    assert entry_b is not None
    repo.mark_dispatched(entry_a.id, filter_id=1, transfer_id=100)
    repo.update_putio_status(entry_a.id, 'COMPLETED')
    repo.mark_dispatched(entry_b.id, filter_id=1, transfer_id=200)
    repo.update_putio_status(entry_b.id, 'DOWNLOADING')

    items, total = repo.list_paginated(days=7, putio_status='COMPLETED')
    assert total == 1
    assert [e.id for e in items] == [entry_a.id]


def test_list_paginated_unassigned_only_returns_entries_with_null_status(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    entry_a = repo.insert_if_new(feed_id, 'guid-a', 'Entry A', 'https://link/a')
    entry_b = repo.insert_if_new(feed_id, 'guid-b', 'Entry B', 'https://link/b')
    assert entry_a is not None
    assert entry_b is not None
    repo.mark_dispatched(entry_b.id, filter_id=1, transfer_id=200)

    items, total = repo.list_paginated(days=7, unassigned_only=True)
    assert total == 1
    assert [e.id for e in items] == [entry_a.id]


def test_list_paginated_unassigned_only_takes_precedence_over_putio_status(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    """``unassigned_only`` and ``putio_status`` are mutually exclusive; when both are
    passed, ``unassigned_only`` wins and ``putio_status`` is ignored."""
    entry_a = repo.insert_if_new(feed_id, 'guid-a', 'Entry A', 'https://link/a')
    entry_b = repo.insert_if_new(feed_id, 'guid-b', 'Entry B', 'https://link/b')
    assert entry_a is not None
    assert entry_b is not None
    repo.mark_dispatched(entry_b.id, filter_id=1, transfer_id=200)
    repo.update_putio_status(entry_b.id, 'COMPLETED')

    items, total = repo.list_paginated(days=7, unassigned_only=True, putio_status='COMPLETED')
    assert total == 1
    assert [e.id for e in items] == [entry_a.id]


# ---------------------------------------------------------------------------
# list_pending_landing
# ---------------------------------------------------------------------------


def test_list_pending_landing_excludes_entries_without_a_transfer(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert repo.list_pending_landing() == []


def test_list_pending_landing_includes_dispatched_entries_without_local_path(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=1, transfer_id=555)

    result = repo.list_pending_landing()
    assert [e.id for e in result] == [entry.id]


def test_list_pending_landing_excludes_entries_that_already_landed(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=1, transfer_id=555)
    repo.update_local_path(entry.id, 'landed.mp4')

    assert repo.list_pending_landing() == []


# ---------------------------------------------------------------------------
# list_stale_in_flight_ghosts
# ---------------------------------------------------------------------------


def _set_dispatched_at(db: Database, entry_id: int, dispatched_at: str) -> None:
    with db.session() as session:
        session.execute(
            sqlalchemy.update(BtFeedEntryRow).where(BtFeedEntryRow.id == entry_id).values(dispatched_at=dispatched_at)
        )


def test_list_stale_in_flight_ghosts_returns_old_dispatched_unlanded_entry(
    repo: BtFeedEntryRepository, db: Database, feed_id: int
) -> None:
    """A dispatched, non-terminal, unlanded entry older than the cutoff is a ghost."""
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Old Ghost', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=1, transfer_id=555)  # putio_status='IN_QUEUE'
    stale = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)).isoformat()
    _set_dispatched_at(db, entry.id, stale)

    result = repo.list_stale_in_flight_ghosts(cutoff_hours=1)
    assert [e.id for e in result] == [entry.id]


def test_list_stale_in_flight_ghosts_excludes_entries_within_cutoff(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    """A recently-dispatched entry (still within the cutoff window) is not yet a ghost."""
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Fresh', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=1, transfer_id=555)  # dispatched_at = now

    assert repo.list_stale_in_flight_ghosts(cutoff_hours=1) == []


def test_list_stale_in_flight_ghosts_excludes_landed_entries(
    repo: BtFeedEntryRepository, db: Database, feed_id: int
) -> None:
    """A landed entry is not a ghost, no matter how old — list_landed already covers it."""
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Landed', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=1, transfer_id=555)
    repo.update_local_path(entry.id, 'EP01.mp4')
    stale = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)).isoformat()
    _set_dispatched_at(db, entry.id, stale)

    assert repo.list_stale_in_flight_ghosts(cutoff_hours=1) == []


def test_list_stale_in_flight_ghosts_excludes_terminal_putio_status(
    repo: BtFeedEntryRepository, db: Database, feed_id: int
) -> None:
    """A dispatched entry that already ended in a terminal Put.io status is not a
    ghost — list_terminal_unlanded already covers it."""
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Failed', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=1, transfer_id=555)
    repo.update_putio_status(entry.id, '失敗')
    stale = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)).isoformat()
    _set_dispatched_at(db, entry.id, stale)

    assert repo.list_stale_in_flight_ghosts(cutoff_hours=1) == []


def test_list_stale_in_flight_ghosts_excludes_never_dispatched_entries(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    """An entry that was never dispatched at all (dispatched_at IS NULL) is never a ghost."""
    repo.insert_if_new(feed_id, 'guid-1', 'Never Dispatched', 'https://link/a')

    assert repo.list_stale_in_flight_ghosts(cutoff_hours=0) == []


# ---------------------------------------------------------------------------
# mark_dispatched / update_putio_status / update_local_path
# ---------------------------------------------------------------------------


def test_mark_dispatched_sets_all_dispatch_fields(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=7, transfer_id=555)

    result = repo.get(entry.id)
    assert result is not None
    assert result.matched_filter_id == 7
    assert result.putio_transfer_id == 555
    assert result.dispatched_at is not None
    assert result.putio_status == 'IN_QUEUE'


def test_update_putio_status_overwrites_status(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=7, transfer_id=555)

    repo.update_putio_status(entry.id, 'DOWNLOADING')
    assert repo.get(entry.id).putio_status == 'DOWNLOADING'  # type: ignore[union-attr]

    repo.update_putio_status(entry.id, 'COMPLETED')
    assert repo.get(entry.id).putio_status == 'COMPLETED'  # type: ignore[union-attr]


def test_update_local_path_sets_the_path(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.update_local_path(entry.id, 'bangumi/show/ep01.mp4')

    result = repo.get(entry.id)
    assert result is not None
    assert result.local_path == 'bangumi/show/ep01.mp4'


def test_get_returns_none_for_missing_id(repo: BtFeedEntryRepository) -> None:
    assert repo.get(999) is None


# ---------------------------------------------------------------------------
# search_by_title
# ---------------------------------------------------------------------------


def _set_fetched_at(db: Database, entry_id: int, fetched_at: str) -> None:
    with db.session() as session:
        session.execute(
            sqlalchemy.update(BtFeedEntryRow).where(BtFeedEntryRow.id == entry_id).values(fetched_at=fetched_at)
        )


def test_search_by_title_ilike_returns_recent_first(repo: BtFeedEntryRepository, db: Database, feed_id: int) -> None:
    older = repo.insert_if_new(feed_id, 'guid-1', 'Attack on Titan - 01', 'https://link/1')
    newer = repo.insert_if_new(feed_id, 'guid-2', 'ATTACK on titan - 02', 'https://link/2')
    unrelated = repo.insert_if_new(feed_id, 'guid-3', 'One Piece - 900', 'https://link/3')
    assert older is not None
    assert newer is not None
    assert unrelated is not None

    _set_fetched_at(db, older.id, '2026-01-01T00:00:00+00:00')
    _set_fetched_at(db, newer.id, '2026-01-02T00:00:00+00:00')

    result = repo.search_by_title('attack')
    assert [e.id for e in result] == [newer.id, older.id]


def test_search_by_title_empty_q_or_no_match_returns_empty(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Some Show - 01', 'https://link/1')
    assert entry is not None

    assert repo.search_by_title('no-such-title-substring') == []


def test_search_by_title_respects_limit(repo: BtFeedEntryRepository, db: Database, feed_id: int) -> None:
    ids: list[int] = []
    for i in range(5):
        entry = repo.insert_if_new(feed_id, f'guid-{i}', f'Show Title - {i:02d}', f'https://link/{i}')
        assert entry is not None
        ids.append(entry.id)
        _set_fetched_at(db, entry.id, f'2026-01-{i + 1:02d}T00:00:00+00:00')

    result = repo.search_by_title('Show Title', limit=2)
    assert [e.id for e in result] == list(reversed(ids))[:2]


# ---------------------------------------------------------------------------
# count_by_feed
# ---------------------------------------------------------------------------


def test_count_by_feed_empty_returns_empty_dict(repo: BtFeedEntryRepository) -> None:
    assert repo.count_by_feed() == {}


def test_count_by_feed_returns_counts_per_feed(repo: BtFeedEntryRepository, db: Database, feed_id: int) -> None:
    feed_repo = BtFeedRepository(db)
    other_feed = feed_repo.create(BtFeedCreate(name='other', url='https://other.example/rss'))
    assert other_feed.id is not None

    for i in range(3):
        assert repo.insert_if_new(feed_id, f'guid-{i}', f'A {i}', f'https://link/a/{i}') is not None
    for i in range(5):
        assert repo.insert_if_new(other_feed.id, f'guid-{i}', f'B {i}', f'https://link/b/{i}') is not None

    assert repo.count_by_feed() == {feed_id: 3, other_feed.id: 5}


# ---------------------------------------------------------------------------
# delete_stale (fix #31 — DB retention)
# ---------------------------------------------------------------------------


def _stale_fetched_at(days: int) -> str:
    return (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).isoformat()


def test_delete_stale_removes_old_unmatched_entries(repo: BtFeedEntryRepository, db: Database, feed_id: int) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Unmatched', 'https://link/1')
    assert entry is not None
    _set_fetched_at(db, entry.id, _stale_fetched_at(100))

    deleted = repo.delete_stale(days=90)
    assert deleted == 1
    assert repo.get(entry.id) is None


def test_delete_stale_removes_old_matched_and_landed_entries(
    repo: BtFeedEntryRepository, db: Database, feed_id: int
) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Landed', 'https://link/1')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=1, transfer_id=100)
    repo.update_local_path(entry.id, 'bangumi/show/ep01.mp4')
    _set_fetched_at(db, entry.id, _stale_fetched_at(100))

    deleted = repo.delete_stale(days=90)
    assert deleted == 1
    assert repo.get(entry.id) is None


def test_delete_stale_keeps_old_matched_but_not_landed_entries(
    repo: BtFeedEntryRepository, db: Database, feed_id: int
) -> None:
    """An in-flight Put.io transfer (matched, dispatched, not yet landed) must survive
    regardless of age — deleting it would orphan the landing worker's tracking."""
    entry = repo.insert_if_new(feed_id, 'guid-1', 'In flight', 'https://link/1')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=1, transfer_id=100)
    _set_fetched_at(db, entry.id, _stale_fetched_at(100))

    deleted = repo.delete_stale(days=90)
    assert deleted == 0
    assert repo.get(entry.id) is not None


def test_delete_stale_keeps_recent_unmatched_entries(repo: BtFeedEntryRepository, feed_id: int) -> None:
    """Entries within the retention window are kept regardless of match state."""
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Recent', 'https://link/1')
    assert entry is not None

    deleted = repo.delete_stale(days=90)
    assert deleted == 0
    assert repo.get(entry.id) is not None


def test_delete_stale_returns_zero_when_nothing_to_delete(repo: BtFeedEntryRepository) -> None:
    assert repo.delete_stale(days=90) == 0


# ---------------------------------------------------------------------------
# list_unmatched_within (rescan pass support)
# ---------------------------------------------------------------------------


def test_list_unmatched_within_returns_unmatched_entries_in_window(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Unmatched Recent', 'https://link/1')
    assert entry is not None

    result = repo.list_unmatched_within(90)
    assert [e.id for e in result] == [entry.id]


def test_list_unmatched_within_excludes_matched_rows(repo: BtFeedEntryRepository, feed_id: int) -> None:
    unmatched = repo.insert_if_new(feed_id, 'guid-1', 'Unmatched', 'https://link/1')
    matched_only = repo.insert_if_new(feed_id, 'guid-2', 'Matched Not Dispatched', 'https://link/2')
    dispatched = repo.insert_if_new(feed_id, 'guid-3', 'Dispatched', 'https://link/3')
    assert unmatched is not None
    assert matched_only is not None
    assert dispatched is not None

    repo.mark_matched(matched_only.id, filter_id=1)
    repo.mark_dispatched(dispatched.id, filter_id=1, transfer_id=100)

    result = repo.list_unmatched_within(90)
    assert [e.id for e in result] == [unmatched.id]


def test_list_unmatched_within_excludes_entries_older_than_window(
    repo: BtFeedEntryRepository, db: Database, feed_id: int
) -> None:
    recent = repo.insert_if_new(feed_id, 'guid-recent', 'Recent', 'https://link/recent')
    stale = repo.insert_if_new(feed_id, 'guid-stale', 'Stale', 'https://link/stale')
    assert recent is not None
    assert stale is not None
    _set_fetched_at(db, stale.id, _stale_fetched_at(100))

    result = repo.list_unmatched_within(90)
    assert [e.id for e in result] == [recent.id]


def test_list_unmatched_within_orders_oldest_first(repo: BtFeedEntryRepository, db: Database, feed_id: int) -> None:
    now = datetime.datetime.now(datetime.UTC)
    newer = repo.insert_if_new(feed_id, 'guid-newer', 'Newer', 'https://link/newer')
    older = repo.insert_if_new(feed_id, 'guid-older', 'Older', 'https://link/older')
    assert newer is not None
    assert older is not None
    _set_fetched_at(db, newer.id, (now - datetime.timedelta(days=1)).isoformat())
    _set_fetched_at(db, older.id, (now - datetime.timedelta(days=2)).isoformat())

    result = repo.list_unmatched_within(90)
    assert [e.id for e in result] == [older.id, newer.id]


def test_list_unmatched_within_returns_empty_when_nothing_unmatched(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'Dispatched', 'https://link/1')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=1, transfer_id=100)

    assert repo.list_unmatched_within(90) == []


# ---------------------------------------------------------------------------
# mark_dispatched_manual
# ---------------------------------------------------------------------------


def test_mark_dispatched_manual_clears_local_path_for_redispatch_of_landed_entry(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    """Re-dispatching an already-landed entry must reset local_path=None so
    landing_worker.list_pending_landing() picks up the new transfer;
    otherwise the new transfer is silently orphaned (never polled).
    """
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=1, transfer_id=100)
    repo.update_local_path(entry.id, 'foo.mp4')
    repo.update_putio_status(entry.id, 'COMPLETED')

    repo.mark_dispatched_manual(entry.id, 200)

    result = repo.get(entry.id)
    assert result is not None
    assert result.local_path is None
    assert result.putio_transfer_id == 200
    assert result.putio_status == 'IN_QUEUE'


# ---------------------------------------------------------------------------
# reset_dispatch (404-from-Put.io recovery)
# ---------------------------------------------------------------------------


def test_reset_dispatch_clears_putio_fields_but_preserves_matched_filter_id(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=7, transfer_id=555)
    repo.update_putio_status(entry.id, 'IN_QUEUE')

    repo.reset_dispatch(entry.id)

    result = repo.get(entry.id)
    assert result is not None
    assert result.putio_transfer_id is None
    assert result.putio_status is None
    assert result.dispatched_at is None
    assert result.local_path is None
    assert result.matched_filter_id == 7


def test_reset_dispatch_clears_local_path_of_a_previously_landed_entry(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=7, transfer_id=555)
    repo.update_local_path(entry.id, 'foo.mp4')

    repo.reset_dispatch(entry.id)

    result = repo.get(entry.id)
    assert result is not None
    assert result.local_path is None


def test_reset_dispatch_makes_entry_eligible_for_list_pending_dispatch(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    """After a reset, the entry falls back into the 'matched but not yet
    dispatched' set so the next tick (or a manual re-dispatch) picks it up."""
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=7, transfer_id=555)

    repo.reset_dispatch(entry.id)

    pending = repo.list_pending_dispatch(10)
    assert [e.id for e in pending] == [entry.id]


def test_reset_dispatch_on_missing_entry_is_a_silent_no_op(repo: BtFeedEntryRepository) -> None:
    repo.reset_dispatch(999)  # must not raise


# ---------------------------------------------------------------------------
# mark_remote_cleared / mark_remote_removed / list_landed_pending_remote_check
# ---------------------------------------------------------------------------


def test_mark_remote_cleared_sets_timestamp_and_status(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=7, transfer_id=555)
    repo.update_local_path(entry.id, 'landed.mp4')

    repo.mark_remote_cleared(entry.id)

    result = repo.get(entry.id)
    assert result is not None
    assert result.putio_status == '遠端已清理'
    assert result.remote_cleared_at is not None
    # Local landing state is untouched.
    assert result.local_path == 'landed.mp4'


def test_mark_remote_cleared_on_missing_entry_is_a_silent_no_op(repo: BtFeedEntryRepository) -> None:
    repo.mark_remote_cleared(999)  # must not raise


def test_mark_remote_removed_sets_timestamp_and_status(repo: BtFeedEntryRepository, feed_id: int) -> None:
    entry = repo.insert_if_new(feed_id, 'guid-1', 'A', 'https://link/a')
    assert entry is not None
    repo.mark_dispatched(entry.id, filter_id=7, transfer_id=555)
    repo.update_local_path(entry.id, 'landed.mp4')
    repo.update_putio_status(entry.id, 'SEEDING')

    repo.mark_remote_removed(entry.id)

    result = repo.get(entry.id)
    assert result is not None
    assert result.putio_status == '遠端已移除'
    assert result.remote_cleared_at is not None
    assert result.local_path == 'landed.mp4'


def test_mark_remote_removed_on_missing_entry_is_a_silent_no_op(repo: BtFeedEntryRepository) -> None:
    repo.mark_remote_removed(999)  # must not raise


def test_list_landed_pending_remote_check_only_returns_landed_uncleared_rows(
    repo: BtFeedEntryRepository, feed_id: int
) -> None:
    # Not dispatched at all -> excluded (no putio_transfer_id).
    not_dispatched = repo.insert_if_new(feed_id, 'guid-not-dispatched', 'A', 'https://link/a')
    assert not_dispatched is not None

    # Dispatched but not yet landed -> excluded (no local_path).
    dispatched_only = repo.insert_if_new(feed_id, 'guid-dispatched-only', 'B', 'https://link/b')
    assert dispatched_only is not None
    repo.mark_dispatched(dispatched_only.id, filter_id=1, transfer_id=101)

    # Landed, remote not yet cleared -> included, this is the target set.
    landed_pending = repo.insert_if_new(feed_id, 'guid-landed-pending', 'C', 'https://link/c')
    assert landed_pending is not None
    repo.mark_dispatched(landed_pending.id, filter_id=1, transfer_id=102)
    repo.update_local_path(landed_pending.id, 'landed-pending.mp4')

    # Landed and already cleared -> excluded.
    landed_cleared = repo.insert_if_new(feed_id, 'guid-landed-cleared', 'D', 'https://link/d')
    assert landed_cleared is not None
    repo.mark_dispatched(landed_cleared.id, filter_id=1, transfer_id=103)
    repo.update_local_path(landed_cleared.id, 'landed-cleared.mp4')
    repo.mark_remote_cleared(landed_cleared.id)

    result = repo.list_landed_pending_remote_check()
    assert [e.id for e in result] == [landed_pending.id]

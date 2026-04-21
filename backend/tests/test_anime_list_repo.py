"""Tests for :class:`AnimeListEntryRepository`."""

from __future__ import annotations

import pathlib

import pytest
import sqlalchemy.exc

from app.logging_ import Logger
from app.persistence.anime_list_repo import AnimeListEntryDTO, AnimeListEntryRepository
from app.persistence.db import Database
from app.persistence.user_repo import UserRepository


def _make_db(tmp_path: pathlib.Path, name: str = 'test.db') -> Database:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{(tmp_path / name).as_posix()}', logger)
    db.run_baseline_migrations()
    return db


def _seed_user(user_repo: UserRepository, uid: str, role: str = 'admin') -> None:
    user_repo.upsert(id=uid, username=f'User-{uid}', avatar_url=None, role=role)


# ---------------------------------------------------------------------------
# list_for_user
# ---------------------------------------------------------------------------


def test_list_for_user_returns_only_that_users_entries(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')
        _seed_user(user_repo, 'u2')

        repo.replace_all_for_user(
            'u1',
            [AnimeListEntryDTO(sn=100), AnimeListEntryDTO(sn=200)],
        )
        repo.replace_all_for_user('u2', [AnimeListEntryDTO(sn=300)])

        u1_entries = repo.list_for_user('u1')
        assert [e.sn for e in u1_entries] == [100, 200]

        u2_entries = repo.list_for_user('u2')
        assert [e.sn for e in u2_entries] == [300]
    finally:
        db.dispose()


def test_list_for_user_returns_empty_for_unknown_user(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = AnimeListEntryRepository(db)
        assert repo.list_for_user('nobody') == []
    finally:
        db.dispose()


def test_list_for_user_orders_by_sort_order(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')

        entries = [
            AnimeListEntryDTO(sn=300, sort_order=2),
            AnimeListEntryDTO(sn=100, sort_order=0),
            AnimeListEntryDTO(sn=200, sort_order=1),
        ]
        repo.replace_all_for_user('u1', entries)

        result = repo.list_for_user('u1')
        assert [e.sn for e in result] == [100, 200, 300]
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


def test_list_all_returns_everyone(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')
        _seed_user(user_repo, 'u2')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=10)])
        repo.replace_all_for_user('u2', [AnimeListEntryDTO(sn=20)])

        all_entries = repo.list_all()
        sns = [e.sn for e in all_entries]
        assert set(sns) == {10, 20}
    finally:
        db.dispose()


def test_list_all_ordered_by_user_id_then_sort_order(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        # "a" < "b" lexicographically
        _seed_user(user_repo, 'b')
        _seed_user(user_repo, 'a')

        repo.replace_all_for_user(
            'b',
            [AnimeListEntryDTO(sn=20, sort_order=0), AnimeListEntryDTO(sn=30, sort_order=1)],
        )
        repo.replace_all_for_user(
            'a',
            [AnimeListEntryDTO(sn=1, sort_order=0), AnimeListEntryDTO(sn=2, sort_order=1)],
        )

        all_entries = repo.list_all()
        sns = [e.sn for e in all_entries]
        # "a" comes before "b", each ordered by sort_order
        assert sns == [1, 2, 20, 30]
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# replace_all_for_user
# ---------------------------------------------------------------------------


def test_replace_all_for_user_removes_old_rows(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=100), AnimeListEntryDTO(sn=200)])
        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=999)])

        entries = repo.list_for_user('u1')
        assert len(entries) == 1
        assert entries[0].sn == 999
    finally:
        db.dispose()


def test_replace_all_for_user_is_atomic(tmp_path: pathlib.Path) -> None:
    """A failed insert inside replace_all_for_user should roll back the delete."""
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=100)])

        # Try to insert two entries with the same sn — the UniqueConstraint will fire.
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            repo.replace_all_for_user(
                'u1',
                [AnimeListEntryDTO(sn=50), AnimeListEntryDTO(sn=50)],
            )

        # The original row must survive (rollback restored it — but SQLite's
        # delete-then-insert pattern means the delete already happened in the
        # session that rolled back). Verify at least no partial data lingers.
        # (After rollback the original data is intact.)
        entries = repo.list_for_user('u1')
        assert len(entries) == 1
        assert entries[0].sn == 100
    finally:
        db.dispose()


def test_replace_all_for_user_preserves_other_users_entries(
    tmp_path: pathlib.Path,
) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')
        _seed_user(user_repo, 'u2')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=1)])
        repo.replace_all_for_user('u2', [AnimeListEntryDTO(sn=2)])

        # Replace u1's list only.
        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=999)])

        assert [e.sn for e in repo.list_for_user('u2')] == [2]
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# get_owner_of
# ---------------------------------------------------------------------------


def test_get_owner_of_returns_correct_user(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')
        _seed_user(user_repo, 'u2')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=100)])
        repo.replace_all_for_user('u2', [AnimeListEntryDTO(sn=200)])

        assert repo.get_owner_of(100) == 'u1'
        assert repo.get_owner_of(200) == 'u2'
    finally:
        db.dispose()


def test_get_owner_of_returns_none_for_missing_sn(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = AnimeListEntryRepository(db)
        assert repo.get_owner_of(99999) is None
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# UniqueConstraint
# ---------------------------------------------------------------------------


def test_unique_constraint_prevents_duplicate_sn_per_user(
    tmp_path: pathlib.Path,
) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')
        _seed_user(user_repo, 'u2')

        # u1 and u2 can both have sn=100 (different users).
        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=100)])
        repo.replace_all_for_user('u2', [AnimeListEntryDTO(sn=100)])

        # But u1 cannot have two rows with sn=100.
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            repo.replace_all_for_user(
                'u1',
                [AnimeListEntryDTO(sn=100), AnimeListEntryDTO(sn=100)],
            )
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# DTO field preservation
# ---------------------------------------------------------------------------


def test_dto_fields_round_trip(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')

        entry = AnimeListEntryDTO(
            sn=42,
            enabled=False,
            mode='latest',
            tag='Action',
            season=3,
            comment='cool anime',
            sort_order=7,
        )
        repo.replace_all_for_user('u1', [entry])
        result = repo.list_for_user('u1')
        assert len(result) == 1
        r = result[0]
        assert r.sn == 42
        assert r.enabled is False
        assert r.mode == 'latest'
        assert r.tag == 'Action'
        assert r.season == 3
        assert r.anime_name is None
        assert r.comment == 'cool anime'
        assert r.sort_order == 7
    finally:
        db.dispose()


def test_update_anime_name_caches_name(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=100)])
        # Initially None.
        assert repo.list_for_user('u1')[0].anime_name is None

        repo.update_anime_name(sn=100, user_id='u1', anime_name='黃泉使者')
        assert repo.list_for_user('u1')[0].anime_name == '黃泉使者'
    finally:
        db.dispose()


def test_update_anime_name_noop_for_missing_sn(tmp_path: pathlib.Path) -> None:
    """update_anime_name on a non-existent (sn, user_id) pair should not raise."""
    db = _make_db(tmp_path)
    try:
        repo = AnimeListEntryRepository(db)
        # Should not raise.
        repo.update_anime_name(sn=9999, user_id='nobody', anime_name='X')
    finally:
        db.dispose()


def test_season_defaults_to_one(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=50)])
        r = repo.list_for_user('u1')[0]
        assert r.season == 1
    finally:
        db.dispose()


def test_custom_name_round_trips(tmp_path: pathlib.Path) -> None:
    """custom_name is persisted and returned via list_for_user."""
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')

        entry = AnimeListEntryDTO(sn=77, custom_name='自訂名稱テスト')
        repo.replace_all_for_user('u1', [entry])
        r = repo.list_for_user('u1')[0]
        assert r.custom_name == '自訂名稱テスト'
    finally:
        db.dispose()


def test_custom_name_defaults_to_none(tmp_path: pathlib.Path) -> None:
    """custom_name is NULL by default."""
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=88)])
        r = repo.list_for_user('u1')[0]
        assert r.custom_name is None
    finally:
        db.dispose()


def test_custom_name_round_trips_via_list_all(tmp_path: pathlib.Path) -> None:
    """custom_name is returned correctly via list_all too."""
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=99, custom_name='Override')])
        all_entries = repo.list_all()
        assert len(all_entries) == 1
        assert all_entries[0].custom_name == 'Override'
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# Feature B: duplicate detection repo methods
# ---------------------------------------------------------------------------


def test_find_duplicate_source_returns_earliest_match(tmp_path: pathlib.Path) -> None:
    """find_duplicate_source returns the row with the lowest id for a given name."""
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')
        _seed_user(user_repo, 'u2')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=10, anime_name='進擊的巨人')])
        repo.replace_all_for_user('u2', [AnimeListEntryDTO(sn=20, anime_name='進擊的巨人')])

        # Query without exclusion — should return the u1 entry (lower id).
        u1_entries = repo.list_for_user('u1')
        assert len(u1_entries) == 1
        source = repo.find_duplicate_source('進擊的巨人')
        assert source is not None
        assert source.sn == 10
        assert source.user_id == 'u1'

        # Exclude u1's entry → should return u2's entry.
        source2 = repo.find_duplicate_source('進擊的巨人', exclude_id=u1_entries[0].id)
        assert source2 is not None
        assert source2.sn == 20
    finally:
        db.dispose()


def test_find_duplicate_source_case_insensitive(tmp_path: pathlib.Path) -> None:
    """find_duplicate_source matches case-insensitively."""
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=10, anime_name='Naruto')])

        result = repo.find_duplicate_source('NARUTO')
        assert result is not None
        assert result.sn == 10

        result2 = repo.find_duplicate_source('naruto')
        assert result2 is not None
        assert result2.sn == 10
    finally:
        db.dispose()


def test_find_duplicate_source_returns_none_for_no_match(tmp_path: pathlib.Path) -> None:
    """find_duplicate_source returns None when no matching entry exists."""
    db = _make_db(tmp_path)
    try:
        repo = AnimeListEntryRepository(db)
        assert repo.find_duplicate_source('NonExistent') is None
    finally:
        db.dispose()


def test_find_duplicate_source_empty_name_returns_none(tmp_path: pathlib.Path) -> None:
    """find_duplicate_source returns None for empty/whitespace names."""
    db = _make_db(tmp_path)
    try:
        repo = AnimeListEntryRepository(db)
        assert repo.find_duplicate_source('') is None
        assert repo.find_duplicate_source('   ') is None
    finally:
        db.dispose()


def test_reevaluate_duplicates_after_delete_clears_pointer(tmp_path: pathlib.Path) -> None:
    """reevaluate_duplicates_after_delete clears duplicate_of_entry_id on the next-earliest."""
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')
        _seed_user(user_repo, 'u2')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=10, anime_name='進擊的巨人')])
        u1_entries = repo.list_for_user('u1')
        source_id = u1_entries[0].id
        assert source_id is not None

        repo.replace_all_for_user(
            'u2',
            [AnimeListEntryDTO(sn=20, enabled=False, anime_name='進擊的巨人', duplicate_of_entry_id=source_id)],
        )

        # Simulate deleting u1's entry.
        u1_dto = repo.list_for_user('u1')[0]
        repo.replace_all_for_user('u1', [])

        updated_ids = repo.reevaluate_duplicates_after_delete(u1_dto)

        # u2's entry should be in the updated set and have its pointer cleared.
        assert len(updated_ids) >= 1
        u2_entries = repo.list_for_user('u2')
        assert len(u2_entries) == 1
        assert u2_entries[0].duplicate_of_entry_id is None
        # Still disabled.
        assert u2_entries[0].enabled is False
    finally:
        db.dispose()


def test_duplicate_of_entry_id_round_trips(tmp_path: pathlib.Path) -> None:
    """duplicate_of_entry_id is persisted and returned via list_for_user."""
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        repo = AnimeListEntryRepository(db)
        _seed_user(user_repo, 'u1')
        _seed_user(user_repo, 'u2')

        repo.replace_all_for_user('u1', [AnimeListEntryDTO(sn=10)])
        u1_entries = repo.list_for_user('u1')
        source_id = u1_entries[0].id
        assert source_id is not None

        repo.replace_all_for_user(
            'u2',
            [AnimeListEntryDTO(sn=20, enabled=False, duplicate_of_entry_id=source_id)],
        )

        u2_entries = repo.list_for_user('u2')
        assert u2_entries[0].duplicate_of_entry_id == source_id
        assert u2_entries[0].enabled is False
    finally:
        db.dispose()

"""Tests for ``TgWatchedChatRepository`` — CRUD + UNIQUE(user_id, chat_id)."""

from __future__ import annotations

import pathlib

import pytest

from app.logging_ import Logger
from app.models import TgWatchedChatCreate, TgWatchedChatUpdate
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths
from app.persistence.tg_watched_chat_repo import (
    _MAX_WATCHED_CHATS_PER_USER,
    DuplicateWatchedChatError,
    TgWatchedChatRepository,
    TooManyWatchedChatsError,
)


@pytest.fixture
def database(tmp_path: pathlib.Path) -> Database:
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    db.run_baseline_migrations()
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def repo(database: Database) -> TgWatchedChatRepository:
    return TgWatchedChatRepository(database)


def _create_payload(**overrides: object) -> TgWatchedChatCreate:
    defaults: dict[str, object] = {
        'chat_id': -1001234567890,
        'chat_title': '測試群組',
        'media_types': ['video'],
        'size_min_mb': None,
        'size_max_mb': None,
        'format_whitelist': None,
        'save_path': None,
        'enabled': True,
    }
    defaults.update(overrides)
    return TgWatchedChatCreate(**defaults)  # type: ignore[arg-type]


def test_insert_and_get(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())

    fetched = repo.get('user-1', -1001234567890)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.chat_title == '測試群組'
    assert fetched.media_types == ['video']
    assert fetched.enabled is True


def test_insert_serialises_json_fields(repo: TgWatchedChatRepository) -> None:
    created = repo.insert(
        'user-1',
        _create_payload(
            media_types=['video', 'document'],
            format_whitelist=['mp4', 'mkv'],
            size_min_mb=10,
            size_max_mb=2000,
        ),
    )

    assert created.media_types == ['video', 'document']
    assert created.format_whitelist == ['mp4', 'mkv']
    assert created.size_min_mb == 10
    assert created.size_max_mb == 2000


def test_duplicate_user_chat_raises(repo: TgWatchedChatRepository) -> None:
    repo.insert('user-1', _create_payload())

    with pytest.raises(DuplicateWatchedChatError):
        repo.insert('user-1', _create_payload())


def test_same_chat_id_different_user_is_allowed(repo: TgWatchedChatRepository) -> None:
    """UNIQUE is on (user_id, chat_id) — two different users can watch the same chat."""
    repo.insert('user-1', _create_payload())
    other = repo.insert('user-2', _create_payload())

    assert other.chat_id == -1001234567890
    assert repo.get('user-2', -1001234567890) is not None


def test_list_by_user_scopes_to_owner(repo: TgWatchedChatRepository) -> None:
    repo.insert('user-1', _create_payload(chat_id=1))
    repo.insert('user-1', _create_payload(chat_id=2))
    repo.insert('user-2', _create_payload(chat_id=3))

    assert {c.chat_id for c in repo.list_by_user('user-1')} == {1, 2}
    assert {c.chat_id for c in repo.list_by_user('user-2')} == {3}


def test_list_enabled_by_user_excludes_disabled(repo: TgWatchedChatRepository) -> None:
    repo.insert('user-1', _create_payload(chat_id=1, enabled=True))
    repo.insert('user-1', _create_payload(chat_id=2, enabled=False))

    enabled = repo.list_enabled_by_user('user-1')
    assert [c.chat_id for c in enabled] == [1]


def test_list_all_enabled_pairs_with_owner(repo: TgWatchedChatRepository) -> None:
    repo.insert('user-1', _create_payload(chat_id=1, enabled=True))
    repo.insert('user-2', _create_payload(chat_id=2, enabled=True))
    repo.insert('user-2', _create_payload(chat_id=3, enabled=False))

    pairs = repo.list_all_enabled()
    assert sorted(pairs, key=lambda p: p[1].chat_id) == [
        ('user-1', repo.get('user-1', 1)),
        ('user-2', repo.get('user-2', 2)),
    ]


def test_update_partial_only_changes_specified_fields(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload(chat_title='原標題', enabled=True))

    updated = repo.update('user-1', created.id, TgWatchedChatUpdate(enabled=False))

    assert updated is not None
    assert updated.enabled is False
    assert updated.chat_title == '原標題'  # untouched


def test_update_media_types_and_format_whitelist(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())

    updated = repo.update(
        'user-1',
        created.id,
        TgWatchedChatUpdate(media_types=['audio'], format_whitelist=['mp3']),
    )

    assert updated is not None
    assert updated.media_types == ['audio']
    assert updated.format_whitelist == ['mp3']


def test_update_clears_format_whitelist_with_explicit_none(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload(format_whitelist=['mp4']))

    updated = repo.update('user-1', created.id, TgWatchedChatUpdate(format_whitelist=None))

    # exclude_unset means an explicit None IS applied (field was set in the payload).
    assert updated is not None
    assert updated.format_whitelist is None


def test_update_wrong_user_scope_is_noop(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())

    result = repo.update('user-2', created.id, TgWatchedChatUpdate(enabled=False))

    assert result is None
    assert repo.get_by_id('user-1', created.id).enabled is True  # type: ignore[union-attr]


def test_delete_removes_row(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())

    repo.delete('user-1', created.id)

    assert repo.get_by_id('user-1', created.id) is None


def test_delete_wrong_user_scope_is_noop(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())

    repo.delete('user-2', created.id)

    assert repo.get_by_id('user-1', created.id) is not None


def test_get_returns_none_when_missing(repo: TgWatchedChatRepository) -> None:
    assert repo.get('user-1', 999) is None


# ---------------------------------------------------------------------------
# HIGH-6 security fix — per-user watched-chat cap
# ---------------------------------------------------------------------------


def test_count_by_user_counts_enabled_and_disabled(repo: TgWatchedChatRepository) -> None:
    repo.insert('user-1', _create_payload(chat_id=1, enabled=True))
    repo.insert('user-1', _create_payload(chat_id=2, enabled=False))
    repo.insert('user-2', _create_payload(chat_id=3))

    assert repo.count_by_user('user-1') == 2
    assert repo.count_by_user('user-2') == 1
    assert repo.count_by_user('user-3-never-inserted') == 0


def test_insert_at_cap_raises_too_many_watched_chats(repo: TgWatchedChatRepository) -> None:
    for chat_id in range(_MAX_WATCHED_CHATS_PER_USER):
        repo.insert('user-1', _create_payload(chat_id=chat_id))
    assert repo.count_by_user('user-1') == _MAX_WATCHED_CHATS_PER_USER

    with pytest.raises(TooManyWatchedChatsError):
        repo.insert('user-1', _create_payload(chat_id=_MAX_WATCHED_CHATS_PER_USER))


def test_insert_at_cap_for_one_user_does_not_block_another(repo: TgWatchedChatRepository) -> None:
    for chat_id in range(_MAX_WATCHED_CHATS_PER_USER):
        repo.insert('user-1', _create_payload(chat_id=chat_id))

    # user-2 is unaffected by user-1's cap — the same chat_id is fine too,
    # since UNIQUE is scoped to (user_id, chat_id).
    created = repo.insert('user-2', _create_payload(chat_id=0))
    assert created.chat_id == 0


# ---------------------------------------------------------------------------
# Historical backfill — default state + insert overrides
# ---------------------------------------------------------------------------


def test_insert_defaults_backfill_fields(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())

    assert created.backfill_enabled is False
    assert created.backfill_days == 7
    assert created.backfill_status is None
    assert created.backfill_scanned_count == 0
    assert created.backfill_matched_count == 0
    assert created.backfill_started_at is None
    assert created.backfill_finished_at is None


def test_insert_honours_backfill_overrides(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload(backfill_enabled=True, backfill_days=30))

    assert created.backfill_enabled is True
    assert created.backfill_days == 30


def test_update_backfill_enabled_and_days(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())

    updated = repo.update('user-1', created.id, TgWatchedChatUpdate(backfill_enabled=True, backfill_days=14))

    assert updated is not None
    assert updated.backfill_enabled is True
    assert updated.backfill_days == 14


# ---------------------------------------------------------------------------
# Historical backfill — mark_backfill_* state machine
# ---------------------------------------------------------------------------


def test_mark_backfill_pending(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())

    repo.mark_backfill_pending('user-1', created.id)

    fetched = repo.get_by_id('user-1', created.id)
    assert fetched is not None
    assert fetched.backfill_status == 'pending'


def test_mark_backfill_running_resets_counters_and_sets_started_at(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())
    repo.mark_backfill_pending('user-1', created.id)
    repo.mark_backfill_progress('user-1', created.id, scanned_count=5, matched_count=2)

    repo.mark_backfill_running('user-1', created.id, started_at='2026-07-11T00:00:00+00:00')

    fetched = repo.get_by_id('user-1', created.id)
    assert fetched is not None
    assert fetched.backfill_status == 'running'
    assert fetched.backfill_started_at == '2026-07-11T00:00:00+00:00'
    assert fetched.backfill_scanned_count == 0
    assert fetched.backfill_matched_count == 0


def test_mark_backfill_progress_updates_counters(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())
    repo.mark_backfill_running('user-1', created.id, started_at='2026-07-11T00:00:00+00:00')

    repo.mark_backfill_progress('user-1', created.id, scanned_count=50, matched_count=12)

    fetched = repo.get_by_id('user-1', created.id)
    assert fetched is not None
    assert fetched.backfill_scanned_count == 50
    assert fetched.backfill_matched_count == 12
    # Progress updates must not disturb the in-flight status.
    assert fetched.backfill_status == 'running'


def test_mark_backfill_done_sets_status_and_finished_at(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())
    repo.mark_backfill_running('user-1', created.id, started_at='2026-07-11T00:00:00+00:00')

    repo.mark_backfill_done('user-1', created.id, finished_at='2026-07-11T01:00:00+00:00')

    fetched = repo.get_by_id('user-1', created.id)
    assert fetched is not None
    assert fetched.backfill_status == 'done'
    assert fetched.backfill_finished_at == '2026-07-11T01:00:00+00:00'


def test_mark_backfill_failed_sets_status_and_finished_at(repo: TgWatchedChatRepository) -> None:
    created = repo.insert('user-1', _create_payload())
    repo.mark_backfill_running('user-1', created.id, started_at='2026-07-11T00:00:00+00:00')

    repo.mark_backfill_failed('user-1', created.id, finished_at='2026-07-11T01:00:00+00:00')

    fetched = repo.get_by_id('user-1', created.id)
    assert fetched is not None
    assert fetched.backfill_status == 'failed'
    assert fetched.backfill_finished_at == '2026-07-11T01:00:00+00:00'


def test_mark_backfill_scoped_to_owning_user(repo: TgWatchedChatRepository) -> None:
    """mark_backfill_* is scoped by (user_id, watched_chat_id) — same convention as update()/delete()."""
    created = repo.insert('user-1', _create_payload())

    repo.mark_backfill_pending('user-2', created.id)

    fetched = repo.get_by_id('user-1', created.id)
    assert fetched is not None
    assert fetched.backfill_status is None

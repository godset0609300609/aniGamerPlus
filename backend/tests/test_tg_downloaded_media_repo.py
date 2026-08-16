"""Tests for ``TgDownloadedMediaRepository`` — insert_if_new dedup, pagination."""

from __future__ import annotations

import datetime
import pathlib

import pytest

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths
from app.persistence.tg_downloaded_media_repo import TgDownloadedMediaRepository


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
def repo(database: Database) -> TgDownloadedMediaRepository:
    return TgDownloadedMediaRepository(database)


def _insert(repo: TgDownloadedMediaRepository, *, user_id: str = 'user-1', chat_id: int = 1, message_id: int = 1):
    return repo.insert_if_new(
        user_id,
        chat_id=chat_id,
        message_id=message_id,
        file_id='unique-file-id',
        file_name='episode01.mp4',
        file_size=123_456_789,
        local_path='/bangumi/tg/user-1/chat/episode01.mp4',
    )


def test_insert_if_new_creates_row(repo: TgDownloadedMediaRepository) -> None:
    entry = _insert(repo)

    assert entry is not None
    assert entry.user_id == 'user-1'
    assert entry.chat_id == 1
    assert entry.message_id == 1
    assert entry.file_name == 'episode01.mp4'


def test_insert_if_new_dedups_on_user_chat_message(repo: TgDownloadedMediaRepository) -> None:
    first = _insert(repo)
    second = _insert(repo)

    assert first is not None
    assert second is None  # duplicate (user_id, chat_id, message_id) — silent skip, not an error


def test_insert_if_new_allows_same_message_id_different_chat(repo: TgDownloadedMediaRepository) -> None:
    first = _insert(repo, chat_id=1)
    second = _insert(repo, chat_id=2)

    assert first is not None
    assert second is not None


def test_insert_if_new_allows_same_message_id_different_user(repo: TgDownloadedMediaRepository) -> None:
    first = _insert(repo, user_id='user-1')
    second = _insert(repo, user_id='user-2')

    assert first is not None
    assert second is not None


def test_exists_reflects_dedup_state(repo: TgDownloadedMediaRepository) -> None:
    assert repo.exists('user-1', 1, 1) is False

    _insert(repo)

    assert repo.exists('user-1', 1, 1) is True
    assert repo.exists('user-1', 1, 2) is False


def test_list_by_user_paginates_newest_first(repo: TgDownloadedMediaRepository) -> None:
    for i in range(5):
        repo.insert_if_new(
            'user-1',
            chat_id=1,
            message_id=i,
            file_id=f'file-{i}',
            file_name=f'ep{i}.mp4',
            file_size=1000 + i,
            local_path=f'/bangumi/tg/ep{i}.mp4',
        )

    page1, total = repo.list_by_user('user-1', page=1, size=2)
    assert total == 5
    assert len(page1) == 2
    # Newest-first: highest message_id (last inserted) comes first.
    assert page1[0].message_id == 4
    assert page1[1].message_id == 3

    page2, total2 = repo.list_by_user('user-1', page=2, size=2)
    assert total2 == 5
    assert [e.message_id for e in page2] == [2, 1]


def test_list_by_user_scopes_to_owner(repo: TgDownloadedMediaRepository) -> None:
    _insert(repo, user_id='user-1', chat_id=1, message_id=1)
    _insert(repo, user_id='user-2', chat_id=1, message_id=2)

    items, total = repo.list_by_user('user-1')
    assert total == 1
    assert items[0].user_id == 'user-1'


def test_count_by_user_since(repo: TgDownloadedMediaRepository) -> None:
    _insert(repo, message_id=1)
    _insert(repo, message_id=2)

    future_cutoff = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)
    past_cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)

    assert repo.count_by_user_since('user-1', past_cutoff) == 2
    assert repo.count_by_user_since('user-1', future_cutoff) == 0


def test_mark_landed_updates_local_path(repo: TgDownloadedMediaRepository) -> None:
    entry = _insert(repo)
    assert entry is not None

    repo.mark_landed(entry.id, '/final/path/episode01.mp4')

    items, _total = repo.list_by_user('user-1')
    assert items[0].local_path == '/final/path/episode01.mp4'


def test_get_by_id_for_user_returns_own_row(repo: TgDownloadedMediaRepository) -> None:
    entry = _insert(repo, user_id='user-1')
    assert entry is not None

    found = repo.get_by_id_for_user('user-1', entry.id)

    assert found is not None
    assert found.id == entry.id
    assert found.user_id == 'user-1'


def test_get_by_id_for_user_returns_none_for_other_users_row(repo: TgDownloadedMediaRepository) -> None:
    """Ownership scoping — the whole point of get_by_id_for_user existing at
    all: a row id belonging to another user must never be returned, so the
    force-redownload feature built on it can never leak/act on someone
    else's download."""
    entry = _insert(repo, user_id='user-1')
    assert entry is not None

    found = repo.get_by_id_for_user('user-2', entry.id)

    assert found is None


def test_get_by_id_for_user_returns_none_for_missing_id(repo: TgDownloadedMediaRepository) -> None:
    assert repo.get_by_id_for_user('user-1', 999999) is None


def test_replace_after_redownload_updates_in_place_keeping_id(repo: TgDownloadedMediaRepository) -> None:
    """Item 3's design decision: force-redownload UPDATEs the existing row
    rather than delete-then-reinsert, so the row id stays stable."""
    entry = _insert(repo)
    assert entry is not None
    original_id = entry.id

    repo.replace_after_redownload(
        entry.id,
        file_id='new-unique-file-id',
        file_name='episode01.mp4',
        file_size=999_999_999,
        local_path='/bangumi/tg/user-1/chat/episode01.mp4',
        progress_sn=42,
    )

    items, total = repo.list_by_user('user-1')
    assert total == 1  # still exactly one row — not delete+reinsert
    updated = items[0]
    assert updated.id == original_id
    assert updated.file_id == 'new-unique-file-id'
    assert updated.file_size == 999_999_999
    assert updated.progress_sn == 42
    assert updated.downloaded_at != entry.downloaded_at  # downloaded_at refreshed to "now"


def test_replace_after_redownload_preserves_dedup_key(repo: TgDownloadedMediaRepository) -> None:
    """The (user_id, chat_id, message_id) UNIQUE key must survive a
    replace — exists() should keep reporting True throughout, never
    flickering to False (see the repo method's own docstring for why a
    delete+reinsert alternative would be the wrong choice here)."""
    entry = _insert(repo, user_id='user-1', chat_id=5, message_id=7)
    assert entry is not None
    assert repo.exists('user-1', 5, 7) is True

    repo.replace_after_redownload(
        entry.id,
        file_id='new-id',
        file_name='episode01.mp4',
        file_size=1,
        local_path='/x/episode01.mp4',
        progress_sn=None,
    )

    assert repo.exists('user-1', 5, 7) is True

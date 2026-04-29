"""Tests for :class:`UserRepository`."""

from __future__ import annotations

import pathlib

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.user_repo import UserRepository


def _make_db(tmp_path: pathlib.Path, name: str = 'test.db') -> Database:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{(tmp_path / name).as_posix()}', logger)
    db.run_baseline_migrations()
    return db


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


def test_upsert_inserts_new_user(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        row = repo.upsert(id='123', username='Alice', avatar_url=None)
        assert row.id == '123'
        assert row.username == 'Alice'
        assert row.role == 'downloader'  # default on insert
        assert row.avatar_url is None
    finally:
        db.dispose()


def test_upsert_with_explicit_role_on_insert(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        row = repo.upsert(id='777', username='Admin', avatar_url=None, role='admin')
        assert row.role == 'admin'
    finally:
        db.dispose()


def test_upsert_updates_existing_user(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='123', username='Alice', avatar_url=None)
        updated = repo.upsert(id='123', username='Alice Updated', avatar_url='http://img')
        assert updated.username == 'Alice Updated'
        assert updated.avatar_url == 'http://img'
    finally:
        db.dispose()


def test_upsert_without_role_on_existing_user_preserves_role(
    tmp_path: pathlib.Path,
) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        # Insert with admin role.
        repo.upsert(id='100', username='Bob', avatar_url=None, role='admin')
        # Update without specifying role — should stay admin.
        updated = repo.upsert(id='100', username='Bob V2', avatar_url=None)
        assert updated.role == 'admin'
    finally:
        db.dispose()


def test_upsert_with_role_on_existing_user_overrides_role(
    tmp_path: pathlib.Path,
) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='200', username='Carol', avatar_url=None, role='downloader')
        updated = repo.upsert(id='200', username='Carol', avatar_url=None, role='admin')
        assert updated.role == 'admin'
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_returns_none_for_missing_id(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        assert repo.get('nonexistent') is None
    finally:
        db.dispose()


def test_get_returns_row_after_upsert(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='42', username='Dave', avatar_url='http://avatar')
        row = repo.get('42')
        assert row is not None
        assert row.username == 'Dave'
        assert row.avatar_url == 'http://avatar'
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# set_role
# ---------------------------------------------------------------------------


def test_set_role_changes_role(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='50', username='Eve', avatar_url=None, role='downloader')
        repo.set_role('50', 'admin')
        row = repo.get('50')
        assert row is not None
        assert row.role == 'admin'
    finally:
        db.dispose()


def test_set_role_preserves_username(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='60', username='Frank', avatar_url=None, role='downloader')
        repo.set_role('60', 'admin')
        row = repo.get('60')
        assert row is not None
        assert row.username == 'Frank'
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# count_admins / first_admin
# ---------------------------------------------------------------------------


def test_count_admins_returns_zero_initially(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        assert repo.count_admins() == 0
    finally:
        db.dispose()


def test_count_admins_counts_only_admins(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='a1', username='Admin1', avatar_url=None, role='admin')
        repo.upsert(id='a2', username='Admin2', avatar_url=None, role='admin')
        repo.upsert(id='d1', username='Down1', avatar_url=None, role='downloader')
        assert repo.count_admins() == 2
    finally:
        db.dispose()


def test_first_admin_returns_none_when_empty(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        assert repo.first_admin() is None
    finally:
        db.dispose()


def test_first_admin_returns_earliest_admin(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        # Insert in order; first_admin should return the first inserted admin.
        repo.upsert(id='z', username='ZAdmin', avatar_url=None, role='admin')
        repo.upsert(id='a', username='AAdmin', avatar_url=None, role='admin')
        first = repo.first_admin()
        assert first is not None
        # The first inserted admin is "z" (earlier created_at).
        assert first.id == 'z'
    finally:
        db.dispose()


def test_first_admin_skips_non_admins(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='d1', username='Downloader', avatar_url=None, role='downloader')
        repo.upsert(id='adm', username='Admin', avatar_url=None, role='admin')
        first = repo.first_admin()
        assert first is not None
        assert first.id == 'adm'
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


def test_list_all_returns_all_users(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='u1', username='User1', avatar_url=None)
        repo.upsert(id='u2', username='User2', avatar_url=None)
        rows = repo.list_all()
        assert len(rows) == 2
        ids = {r.id for r in rows}
        assert ids == {'u1', 'u2'}
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# Telegram binding helpers
# ---------------------------------------------------------------------------


def test_telegram_defaults_on_new_user(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        row = repo.upsert(id='tg1', username='Alice', avatar_url=None)
        assert row.telegram_chat_id is None
        assert row.telegram_link_token is None
        assert row.telegram_notify_enabled is True
    finally:
        db.dispose()


def test_set_telegram_link_token(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='tg2', username='Bob', avatar_url=None)
        repo.set_telegram_link_token('tg2', 'abc123token')
        row = repo.get('tg2')
        assert row is not None
        assert row.telegram_link_token == 'abc123token'
    finally:
        db.dispose()


def test_set_telegram_link_token_clears_when_none(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='tg3', username='Carol', avatar_url=None)
        repo.set_telegram_link_token('tg3', 'sometoken')
        repo.set_telegram_link_token('tg3', None)
        row = repo.get('tg3')
        assert row is not None
        assert row.telegram_link_token is None
    finally:
        db.dispose()


def test_finalize_telegram_binding(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='tg4', username='Dave', avatar_url=None)
        repo.set_telegram_link_token('tg4', 'linktoken')
        repo.finalize_telegram_binding('tg4', 99887766)
        row = repo.get('tg4')
        assert row is not None
        assert row.telegram_chat_id == 99887766
        assert row.telegram_link_token is None
    finally:
        db.dispose()


def test_clear_telegram_binding(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='tg5', username='Eve', avatar_url=None)
        repo.finalize_telegram_binding('tg5', 11223344)
        repo.clear_telegram_binding('tg5')
        row = repo.get('tg5')
        assert row is not None
        assert row.telegram_chat_id is None
        assert row.telegram_link_token is None
    finally:
        db.dispose()


def test_find_by_telegram_chat_id(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='tg6', username='Frank', avatar_url=None)
        repo.finalize_telegram_binding('tg6', 55443322)
        found = repo.find_by_telegram_chat_id(55443322)
        assert found is not None
        assert found.id == 'tg6'
    finally:
        db.dispose()


def test_find_by_telegram_chat_id_returns_none_when_not_found(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        assert repo.find_by_telegram_chat_id(99999999) is None
    finally:
        db.dispose()


def test_find_by_telegram_link_token(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='tg7', username='Grace', avatar_url=None)
        repo.set_telegram_link_token('tg7', 'uniquetoken42')
        found = repo.find_by_telegram_link_token('uniquetoken42')
        assert found is not None
        assert found.id == 'tg7'
    finally:
        db.dispose()


def test_find_by_telegram_link_token_returns_none_for_unknown(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        assert repo.find_by_telegram_link_token('nosuchtoken') is None
    finally:
        db.dispose()


def test_set_telegram_notify_enabled(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='tg8', username='Heidi', avatar_url=None)
        # Disable notifications.
        repo.set_telegram_notify_enabled('tg8', False)
        row = repo.get('tg8')
        assert row is not None
        assert row.telegram_notify_enabled is False
        # Re-enable.
        repo.set_telegram_notify_enabled('tg8', True)
        row2 = repo.get('tg8')
        assert row2 is not None
        assert row2.telegram_notify_enabled is True
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# set_telegram_mute_until
# ---------------------------------------------------------------------------


def test_set_telegram_mute_until_persists(tmp_path: pathlib.Path) -> None:
    import datetime

    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='tg9', username='Ivan', avatar_url=None)
        # Confirm default is None.
        row = repo.get('tg9')
        assert row is not None
        assert row.telegram_mute_until is None
        # Set a future deadline.
        deadline = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
        repo.set_telegram_mute_until('tg9', deadline)
        row2 = repo.get('tg9')
        assert row2 is not None
        assert row2.telegram_mute_until is not None
        # Compare at-second granularity to avoid sub-second drift from DB storage.
        assert abs((row2.telegram_mute_until.replace(tzinfo=datetime.timezone.utc) - deadline).total_seconds()) < 2
    finally:
        db.dispose()


def test_set_telegram_mute_until_to_none_clears(tmp_path: pathlib.Path) -> None:
    import datetime

    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='tg10', username='Judy', avatar_url=None)
        deadline = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2)
        repo.set_telegram_mute_until('tg10', deadline)
        # Verify it was set.
        row = repo.get('tg10')
        assert row is not None
        assert row.telegram_mute_until is not None
        # Clear it.
        repo.set_telegram_mute_until('tg10', None)
        row2 = repo.get('tg10')
        assert row2 is not None
        assert row2.telegram_mute_until is None
    finally:
        db.dispose()

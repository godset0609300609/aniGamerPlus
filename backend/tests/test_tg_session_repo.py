"""Tests for ``TgSessionRepository`` — Fernet round-trip, upsert semantics,
revoke, unique constraint.

``ANIGAMERPLUS_FERNET_KEY`` is provided by the autouse ``_tg_fernet_key``
fixture in ``conftest.py``.
"""

from __future__ import annotations

import pathlib

import pytest
import sqlalchemy

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.models import TgSessionRow
from app.persistence.paths import WorkspacePaths
from app.persistence.tg_session_repo import TgSessionRepository
from app.security import crypto


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
def repo(database: Database) -> TgSessionRepository:
    return TgSessionRepository(database)


def test_upsert_creates_new_session(repo: TgSessionRepository) -> None:
    entry = repo.upsert('user-1', session_string='PLAINTEXT_SESSION', phone_tail4='1234', telegram_user_id=999)

    assert entry.user_id == 'user-1'
    assert entry.phone_tail4 == '1234'
    assert entry.telegram_user_id == 999
    assert entry.status == 'active'
    assert entry.id is not None


def test_upsert_never_stores_plaintext_session_string(repo: TgSessionRepository, database: Database) -> None:
    """The DB column must hold a Fernet token, not the plaintext session string.

    Real round-trip through ``app.security.crypto`` — not a mocked
    encrypt/decrypt — so this actually exercises Fernet, not just the
    repo's plumbing around it.
    """
    repo.upsert('user-1', session_string='PLAINTEXT_SESSION', phone_tail4=None, telegram_user_id=None)

    with database.session() as session:
        row = session.scalars(sqlalchemy.select(TgSessionRow).where(TgSessionRow.user_id == 'user-1')).one()
        stored = row.session_string_encrypted

    assert stored != 'PLAINTEXT_SESSION'
    # A genuine Fernet token round-trips back to the original plaintext.
    assert crypto.decrypt_str(stored) == 'PLAINTEXT_SESSION'


def test_get_decrypted_session_string_round_trips(repo: TgSessionRepository) -> None:
    repo.upsert('user-1', session_string='super-secret-session-string', phone_tail4=None, telegram_user_id=None)

    assert repo.get_decrypted_session_string('user-1') == 'super-secret-session-string'


def test_get_decrypted_session_string_none_when_no_row(repo: TgSessionRepository) -> None:
    assert repo.get_decrypted_session_string('nobody') is None


def test_get_decrypted_session_string_none_when_revoked(repo: TgSessionRepository) -> None:
    repo.upsert('user-1', session_string='s', phone_tail4=None, telegram_user_id=None)
    repo.revoke('user-1')

    assert repo.get_decrypted_session_string('user-1') is None


def test_get_decrypted_session_string_none_when_expired(repo: TgSessionRepository) -> None:
    repo.upsert('user-1', session_string='s', phone_tail4=None, telegram_user_id=None)
    repo.mark_expired('user-1')

    assert repo.get_decrypted_session_string('user-1') is None
    entry = repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.status == 'expired'


def test_upsert_is_idempotent_per_user_unique_constraint(repo: TgSessionRepository) -> None:
    """A second ``upsert`` for the same user overwrites rather than duplicating (UNIQUE(user_id))."""
    first = repo.upsert('user-1', session_string='session-a', phone_tail4='1111', telegram_user_id=1)
    second = repo.upsert('user-1', session_string='session-b', phone_tail4='2222', telegram_user_id=2)

    assert first.id == second.id
    assert repo.get_decrypted_session_string('user-1') == 'session-b'
    entry = repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.phone_tail4 == '2222'
    assert entry.telegram_user_id == 2


def test_rebind_after_revoke_reactivates(repo: TgSessionRepository) -> None:
    repo.upsert('user-1', session_string='session-a', phone_tail4=None, telegram_user_id=None)
    repo.revoke('user-1')
    assert repo.get_by_user_id('user-1').status == 'revoked'  # type: ignore[union-attr]

    repo.upsert('user-1', session_string='session-b', phone_tail4=None, telegram_user_id=None)

    entry = repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.status == 'active'
    assert repo.get_decrypted_session_string('user-1') == 'session-b'


def test_get_by_user_id_none_when_missing(repo: TgSessionRepository) -> None:
    assert repo.get_by_user_id('nobody') is None


def test_touch_last_active_updates_timestamp(repo: TgSessionRepository) -> None:
    entry = repo.upsert('user-1', session_string='s', phone_tail4=None, telegram_user_id=None)
    original_last_active = entry.last_active_at

    repo.touch_last_active('user-1')

    updated = repo.get_by_user_id('user-1')
    assert updated is not None
    # ISO timestamps are lexicographically comparable; touch must not go backwards.
    assert updated.last_active_at is not None
    assert original_last_active is not None
    assert updated.last_active_at >= original_last_active


def test_list_active_returns_only_active_sessions(repo: TgSessionRepository) -> None:
    repo.upsert('user-1', session_string='a', phone_tail4=None, telegram_user_id=None)
    repo.upsert('user-2', session_string='b', phone_tail4=None, telegram_user_id=None)
    repo.revoke('user-2')
    repo.upsert('user-3', session_string='c', phone_tail4=None, telegram_user_id=None)
    repo.mark_expired('user-3')

    active_user_ids = {e.user_id for e in repo.list_active()}
    assert active_user_ids == {'user-1'}


def test_revoke_missing_user_is_noop(repo: TgSessionRepository) -> None:
    repo.revoke('nobody')  # must not raise
    assert repo.get_by_user_id('nobody') is None


def test_upsert_persists_notification_bind_outcome(repo: TgSessionRepository) -> None:
    entry = repo.upsert(
        'user-1',
        session_string='s',
        phone_tail4=None,
        telegram_user_id=None,
        notification_bind_status='bot_username_not_configured',
        notification_bind_error=None,
    )

    assert entry.notification_bind_status == 'bot_username_not_configured'
    assert entry.notification_bind_error is None


def test_upsert_defaults_notification_bind_fields_to_none(repo: TgSessionRepository) -> None:
    entry = repo.upsert('user-1', session_string='s', phone_tail4=None, telegram_user_id=None)

    assert entry.notification_bind_status is None
    assert entry.notification_bind_error is None


def test_update_notification_bind_status_updates_existing_row(repo: TgSessionRepository) -> None:
    repo.upsert('user-1', session_string='s', phone_tail4=None, telegram_user_id=None)

    repo.update_notification_bind_status('user-1', status='flood_wait', error='A wait of 30 seconds is required')

    entry = repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.notification_bind_status == 'flood_wait'
    assert entry.notification_bind_error == 'A wait of 30 seconds is required'


def test_update_notification_bind_status_does_not_touch_session_string(repo: TgSessionRepository) -> None:
    repo.upsert('user-1', session_string='keep-me', phone_tail4='1234', telegram_user_id=99)

    repo.update_notification_bind_status('user-1', status='success', error=None)

    assert repo.get_decrypted_session_string('user-1') == 'keep-me'
    entry = repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.phone_tail4 == '1234'
    assert entry.telegram_user_id == 99


def test_update_notification_bind_status_missing_user_is_noop(repo: TgSessionRepository) -> None:
    repo.update_notification_bind_status('nobody', status='success', error=None)  # must not raise
    assert repo.get_by_user_id('nobody') is None

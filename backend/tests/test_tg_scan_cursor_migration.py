"""Tests for Alembic migration 0021 — tg_watched_chat scan cursor columns.

Mirrors the harness in ``test_bt_remote_cleared_migration.py``: build a real
SQLite file, drive Alembic's programmatic API to a specific revision, seed
rows with raw SQL, then upgrade one revision further and inspect the result.

Covers all four columns 0021 adds: last_scanned_message_id/last_scanned_at
(surfaced on the API-facing TgWatchedChat model) and
scan_resume_offset_id/scan_pending_cursor (internal-only resumable-sweep
bookkeeping — see app.tg_downloader.catchup.TgCatchupService).
"""

from __future__ import annotations

import pathlib

import alembic.command
import alembic.config
import sqlalchemy

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ALEMBIC_INI = _BACKEND_ROOT / 'alembic.ini'
_ALEMBIC_DIR = _BACKEND_ROOT / 'alembic'


def _make_cfg(url: str) -> alembic.config.Config:
    cfg = alembic.config.Config(str(_ALEMBIC_INI))
    cfg.set_main_option('script_location', str(_ALEMBIC_DIR))
    cfg.set_main_option('sqlalchemy.url', url)
    # Skip alembic's fileConfig() call — see Database.run_baseline_migrations
    # for why (it would otherwise silence app.* loggers).
    cfg.attributes['skip_log_config'] = True
    return cfg


def test_0021_adds_scan_cursor_columns(tmp_path: pathlib.Path) -> None:
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    # Land exactly on 0020 — the schema right before this migration.
    alembic.command.upgrade(cfg, '0020')

    new_columns = {'last_scanned_message_id', 'last_scanned_at', 'scan_resume_offset_id', 'scan_pending_cursor'}

    engine = sqlalchemy.create_engine(url, future=True)
    with engine.connect() as conn:
        columns_before = {row[1] for row in conn.execute(sqlalchemy.text('PRAGMA table_info(tg_watched_chat)'))}
    assert not (new_columns & columns_before)

    alembic.command.upgrade(cfg, '0021')

    with engine.connect() as conn:
        columns_after = {row[1] for row in conn.execute(sqlalchemy.text('PRAGMA table_info(tg_watched_chat)'))}
    assert new_columns <= columns_after

    engine.dispose()


def test_0021_preserves_existing_rows_and_defaults_new_columns_to_null(tmp_path: pathlib.Path) -> None:
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    alembic.command.upgrade(cfg, '0020')

    engine = sqlalchemy.create_engine(url, future=True)
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text('INSERT INTO users (id, username, role) VALUES (:id, :username, :role)'),
            {'id': 'user-1', 'username': 'tester', 'role': 'downloader'},
        )
        conn.execute(
            sqlalchemy.text(
                'INSERT INTO tg_watched_chat '
                '(user_id, chat_id, chat_title, media_types, enabled, created_at) '
                "VALUES ('user-1', -100123, '既有頻道', '[\"video\"]', 1, '2026-08-01T00:00:00+00:00')"
            )
        )

    alembic.command.upgrade(cfg, '0021')

    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.text(
                'SELECT chat_title, last_scanned_message_id, last_scanned_at, '
                'scan_resume_offset_id, scan_pending_cursor '
                'FROM tg_watched_chat WHERE chat_id = -100123'
            )
        ).one()

    assert row.chat_title == '既有頻道'
    assert row.last_scanned_message_id is None
    assert row.last_scanned_at is None
    assert row.scan_resume_offset_id is None
    assert row.scan_pending_cursor is None

    engine.dispose()


def test_0021_downgrade_drops_the_columns(tmp_path: pathlib.Path) -> None:
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    alembic.command.upgrade(cfg, '0021')
    alembic.command.downgrade(cfg, '0020')

    engine = sqlalchemy.create_engine(url, future=True)
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(sqlalchemy.text('PRAGMA table_info(tg_watched_chat)'))}
    assert 'last_scanned_message_id' not in columns
    assert 'last_scanned_at' not in columns
    assert 'scan_resume_offset_id' not in columns
    assert 'scan_pending_cursor' not in columns
    engine.dispose()

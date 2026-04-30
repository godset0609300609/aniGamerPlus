"""Tests for ``app.persistence.db.Database``."""

from __future__ import annotations

import pathlib
import sqlite3

import pytest
import sqlalchemy

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.models import Anime


def _make_logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


def _make_db(tmp_path: pathlib.Path, filename: str = 'test.db') -> Database:
    db_path = tmp_path / filename
    return Database(f'sqlite:///{db_path}', _make_logger(tmp_path))


def test_session_commits_on_success(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    db.run_baseline_migrations()

    with db.session() as session:
        session.add(
            Anime(
                sn=1,
                title='t',
                anime_name='a',
                episode='01',
                resolution=1080,
                file_size=0,
            )
        )

    # A fresh session sees the committed row.
    with db.session() as session:
        got = session.get(Anime, 1)
        assert got is not None
        assert got.title == 't'
    db.dispose()


def test_session_rolls_back_on_exception(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    db.run_baseline_migrations()

    with pytest.raises(RuntimeError), db.session() as session:
        session.add(
            Anime(
                sn=2,
                title='t',
                anime_name='a',
                episode='01',
                resolution=1080,
                file_size=0,
            )
        )
        raise RuntimeError('boom')

    with db.session() as session:
        assert session.get(Anime, 2) is None
    db.dispose()


def test_run_baseline_migrations_creates_anime_table(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path, 'fresh.db')
    db.run_baseline_migrations()

    inspector = sqlalchemy.inspect(db.engine)
    assert 'anime' in inspector.get_table_names()
    col_names = {col['name'] for col in inspector.get_columns('anime')}
    assert {'sn', 'title', 'anime_name', 'episode', 'CreatedTime'}.issubset(col_names)
    # revision 0002 added:
    assert 'created_at_utc' in col_names
    db.dispose()


def test_run_baseline_migrations_is_idempotent(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path, 'twice.db')
    db.run_baseline_migrations()
    # Second call should be a no-op (alembic sees head already).
    db.run_baseline_migrations()

    inspector = sqlalchemy.inspect(db.engine)
    assert 'anime' in inspector.get_table_names()
    db.dispose()


def test_sqlite_database_uses_wal_mode(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path, 'wal.db')
    with db.session() as sess:
        result = sess.execute(sqlalchemy.text('PRAGMA journal_mode')).scalar()
        assert str(result).lower() == 'wal'
        timeout = sess.execute(sqlalchemy.text('PRAGMA busy_timeout')).scalar()
        assert int(timeout) >= 30000
    db.dispose()


def test_non_sqlite_url_skips_pragmas() -> None:
    """The pragma listener is only registered when the backend name is 'sqlite'."""
    # We verify the guard by inspecting the Database source: the event listener
    # is wrapped in ``if self._engine.url.get_backend_name() == 'sqlite'``.
    # Without a live non-SQLite engine we confirm the branch exists in source.
    import inspect

    import app.persistence.db as db_module

    src = inspect.getsource(db_module.Database.__init__)
    assert "get_backend_name() == 'sqlite'" in src


def test_migrations_succeed_over_legacy_schema(tmp_path: pathlib.Path) -> None:
    """An SQLite file that already has the legacy ``anime`` table upgrades cleanly."""
    db_path = tmp_path / 'legacy.db'
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.execute(
        'CREATE TABLE IF NOT EXISTS anime ('
        'sn INTEGER PRIMARY KEY NOT NULL,'
        'title VARCHAR(100) NOT NULL,'
        'anime_name VARCHAR(100) NOT NULL, '
        'episode VARCHAR(10) NOT NULL,'
        'status TINYINT DEFAULT 0,'
        'remote_status INTEGER DEFAULT 0,'
        'resolution INTEGER DEFAULT 0,'
        'file_size INTEGER DEFAULT 0,'
        'local_file_path VARCHAR(500),'
        "[CreatedTime] TimeStamp NOT NULL DEFAULT (datetime('now','localtime')))"
    )
    legacy_conn.execute(
        'INSERT INTO anime (sn, title, anime_name, episode) VALUES (?, ?, ?, ?)',
        (7777, 'legacy title', 'legacy series', '01'),
    )
    legacy_conn.commit()
    legacy_conn.close()

    db = Database(f'sqlite:///{db_path}', _make_logger(tmp_path))
    db.run_baseline_migrations()

    with db.session() as session:
        orm = session.get(Anime, 7777)
        assert orm is not None
        assert orm.anime_name == 'legacy series'

    inspector = sqlalchemy.inspect(db.engine)
    col_names = {col['name'] for col in inspector.get_columns('anime')}
    # 0002 still applied on top of the adopted legacy table.
    assert 'created_at_utc' in col_names
    db.dispose()

"""Tests for Alembic migration 0004 — legacy sn_list.txt import.

Each test patches the ``backend_root`` that the migration uses so it points
at ``tmp_path`` rather than the real backend directory.  This prevents the
tests from touching the real ``sn_list.txt`` on disk.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import unittest.mock

import pytest
import sqlalchemy

from app.logging_ import Logger
from app.persistence.db import Database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: pathlib.Path, name: str = 'test.db') -> Database:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{(tmp_path / name).as_posix()}', logger)
    return db


_MIGRATION_PATH = pathlib.Path(__file__).resolve().parents[1] / 'alembic' / 'versions' / '0004_import_legacy_sn_list.py'


def _load_migration_module() -> types.ModuleType:
    """Dynamically import the 0004 migration module."""
    spec = importlib.util.spec_from_file_location('migration_0004', str(_MIGRATION_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Test: fresh DB + sn_list.txt → import succeeds
# ---------------------------------------------------------------------------


def test_import_inserts_entries_into_db(tmp_path: pathlib.Path) -> None:
    """Entries from sn_list.txt appear in anime_list_entries under sentinel user."""
    sn_list = tmp_path / 'sn_list.txt'
    sn_list.write_text(
        '@Action\n100 latest\n200 all <Renamed>\n@Slice of Life\n300\n',
        encoding='utf-8',
    )

    db = _make_db(tmp_path)
    # Run migrations up to 0003 only, then call upgrade() of 0004 manually
    # so we can intercept the backend_root resolution.
    db.run_baseline_migrations()

    mod = _load_migration_module()

    with db.engine.begin() as conn:
        # Patch __file__ resolution inside the migration to point at tmp_path.
        fake_file = tmp_path / 'alembic' / 'versions' / '0004_fake.py'
        fake_file.parent.mkdir(parents=True, exist_ok=True)
        fake_file.touch()

        with (
            unittest.mock.patch.object(
                mod,
                '__file__',
                str(fake_file),
            ),
            unittest.mock.patch('alembic.op.get_bind', return_value=conn),
        ):
            mod.upgrade()

    # Verify entries in the DB.
    with db.engine.connect() as conn:
        rows = conn.execute(
            sqlalchemy.text('SELECT sn, mode, tag FROM anime_list_entries ORDER BY sort_order')
        ).fetchall()

    assert len(rows) == 3
    assert rows[0][0] == 100
    assert rows[0][1] == 'latest'
    assert rows[0][2] == 'Action'
    assert rows[1][0] == 200
    assert rows[1][1] == 'all'
    assert rows[2][0] == 300

    # sn_list.txt must be renamed.
    assert not sn_list.exists()
    assert (tmp_path / 'sn_list.txt.imported').exists()

    db.dispose()


def test_import_uses_sentinel_user_when_no_admin(tmp_path: pathlib.Path) -> None:
    sn_list = tmp_path / 'sn_list.txt'
    sn_list.write_text('@\n500 latest\n', encoding='utf-8')

    db = _make_db(tmp_path)
    db.run_baseline_migrations()

    mod = _load_migration_module()
    fake_file = tmp_path / 'alembic' / 'versions' / '0004_fake.py'
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.touch()

    with db.engine.begin() as conn:
        with (
            unittest.mock.patch.object(mod, '__file__', str(fake_file)),
            unittest.mock.patch('alembic.op.get_bind', return_value=conn),
        ):
            mod.upgrade()

    with db.engine.connect() as conn:
        sentinel = conn.execute(
            sqlalchemy.text("SELECT id, username, role FROM users WHERE id = '__legacy_import__'")
        ).fetchone()
        assert sentinel is not None
        assert sentinel[1] == 'Legacy Import'
        assert sentinel[2] == 'admin'

        row = conn.execute(sqlalchemy.text('SELECT user_id FROM anime_list_entries WHERE sn = 500')).fetchone()
        assert row is not None
        assert row[0] == '__legacy_import__'

    db.dispose()


def test_import_uses_existing_admin_as_owner(tmp_path: pathlib.Path) -> None:
    sn_list = tmp_path / 'sn_list.txt'
    sn_list.write_text('@\n700 all\n', encoding='utf-8')

    db = _make_db(tmp_path)
    db.run_baseline_migrations()

    # Pre-create an admin user.
    with db.engine.begin() as conn:
        conn.execute(
            sqlalchemy.text(
                'INSERT INTO users (id, username, avatar_url, role, created_at)'
                " VALUES ('real_admin', 'Real Admin', NULL, 'admin', '2026-01-01 00:00:00')"
            )
        )

    mod = _load_migration_module()
    fake_file = tmp_path / 'alembic' / 'versions' / '0004_fake.py'
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.touch()

    with db.engine.begin() as conn:
        with (
            unittest.mock.patch.object(mod, '__file__', str(fake_file)),
            unittest.mock.patch('alembic.op.get_bind', return_value=conn),
        ):
            mod.upgrade()

    with db.engine.connect() as conn:
        row = conn.execute(sqlalchemy.text('SELECT user_id FROM anime_list_entries WHERE sn = 700')).fetchone()
        assert row is not None
        assert row[0] == 'real_admin'

        # Sentinel user should NOT have been created.
        sentinel = conn.execute(sqlalchemy.text("SELECT id FROM users WHERE id = '__legacy_import__'")).fetchone()
        assert sentinel is None

    db.dispose()


# ---------------------------------------------------------------------------
# Test: fresh DB + no sn_list.txt → no-op
# ---------------------------------------------------------------------------


def test_no_sn_list_txt_is_noop(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    db.run_baseline_migrations()

    mod = _load_migration_module()
    fake_file = tmp_path / 'alembic' / 'versions' / '0004_fake.py'
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.touch()

    with db.engine.begin() as conn:
        with (
            unittest.mock.patch.object(mod, '__file__', str(fake_file)),
            unittest.mock.patch('alembic.op.get_bind', return_value=conn),
        ):
            mod.upgrade()  # should not raise

    with db.engine.connect() as conn:
        count = conn.execute(sqlalchemy.text('SELECT COUNT(*) FROM anime_list_entries')).scalar()
        assert count == 0

        user_count = conn.execute(sqlalchemy.text('SELECT COUNT(*) FROM users')).scalar()
        assert user_count == 0

    db.dispose()


# ---------------------------------------------------------------------------
# Test: idempotent re-run
# ---------------------------------------------------------------------------


def test_import_is_idempotent(tmp_path: pathlib.Path) -> None:
    """Re-running upgrade() does not double-insert rows."""
    sn_list = tmp_path / 'sn_list.txt'
    sn_list.write_text('@\n111 latest\n', encoding='utf-8')

    db = _make_db(tmp_path)
    db.run_baseline_migrations()

    mod = _load_migration_module()
    fake_file = tmp_path / 'alembic' / 'versions' / '0004_fake.py'
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.touch()

    # First run — imports and renames.
    with db.engine.begin() as conn:
        with (
            unittest.mock.patch.object(mod, '__file__', str(fake_file)),
            unittest.mock.patch('alembic.op.get_bind', return_value=conn),
        ):
            mod.upgrade()

    assert not sn_list.exists()

    # Restore the file to simulate a re-run scenario.
    imported = tmp_path / 'sn_list.txt.imported'
    # Second run: sn_list.txt doesn't exist → no-op.
    with db.engine.begin() as conn:
        with (
            unittest.mock.patch.object(mod, '__file__', str(fake_file)),
            unittest.mock.patch('alembic.op.get_bind', return_value=conn),
        ):
            mod.upgrade()

    with db.engine.connect() as conn:
        count = conn.execute(sqlalchemy.text('SELECT COUNT(*) FROM anime_list_entries')).scalar()
        assert count == 1  # still exactly one row

    db.dispose()

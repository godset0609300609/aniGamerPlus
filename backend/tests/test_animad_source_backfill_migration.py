"""Tests for Alembic migration 0020 — backfill legacy NULL task_history.source.

Mirrors the harness in ``test_bt_backfill_migration.py``: build a real
SQLite file, drive Alembic's programmatic API to a specific revision, seed
rows with raw SQL, then upgrade one revision further and inspect the result.
"""

from __future__ import annotations

import datetime
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


def _insert_task_history_row(
    conn: sqlalchemy.Connection,
    *,
    sn: int,
    filename: str,
    source: str | None,
    final_status: str = '下載完成',
) -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat()
    conn.execute(
        sqlalchemy.text(
            'INSERT INTO task_history '
            '(sn, filename, final_status, started_at, finished_at, retries, source) '
            'VALUES (:sn, :filename, :final_status, :now, :now, 0, :source)'
        ),
        {'sn': sn, 'filename': filename, 'final_status': final_status, 'now': now, 'source': source},
    )


def test_0020_backfills_null_source_rows_to_animad(tmp_path: pathlib.Path) -> None:
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    # Land exactly on 0019 — the schema right before this migration — so we
    # can seed pre-migration rows.
    alembic.command.upgrade(cfg, '0019')

    engine = sqlalchemy.create_engine(url, future=True)
    with engine.begin() as conn:
        _insert_task_history_row(conn, sn=1, filename='legacy-animad-1.mp4', source=None)
        _insert_task_history_row(conn, sn=2, filename='legacy-animad-2.mp4', source=None)
        _insert_task_history_row(conn, sn=3, filename='bilibili-episode.mp4', source='bilibili')
        _insert_task_history_row(conn, sn=4, filename='tg-episode.mp4', source='tg')
        _insert_task_history_row(conn, sn=5, filename='bt-episode.mp4', source='bt')
        _insert_task_history_row(conn, sn=6, filename='already-animad.mp4', source='animad')

    alembic.command.upgrade(cfg, '0020')

    with engine.connect() as conn:
        rows = dict(conn.execute(sqlalchemy.text('SELECT sn, source FROM task_history')).fetchall())

    assert rows[1] == 'animad'  # legacy NULL-source row backfilled
    assert rows[2] == 'animad'  # legacy NULL-source row backfilled
    assert rows[3] == 'bilibili'  # explicitly-sourced row untouched
    assert rows[4] == 'tg'  # explicitly-sourced row untouched
    assert rows[5] == 'bt'  # explicitly-sourced row untouched
    assert rows[6] == 'animad'  # already-animad row untouched (still 'animad')

    engine.dispose()


def test_0020_is_idempotent_when_run_again(tmp_path: pathlib.Path) -> None:
    """Re-running the UPDATE (e.g. via a second upgrade attempt) is harmless."""
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    alembic.command.upgrade(cfg, '0019')

    engine = sqlalchemy.create_engine(url, future=True)
    with engine.begin() as conn:
        _insert_task_history_row(conn, sn=1, filename='legacy-animad-1.mp4', source=None)
        _insert_task_history_row(conn, sn=2, filename='bilibili-episode.mp4', source='bilibili')

    alembic.command.upgrade(cfg, '0020')

    # Re-run the same UPDATE statement directly to confirm re-application is a no-op.
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("UPDATE task_history SET source = 'animad' WHERE source IS NULL"))

    with engine.connect() as conn:
        rows = dict(conn.execute(sqlalchemy.text('SELECT sn, source FROM task_history')).fetchall())

    assert rows[1] == 'animad'
    assert rows[2] == 'bilibili'  # never touched by either run

    engine.dispose()


def test_0020_no_op_on_empty_table(tmp_path: pathlib.Path) -> None:
    """Smoke check that ``upgrade()`` runs to completion with zero rows."""
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    alembic.command.upgrade(cfg, '0019')
    alembic.command.upgrade(cfg, '0020')  # must not raise on an empty task_history table

    engine = sqlalchemy.create_engine(url, future=True)
    with engine.connect() as conn:
        count = conn.execute(sqlalchemy.text('SELECT COUNT(*) FROM task_history')).scalar_one()
    assert count == 0
    engine.dispose()


def test_0020_downgrade_is_a_no_op(tmp_path: pathlib.Path) -> None:
    """Downgrade cannot un-backfill (indistinguishable from genuine animad rows), so it's a no-op."""
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    alembic.command.upgrade(cfg, '0019')

    engine = sqlalchemy.create_engine(url, future=True)
    with engine.begin() as conn:
        _insert_task_history_row(conn, sn=1, filename='legacy-animad-1.mp4', source=None)

    alembic.command.upgrade(cfg, '0020')
    alembic.command.downgrade(cfg, '0019')

    with engine.connect() as conn:
        source = conn.execute(sqlalchemy.text('SELECT source FROM task_history WHERE sn = 1')).scalar_one()
    assert source == 'animad'  # downgrade does not revert the backfill

    engine.dispose()

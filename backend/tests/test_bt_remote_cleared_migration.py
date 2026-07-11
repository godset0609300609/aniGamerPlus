"""Tests for Alembic migration 0017 — bt_feed_entry.remote_cleared_at column.

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


def test_0017_adds_remote_cleared_at_column(tmp_path: pathlib.Path) -> None:
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    # Land exactly on 0016 — the schema right before this migration.
    alembic.command.upgrade(cfg, '0016')

    engine = sqlalchemy.create_engine(url, future=True)
    with engine.connect() as conn:
        columns_before = {row[1] for row in conn.execute(sqlalchemy.text('PRAGMA table_info(bt_feed_entry)'))}
    assert 'remote_cleared_at' not in columns_before

    alembic.command.upgrade(cfg, '0017')

    with engine.connect() as conn:
        columns_after = {row[1] for row in conn.execute(sqlalchemy.text('PRAGMA table_info(bt_feed_entry)'))}
    assert 'remote_cleared_at' in columns_after

    engine.dispose()


def test_0017_preserves_existing_rows_and_defaults_new_column_to_null(tmp_path: pathlib.Path) -> None:
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    alembic.command.upgrade(cfg, '0016')

    engine = sqlalchemy.create_engine(url, future=True)
    now = datetime.datetime.now(datetime.UTC).isoformat()

    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text(
                'INSERT INTO bt_feed (name, url, title_key, link_key, enabled, created_at, updated_at) '
                "VALUES ('feed', 'https://feed.example/rss', 'title', 'link', 1, :now, :now)"
            ),
            {'now': now},
        )
        feed_id = conn.execute(sqlalchemy.text('SELECT id FROM bt_feed')).scalar_one()

        conn.execute(
            sqlalchemy.text(
                'INSERT INTO bt_feed_entry '
                '(feed_id, guid, title, link, fetched_at, putio_transfer_id, putio_status, local_path) '
                'VALUES (:feed_id, :guid, :title, :link, :fetched_at, :transfer_id, :status, :local_path)'
            ),
            {
                'feed_id': feed_id,
                'guid': 'guid-landed',
                'title': 'A landed episode',
                'link': 'https://link/1',
                'fetched_at': now,
                'transfer_id': 555,
                'status': 'SEEDING',
                'local_path': 'landed.mp4',
            },
        )

    alembic.command.upgrade(cfg, '0017')

    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.text(
                'SELECT guid, title, link, putio_transfer_id, putio_status, local_path, remote_cleared_at '
                "FROM bt_feed_entry WHERE guid = 'guid-landed'"
            )
        ).one()

    assert row.guid == 'guid-landed'
    assert row.title == 'A landed episode'
    assert row.link == 'https://link/1'
    assert row.putio_transfer_id == 555
    assert row.putio_status == 'SEEDING'
    assert row.local_path == 'landed.mp4'
    assert row.remote_cleared_at is None  # new column defaults to NULL for pre-existing rows

    engine.dispose()


def test_0017_downgrade_drops_the_column(tmp_path: pathlib.Path) -> None:
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    alembic.command.upgrade(cfg, '0017')
    alembic.command.downgrade(cfg, '0016')

    engine = sqlalchemy.create_engine(url, future=True)
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(sqlalchemy.text('PRAGMA table_info(bt_feed_entry)'))}
    assert 'remote_cleared_at' not in columns
    engine.dispose()

"""Tests for Alembic migration 0015 — backfill bt_feed_entry.title to 繁體.

Mirrors the harness in ``test_migrations_legacy_import.py``: build a real
SQLite file, drive Alembic's programmatic API to a specific revision, seed
rows with raw SQL, then upgrade one revision further and inspect the result.
"""

from __future__ import annotations

import datetime
import pathlib

import alembic.command
import alembic.config
import opencc
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


def test_backfill_converts_simplified_titles_and_leaves_traditional_ones_untouched(
    tmp_path: pathlib.Path,
) -> None:
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    # Land exactly on 0014 — the schema right before the backfill migration —
    # so we can seed pre-migration rows.
    alembic.command.upgrade(cfg, '0014')

    engine = sqlalchemy.create_engine(url, future=True)
    now = datetime.datetime.now(datetime.UTC).isoformat()

    simplified_title = '【豌豆字幕组】关于我转生变成史莱姆'
    traditional_title = '【豌豆字幕組】關於我轉生變成史萊姆 第二季'
    no_han_title = 'Some English Only Title 01'

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
                'INSERT INTO bt_feed_entry (feed_id, guid, title, link, fetched_at) '
                'VALUES (:feed_id, :guid, :title, :link, :fetched_at)'
            ),
            [
                {
                    'feed_id': feed_id,
                    'guid': 'guid-simplified',
                    'title': simplified_title,
                    'link': 'https://link/1',
                    'fetched_at': now,
                },
                {
                    'feed_id': feed_id,
                    'guid': 'guid-traditional',
                    'title': traditional_title,
                    'link': 'https://link/2',
                    'fetched_at': now,
                },
                {
                    'feed_id': feed_id,
                    'guid': 'guid-no-han',
                    'title': no_han_title,
                    'link': 'https://link/3',
                    'fetched_at': now,
                },
            ],
        )

    # Apply just the backfill migration.
    alembic.command.upgrade(cfg, '0015')

    expected_title = opencc.OpenCC('s2t').convert(simplified_title)
    with engine.connect() as conn:
        rows = dict(
            conn.execute(sqlalchemy.text('SELECT guid, title FROM bt_feed_entry')).fetchall()
        )

    assert rows['guid-simplified'] == expected_title
    assert rows['guid-simplified'] != simplified_title  # sanity: the seed data really was 簡體
    assert rows['guid-traditional'] == traditional_title  # already 繁體 — untouched
    assert rows['guid-no-han'] == no_han_title  # no Han characters — untouched

    engine.dispose()


def test_backfill_is_unconditional_regardless_of_hanzi_convert_setting(tmp_path: pathlib.Path) -> None:
    """The migration does not read app settings at all — it always converts.

    This is a smoke check that ``upgrade()`` runs to completion on an empty
    table (no settings/config table dependency) and is a no-op when there
    are zero rows.
    """
    db_path = tmp_path / 'test.db'
    url = f'sqlite:///{db_path.as_posix()}'
    cfg = _make_cfg(url)

    alembic.command.upgrade(cfg, '0014')
    alembic.command.upgrade(cfg, '0015')  # must not raise on an empty bt_feed_entry table

    engine = sqlalchemy.create_engine(url, future=True)
    with engine.connect() as conn:
        count = conn.execute(sqlalchemy.text('SELECT COUNT(*) FROM bt_feed_entry')).scalar_one()
    assert count == 0
    engine.dispose()

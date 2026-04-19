"""Tests for :class:`AnimeListService`."""

from __future__ import annotations

import datetime as dt
import pathlib
from typing import Any

from app.logging_ import Logger
from app.models import AnimeListEntry
from app.persistence.anime_list_repo import AnimeListEntryDTO, AnimeListEntryRepository
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths
from app.persistence.repositories import AnimeRepository
from app.persistence.sn_list_repo import SnListRepository
from app.persistence.user_repo import UserRepository, UserRow
from app.services.animelist_service import AnimeListService


def _make_service(
    tmp_path: pathlib.Path,
    content: str = '',
    seed_rows: list[dict[str, Any]] | None = None,
) -> tuple[AnimeListService, SnListRepository, AnimeRepository, Database]:
    """Build an :class:`AnimeListService` with real repos on ``tmp_path``.

    ``seed_rows`` — optional list of dicts whose keys match
    :meth:`AnimeRepository.insert` kwargs; a ``status`` key is honoured via
    a follow-up update so we can mimic partially-downloaded series.

    The caller is responsible for disposing the returned :class:`Database`
    once it's done with the service; leaving sqlite3 connections live past
    the test triggers unraisable-exception warnings.
    """
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)
    sn_list_repo = SnListRepository(paths, logger)
    if content:
        sn_list_repo.write_raw(content)

    db = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    db.run_baseline_migrations()
    anime_repo = AnimeRepository(db)

    if seed_rows:
        for row in seed_rows:
            status = row.pop('status', 0)
            anime_repo.insert(**row)
            if status:
                anime_repo.update(row['sn'], status=status)

    service = AnimeListService(sn_list_repo, anime_repo)
    return service, sn_list_repo, anime_repo, db


FIXTURE = """\
# top-level comment that should be ignored

@2024冬季番
11111 latest  # 第一部
# 22222 all  # 先停一下

@2024春季番

44444
55555 largest-sn  # 春番備注
"""


def test_parse_realistic_fixture(tmp_path: pathlib.Path) -> None:
    service, _, _, db = _make_service(tmp_path, FIXTURE)
    try:
        entries = service.list()

        assert [e.sn for e in entries] == [11111, 22222, 44444, 55555]

        e1, e2, e4, e5 = entries

        assert e1.enabled and e1.mode == 'latest' and e1.tag == '2024冬季番'
        assert e1.comment == '第一部'

        # disabled row: ``# 22222 all  # 先停一下``
        assert e2.enabled is False
        assert e2.sn == 22222 and e2.mode == 'all'
        assert e2.comment == '先停一下'
        assert e2.tag == '2024冬季番'

        # bare sn, no mode, in second tag group
        assert e4.enabled and e4.mode is None and e4.tag == '2024春季番'
        assert e4.comment == ''

        # largest-sn + comment
        assert e5.enabled and e5.mode == 'largest-sn'
        assert e5.comment == '春番備注' and e5.tag == '2024春季番'
    finally:
        db.dispose()


def test_round_trip_parse_serialize_parse(tmp_path: pathlib.Path) -> None:
    service, sn_list_repo, _, db = _make_service(tmp_path, FIXTURE)
    try:
        parsed_once = service.list()

        service.replace_all(parsed_once)
        serialized = sn_list_repo.read_raw()

        service2, _, _, db2 = _make_service(tmp_path / 'sub', serialized)
        try:
            parsed_twice = service2.list()
        finally:
            db2.dispose()

        def _strip_derived(entries: Any) -> list[dict[str, Any]]:
            return [
                {
                    'sn': e.sn,
                    'enabled': e.enabled,
                    'mode': e.mode,
                    'tag': e.tag,
                    'comment': e.comment,
                }
                for e in entries
            ]

        assert _strip_derived(parsed_once) == _strip_derived(parsed_twice)
    finally:
        db.dispose()


def test_disabled_round_trip_single_row(tmp_path: pathlib.Path) -> None:
    content = '@\n# 12345 latest\n'
    service, sn_list_repo, _, db = _make_service(tmp_path, content)
    try:
        entries = service.list()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.enabled is False
        assert entry.sn == 12345
        assert entry.mode == 'latest'
        assert entry.comment == ''

        # Serialize back and confirm the ``# `` prefix is there.
        service.replace_all(entries)
        assert '# 12345 latest' in sn_list_repo.read_raw()
    finally:
        db.dispose()


def test_unknown_mode_falls_back_to_none(tmp_path: pathlib.Path) -> None:
    service, _, _, db = _make_service(tmp_path, '@\n99999 bogus\n')
    try:
        entries = service.list()
        assert len(entries) == 1
        assert entries[0].mode is None
    finally:
        db.dispose()


def test_group_order_and_first_occurrence(tmp_path: pathlib.Path) -> None:
    service, sn_list_repo, _, db = _make_service(tmp_path, '')
    try:
        inputs = [
            AnimeListEntry(sn=1, tag='A'),
            AnimeListEntry(sn=2, tag='B'),
            AnimeListEntry(sn=3, tag='A'),  # should be grouped back with sn=1
        ]
        service.replace_all(inputs)
        text = sn_list_repo.read_raw()

        # Category A comes first and contains both 1 and 3.
        a_pos = text.index('@A')
        b_pos = text.index('@B')
        assert a_pos < b_pos
        lines_after_a = text[a_pos:b_pos].splitlines()
        sn_values = [ln.strip().split()[0] for ln in lines_after_a if ln.strip() and not ln.strip().startswith('@')]
        assert '1' in sn_values
        assert '3' in sn_values
        assert '2' not in sn_values
    finally:
        db.dispose()


def test_cached_anime_name_shows_without_download(tmp_path: pathlib.Path) -> None:
    """Goal A: when an entry has cached anime_name, _enrich shows it even
    without any row in the anime (downloaded) table."""
    from app.persistence.anime_list_repo import AnimeListEntryDTO, AnimeListEntryRepository
    from app.persistence.user_repo import UserRepository

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    db.run_baseline_migrations()
    try:
        user_repo = UserRepository(db)
        user_repo.upsert(id='u1', username='User1', avatar_url=None, role='downloader')
        entry_repo = AnimeListEntryRepository(db)
        entry_repo.replace_all_for_user(
            'u1',
            [AnimeListEntryDTO(sn=777, anime_name='黃泉使者')],
        )

        from app.persistence.repositories import AnimeRepository
        from app.persistence.sn_list_repo import SnListRepository

        sn_list_repo = SnListRepository(paths, logger)
        anime_repo = AnimeRepository(db)

        service = AnimeListService(sn_list_repo, anime_repo, entry_repo)
        from app.persistence.user_repo import UserRow
        import datetime as dt

        user = UserRow(
            id='u1',
            username='User1',
            avatar_url=None,
            role='downloader',
            created_at=dt.datetime.now(dt.timezone.utc),
            last_login_at=None,
        )
        entries = service.list_entries(user)
        assert len(entries) == 1
        assert entries[0].anime_name == '黃泉使者'
        # No downloads yet so counts are 0.
        assert entries[0].known_episodes == 0
        assert entries[0].downloaded_episodes == 0
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# DB enrichment
# ---------------------------------------------------------------------------


_SEED_ROWS = [
    {
        'sn': 100,
        'title': 'OneAnime [1]',
        'anime_name': 'OneAnime',
        'episode': '1',
        'resolution': 0,
        'file_size': 0,
        'status': 1,
    },
    {
        'sn': 101,
        'title': 'OneAnime [2]',
        'anime_name': 'OneAnime',
        'episode': '2',
        'resolution': 0,
        'file_size': 0,
        'status': 1,
    },
    {
        'sn': 102,
        'title': 'OneAnime [3]',
        'anime_name': 'OneAnime',
        'episode': '3',
        'resolution': 0,
        'file_size': 0,
        'status': 0,
    },
    {
        'sn': 200,
        'title': 'TwoAnime [1]',
        'anime_name': 'TwoAnime',
        'episode': '1',
        'resolution': 0,
        'file_size': 0,
        'status': 1,
    },
]


def test_enrichment_counts_episodes(tmp_path: pathlib.Path) -> None:
    content = '@\n100\n200 latest\n9999\n'
    # The helper mutates each dict, so give it a fresh list.
    rows = [dict(r) for r in _SEED_ROWS]
    service, _, _, db = _make_service(tmp_path, content, seed_rows=rows)
    try:
        entries = service.list()

        by_sn = {e.sn: e for e in entries}
        assert by_sn[100].anime_name == 'OneAnime'
        assert by_sn[100].downloaded_episodes == 2
        assert by_sn[100].known_episodes == 3

        assert by_sn[200].anime_name == 'TwoAnime'
        assert by_sn[200].downloaded_episodes == 1
        assert by_sn[200].known_episodes == 1

        # sn not in the DB: anime_name stays None and counts at 0.
        assert by_sn[9999].anime_name is None
        assert by_sn[9999].downloaded_episodes == 0
        assert by_sn[9999].known_episodes == 0
    finally:
        db.dispose()


def test_enrichment_missing_rows_leaves_counts_zero(tmp_path: pathlib.Path) -> None:
    """Unknown sn → fields stay at defaults."""
    service, _, _, db = _make_service(tmp_path, '@\n100\n')
    try:
        entries = service.list()
        assert len(entries) == 1
        assert entries[0].anime_name is None
        assert entries[0].downloaded_episodes == 0
        assert entries[0].known_episodes == 0
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# Bug-fix: admin delete-all / remove-owner-from-payload should persist
# ---------------------------------------------------------------------------


def _make_service_with_repos(
    tmp_path: pathlib.Path,
) -> tuple[AnimeListService, AnimeListEntryRepository, UserRepository, Database]:
    """Build a full RBAC-aware service backed by a real SQLite DB."""
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    db.run_baseline_migrations()
    sn_list_repo = SnListRepository(paths, logger)
    anime_repo = AnimeRepository(db)
    entry_repo = AnimeListEntryRepository(db)
    user_repo = UserRepository(db)
    service = AnimeListService(sn_list_repo, anime_repo, entry_repo, user_repo)
    return service, entry_repo, user_repo, db


def _make_user_row(uid: str, role: str) -> UserRow:
    return UserRow(
        id=uid,
        username=f'User-{uid}',
        avatar_url=None,
        role=role,
        created_at=dt.datetime.now(dt.timezone.utc),
        last_login_at=None,
    )


def test_admin_delete_all_entries_persists_empty_list(tmp_path: pathlib.Path) -> None:
    """Saving an empty list as admin must clear the admin's own entries."""
    service, entry_repo, user_repo, db = _make_service_with_repos(tmp_path)
    try:
        user_repo.upsert(id='admin1', username='Admin', avatar_url=None, role='admin')
        entry_repo.replace_all_for_user(
            'admin1',
            [AnimeListEntryDTO(sn=111), AnimeListEntryDTO(sn=222)],
        )

        admin = _make_user_row('admin1', 'admin')
        service.replace_entries(admin, [])

        remaining = entry_repo.list_for_user('admin1')
        assert remaining == [], f'Expected empty list, got {remaining}'
    finally:
        db.dispose()


def test_admin_removes_owner_entirely_clears_their_slice(tmp_path: pathlib.Path) -> None:
    """When admin saves without any entries for owner B, B's slice must be wiped."""
    service, entry_repo, user_repo, db = _make_service_with_repos(tmp_path)
    try:
        user_repo.upsert(id='admin1', username='Admin', avatar_url=None, role='admin')
        user_repo.upsert(id='ownerB', username='OwnerB', avatar_url=None, role='downloader')
        entry_repo.replace_all_for_user('admin1', [AnimeListEntryDTO(sn=10)])
        entry_repo.replace_all_for_user('ownerB', [AnimeListEntryDTO(sn=20), AnimeListEntryDTO(sn=21)])

        admin = _make_user_row('admin1', 'admin')
        # Save only admin1's entry; owner B is entirely absent from the payload.
        admin_entry = AnimeListEntry(sn=10, enabled=True, mode=None, tag='', season=1, comment='', owner_id='admin1')
        service.replace_entries(admin, [admin_entry])

        owner_b_remaining = entry_repo.list_for_user('ownerB')
        assert owner_b_remaining == [], f"Owner B's slice should be empty, got {owner_b_remaining}"

        # admin1's own entry must still be there.
        admin_remaining = entry_repo.list_for_user('admin1')
        assert [e.sn for e in admin_remaining] == [10]
    finally:
        db.dispose()

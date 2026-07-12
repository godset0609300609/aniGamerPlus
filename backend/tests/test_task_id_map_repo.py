"""Tests for TaskIdMapRepository."""

from __future__ import annotations

import collections.abc
import pathlib

import pytest

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths
from app.persistence.task_id_map_repo import BASE_OFFSET, TaskIdMapRepository


@pytest.fixture
def db(tmp_path: pathlib.Path) -> collections.abc.Iterator[Database]:
    """Yield an on-disk SQLite Database; disposes the engine on teardown.

    The engine is disposed on teardown so sqlite3 connection-finaliser warnings
    don't leak into pytest's unraisable-exception hook.
    """
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)
    database = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    database.run_baseline_migrations()
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def repo(db: Database) -> TaskIdMapRepository:
    return TaskIdMapRepository(db)


def test_allocate_returns_base_offset_plus_row_id(repo: TaskIdMapRepository) -> None:
    sn = repo.allocate(source='bilibili', external_id='BV1xx411c7mD')
    assert sn > BASE_OFFSET
    assert sn == BASE_OFFSET + 1


def test_allocate_autoincrement(repo: TaskIdMapRepository) -> None:
    sn1 = repo.allocate(source='bilibili', external_id='BV1aa111a1aA')
    sn2 = repo.allocate(source='bilibili', external_id='BV1bb222b2bB')
    assert sn2 == sn1 + 1


def test_allocate_deduplicate_same_pair(repo: TaskIdMapRepository) -> None:
    sn1 = repo.allocate(source='bilibili', external_id='BV1xx411c7mD')
    sn2 = repo.allocate(source='bilibili', external_id='BV1xx411c7mD')
    assert sn1 == sn2


def test_allocate_different_sources_different_ids(repo: TaskIdMapRepository) -> None:
    sn_bilibili = repo.allocate(source='bilibili', external_id='video123')
    sn_youtube = repo.allocate(source='youtube', external_id='video123')
    assert sn_bilibili != sn_youtube


def test_base_offset_is_two_to_31(repo: TaskIdMapRepository) -> None:
    assert BASE_OFFSET == 2**31

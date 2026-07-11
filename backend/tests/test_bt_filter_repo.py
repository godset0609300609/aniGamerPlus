"""Tests for BtFilterRepository."""

from __future__ import annotations

import collections.abc
import pathlib

import pytest

from app.logging_ import Logger
from app.models import BtFilter
from app.persistence.bt_filter_repo import BtFilterRepository
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths


@pytest.fixture
def db(tmp_path: pathlib.Path) -> collections.abc.Iterator[Database]:
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)
    database = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    database.run_baseline_migrations()
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def repo(db: Database) -> BtFilterRepository:
    return BtFilterRepository(db)


def test_list_all_empty_initially(repo: BtFilterRepository) -> None:
    assert repo.list_all() == []


def test_replace_all_persists_filters(repo: BtFilterRepository) -> None:
    repo.replace_all([
        BtFilter(name='f1', keywords=['LoliHouse', '1080'], sort_order=0),
        BtFilter(name='f2', keywords=['Sub'], sort_order=1),
    ])
    result = repo.list_all()
    assert [f.name for f in result] == ['f1', 'f2']
    assert all(f.id is not None for f in result)


def test_replace_all_keywords_json_round_trip(repo: BtFilterRepository) -> None:
    keywords = ['LoliHouse', 'Hikaru ga Shinda Natsu', '1080', '繁']
    repo.replace_all([BtFilter(name='f1', keywords=keywords)])
    [result] = repo.list_all()
    assert result.keywords == keywords


def test_replace_all_empty_keywords_round_trip(repo: BtFilterRepository) -> None:
    repo.replace_all([BtFilter(name='f1', keywords=[])])
    [result] = repo.list_all()
    assert result.keywords == []


def test_replace_all_is_transactional_swap(repo: BtFilterRepository) -> None:
    repo.replace_all([BtFilter(name='old1'), BtFilter(name='old2')])
    assert len(repo.list_all()) == 2

    repo.replace_all([BtFilter(name='new1')])
    result = repo.list_all()
    assert [f.name for f in result] == ['new1']


def test_replace_all_with_empty_list_clears_table(repo: BtFilterRepository) -> None:
    repo.replace_all([BtFilter(name='old1')])
    repo.replace_all([])
    assert repo.list_all() == []


def test_list_all_ordered_by_sort_order(repo: BtFilterRepository) -> None:
    repo.replace_all([
        BtFilter(name='third', sort_order=2),
        BtFilter(name='first', sort_order=0),
        BtFilter(name='second', sort_order=1),
    ])
    result = repo.list_all()
    assert [f.name for f in result] == ['first', 'second', 'third']


def test_replace_all_preserves_enabled_flag(repo: BtFilterRepository) -> None:
    repo.replace_all([
        BtFilter(name='on', enabled=True),
        BtFilter(name='off', enabled=False),
    ])
    result = {f.name: f.enabled for f in repo.list_all()}
    assert result == {'on': True, 'off': False}


def test_replace_all_new_filters_get_created_at(repo: BtFilterRepository) -> None:
    repo.replace_all([BtFilter(name='f1')])
    [result] = repo.list_all()
    assert result.created_at
    assert result.updated_at


def test_replace_all_preserves_existing_created_at(repo: BtFilterRepository) -> None:
    repo.replace_all([BtFilter(name='f1')])
    [first] = repo.list_all()

    repo.replace_all([
        BtFilter(name='f1', created_at=first.created_at, keywords=['edited']),
    ])
    [second] = repo.list_all()
    assert second.created_at == first.created_at

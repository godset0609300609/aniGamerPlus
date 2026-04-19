"""Tests for ``AnimeRepository``."""

from __future__ import annotations

import pathlib
from collections.abc import Iterator

import pytest

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.repositories import AnimeRepository


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> Iterator[AnimeRepository]:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{tmp_path / "repo.db"}', logger)
    db.run_baseline_migrations()
    try:
        yield AnimeRepository(db)
    finally:
        db.dispose()


def test_insert_and_read_round_trip(repo: AnimeRepository) -> None:
    repo.insert(
        sn=1,
        title='t',
        anime_name='a',
        episode='01',
        resolution=1080,
        file_size=100,
        local_file_path='/tmp/a.mp4',
    )
    row = repo.read(1)
    assert row is not None
    assert row.sn == 1
    assert row.title == 't'
    assert row.anime_name == 'a'
    assert row.episode == '01'
    assert row.resolution == 1080
    assert row.file_size == 100
    assert row.local_file_path == '/tmp/a.mp4'
    assert row.status == 0
    assert row.remote_status == 0


def test_update_only_sets_provided_fields(repo: AnimeRepository) -> None:
    repo.insert(
        sn=2,
        title='t',
        anime_name='a',
        episode='01',
        resolution=1080,
        file_size=100,
    )
    repo.update(2, status=1)
    row = repo.read(2)
    assert row is not None
    assert row.status == 1
    # Other fields are untouched.
    assert row.title == 't'
    assert row.resolution == 1080
    assert row.file_size == 100


def test_count_by_anime_name(repo: AnimeRepository) -> None:
    repo.insert(sn=10, title='t1', anime_name='series-a', episode='01', resolution=1080, file_size=1)
    repo.insert(sn=11, title='t2', anime_name='series-a', episode='02', resolution=1080, file_size=1)
    repo.insert(sn=12, title='t3', anime_name='series-a', episode='03', resolution=1080, file_size=1)
    repo.insert(sn=20, title='t4', anime_name='series-b', episode='01', resolution=1080, file_size=1)

    repo.update(10, status=1)
    repo.update(12, status=1)

    known, downloaded = repo.count_by_anime_name('series-a')
    assert known == 3
    assert downloaded == 2

    known_b, downloaded_b = repo.count_by_anime_name('series-b')
    assert known_b == 1
    assert downloaded_b == 0


def test_read_all_returns_rows_sorted_by_sn(repo: AnimeRepository) -> None:
    repo.insert(sn=30, title='t', anime_name='a', episode='03', resolution=1080, file_size=1)
    repo.insert(sn=10, title='t', anime_name='a', episode='01', resolution=1080, file_size=1)
    repo.insert(sn=20, title='t', anime_name='a', episode='02', resolution=1080, file_size=1)

    rows = repo.read_all()
    assert [row.sn for row in rows] == [10, 20, 30]


def test_read_missing_sn_returns_none(repo: AnimeRepository) -> None:
    assert repo.read(9999) is None


def test_update_with_no_fields_is_noop(repo: AnimeRepository) -> None:
    repo.insert(sn=5, title='t', anime_name='a', episode='01', resolution=1080, file_size=1)
    # No kwargs — should neither raise nor mutate.
    repo.update(5)
    row = repo.read(5)
    assert row is not None
    assert row.title == 't'


def test_update_many_fields_at_once(repo: AnimeRepository) -> None:
    repo.insert(sn=6, title='old', anime_name='old-name', episode='01', resolution=720, file_size=1)
    repo.update(
        6,
        status=1,
        resolution=1080,
        title='new',
        anime_name='new-name',
        file_size=42,
        local_file_path='/p.mp4',
        remote_status=1,
        episode='02',
    )
    row = repo.read(6)
    assert row is not None
    assert row.status == 1
    assert row.resolution == 1080
    assert row.title == 'new'
    assert row.anime_name == 'new-name'
    assert row.file_size == 42
    assert row.local_file_path == '/p.mp4'
    assert row.remote_status == 1
    assert row.episode == '02'

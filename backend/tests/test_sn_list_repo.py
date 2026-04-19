"""Tests for ``SnListRepository``."""

from __future__ import annotations

import pathlib

import pytest

from app.logging_ import Logger
from app.persistence.paths import WorkspacePaths
from app.persistence.sn_list_repo import SnListRepository


@pytest.fixture
def paths(tmp_path: pathlib.Path) -> WorkspacePaths:
    return WorkspacePaths.detect(working_dir=tmp_path)


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture
def repo(paths: WorkspacePaths, logger: Logger) -> SnListRepository:
    return SnListRepository(paths, logger)


def test_read_raw_missing_file_returns_empty_string(repo: SnListRepository) -> None:
    assert repo.read_raw() == ''


def test_write_and_read_round_trip(repo: SnListRepository, paths: WorkspacePaths) -> None:
    content = '@分類1\n12345 latest\n67890 all <自訂名>\n# 注釋行\n'
    repo.write_raw(content)
    assert paths.sn_list_path.exists()
    assert repo.read_raw() == content


def test_parse_legacy_matches_legacy_semantics(
    repo: SnListRepository,
) -> None:
    content = (
        '@動作冒險\n'
        '12345 latest\n'
        '67890 all <自訂名>\n'
        '# 純注釋\n'
        '11111 nonsense-mode\n'  # invalid mode -> default
        '@ \n'  # reset tag
        '22222\n'  # no mode -> default
    )
    repo.write_raw(content)

    out = repo.parse_legacy(default_mode='latest')
    assert out[12345] == {'mode': 'latest', 'tag': '動作冒險', 'rename': ''}
    assert out[67890] == {'mode': 'all', 'tag': '動作冒險', 'rename': '自訂名'}
    assert out[11111] == {'mode': 'latest', 'tag': '動作冒險', 'rename': ''}
    assert out[22222] == {'mode': 'latest', 'tag': '', 'rename': ''}


def test_parse_legacy_empty_file_returns_empty_dict(repo: SnListRepository, paths: WorkspacePaths) -> None:
    paths.sn_list_path.write_text('', encoding='utf-8')
    assert repo.parse_legacy(default_mode='latest') == {}

"""Tests for ``CookieRepository``."""

from __future__ import annotations

import pathlib
import threading

import pytest

from app.logging_ import Logger
from app.persistence.cookie_repo import CookieRepository, _parse_cookie_line
from app.persistence.paths import WorkspacePaths


@pytest.fixture
def paths(tmp_path: pathlib.Path) -> WorkspacePaths:
    return WorkspacePaths.detect(working_dir=tmp_path)


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture
def repo(paths: WorkspacePaths, logger: Logger) -> CookieRepository:
    return CookieRepository(paths, logger)


def test_parse_cookie_line() -> None:
    parsed = _parse_cookie_line('BAHARUNE=abc; CURRENT_USER_VIP=1')
    assert parsed == {'BAHARUNE': 'abc', 'CURRENT_USER_VIP': '1'}


def test_renew_writes_single_line_format(repo: CookieRepository, paths: WorkspacePaths) -> None:
    repo.renew({'a': '1', 'b': '2'})
    content = paths.cookie_path.read_text(encoding='utf-8')
    assert content == 'a=1; b=2'
    assert '\n' not in content


def test_invalidate_moves_cookie_to_invalid_path(repo: CookieRepository, paths: WorkspacePaths) -> None:
    repo.renew({'BAHARUNE': 'xyz'})
    assert paths.cookie_path.exists()

    repo.invalidate()
    assert not paths.cookie_path.exists()
    assert paths.invalid_cookie_path.exists()
    assert paths.invalid_cookie_path.read_text(encoding='utf-8') == 'BAHARUNE=xyz'


def test_renew_is_thread_safe(repo: CookieRepository, paths: WorkspacePaths) -> None:
    """20 concurrent renew() calls — final file must be a valid single-line cookie."""
    threads: list[threading.Thread] = []
    for i in range(20):
        cookie = {f'k{i}': str(i), 'shared': str(i)}
        threads.append(threading.Thread(target=repo.renew, args=(cookie,)))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    content = paths.cookie_path.read_text(encoding='utf-8')
    # Exactly one line (no partial writes).
    assert '\n' not in content
    # Parses cleanly back to a dict.
    parsed = repo.load()
    assert 'shared' in parsed
    # The shared value is always numeric string of digits, never garbled.
    assert parsed['shared'].isdigit()

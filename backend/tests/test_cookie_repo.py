"""Tests for ``CookieRepository``."""

from __future__ import annotations

import errno
import os
import pathlib
import platform
import threading
import unittest.mock

import pytest

from app.logging_ import Logger
from app.persistence.cookie_repo import CookieRepository, _parse_cookie_line
from app.persistence.file_utils import atomic_write_text
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


# ---------------------------------------------------------------------------
# atomic_write_text — EBUSY / fallback behaviour
# ---------------------------------------------------------------------------


def test_atomic_write_falls_back_on_ebusy(tmp_path: pathlib.Path) -> None:
    """EBUSY from os.replace must trigger an in-place write and temp cleanup."""
    target = tmp_path / 'cookie.txt'
    target.write_text('old', encoding='utf-8')

    replace_calls: list[tuple[str, str]] = []

    def patched_replace(src: str, dst: str) -> None:
        replace_calls.append((src, dst))
        raise OSError(errno.EBUSY, 'Device or resource busy', src)

    with unittest.mock.patch('app.persistence.file_utils.os.replace', side_effect=patched_replace):
        atomic_write_text(target, 'hello')

    # os.replace was attempted exactly once.
    assert len(replace_calls) == 1
    # The file now contains the new content (in-place fallback worked).
    assert target.read_text(encoding='utf-8') == 'hello'
    # The temp file was cleaned up — no leftover siblings.
    siblings = [p for p in tmp_path.iterdir() if p != target]
    assert siblings == [], f'unexpected temp files left: {siblings}'


def test_atomic_write_still_uses_replace_when_possible(tmp_path: pathlib.Path) -> None:
    """Normal path: os.replace is called and the file gets the new content."""
    target = tmp_path / 'cookie.txt'

    replace_calls: list[tuple[str, str]] = []
    original_replace = os.replace

    def tracking_replace(src: str, dst: str) -> None:
        replace_calls.append((src, dst))
        original_replace(src, dst)

    with unittest.mock.patch('app.persistence.file_utils.os.replace', side_effect=tracking_replace):
        atomic_write_text(target, 'world')

    assert len(replace_calls) == 1
    assert target.read_text(encoding='utf-8') == 'world'
    # No leftover temp file after successful replace.
    siblings = [p for p in tmp_path.iterdir() if p != target]
    assert siblings == []


def test_atomic_write_propagates_non_ebusy_oserror(tmp_path: pathlib.Path) -> None:
    """Non-EBUSY OSError (e.g. EPERM) must propagate, not be swallowed."""
    target = tmp_path / 'cookie.txt'

    def failing_replace(src: str, dst: str) -> None:
        raise OSError(errno.EPERM, 'Operation not permitted', src)

    with (
        unittest.mock.patch('app.persistence.file_utils.os.replace', side_effect=failing_replace),
        pytest.raises(OSError) as exc_info,
    ):
        atomic_write_text(target, 'data')

    assert exc_info.value.errno == errno.EPERM
    # Temp file must be cleaned up even though we re-raised.
    siblings = [p for p in tmp_path.iterdir() if p != target]
    assert siblings == []


@pytest.mark.skipif(platform.system() == 'Windows', reason='POSIX mode bits do not apply on Windows')
def test_atomic_write_sets_0600_permissions(tmp_path: pathlib.Path) -> None:
    """The written file must be chmod 0600 — these files hold secrets."""
    target = tmp_path / 'secret.txt'
    atomic_write_text(target, 'top-secret')

    mode = os.stat(target).st_mode & 0o777
    assert mode == 0o600, f'expected 0600, got {oct(mode)}'


@pytest.mark.skipif(platform.system() == 'Windows', reason='POSIX mode bits do not apply on Windows')
def test_atomic_write_sets_0600_on_ebusy_fallback(tmp_path: pathlib.Path) -> None:
    """The EBUSY in-place-overwrite fallback must also end up chmod 0600."""
    target = tmp_path / 'secret.txt'
    target.write_text('old', encoding='utf-8')
    os.chmod(target, 0o644)

    def patched_replace(src: str, dst: str) -> None:
        raise OSError(errno.EBUSY, 'Device or resource busy', src)

    with unittest.mock.patch('app.persistence.file_utils.os.replace', side_effect=patched_replace):
        atomic_write_text(target, 'new-secret')

    mode = os.stat(target).st_mode & 0o777
    assert mode == 0o600, f'expected 0600, got {oct(mode)}'

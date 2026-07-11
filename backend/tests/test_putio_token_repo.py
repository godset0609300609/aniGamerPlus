"""Tests for PutioTokenRepository."""

from __future__ import annotations

import os
import pathlib
import platform

import pytest

from app.persistence.paths import WorkspacePaths
from app.persistence.putio_token_repo import PutioTokenRepository


@pytest.fixture
def paths(tmp_path: pathlib.Path) -> WorkspacePaths:
    return WorkspacePaths.detect(working_dir=tmp_path)


@pytest.fixture
def repo(paths: WorkspacePaths) -> PutioTokenRepository:
    return PutioTokenRepository(paths)


def test_path_property(repo: PutioTokenRepository, paths: WorkspacePaths) -> None:
    assert repo.path == paths.putio_token_path


def test_exists_and_nonempty_false_when_missing(repo: PutioTokenRepository) -> None:
    assert repo.exists_and_nonempty() is False


def test_read_returns_empty_string_when_missing(repo: PutioTokenRepository) -> None:
    assert repo.read() == ''


def test_write_then_read_round_trip(repo: PutioTokenRepository) -> None:
    repo.write('my-oauth-token-abc123')
    assert repo.read() == 'my-oauth-token-abc123'


def test_write_creates_file(repo: PutioTokenRepository, paths: WorkspacePaths) -> None:
    repo.write('tok')
    assert paths.putio_token_path.exists()


def test_exists_and_nonempty_true_after_write(repo: PutioTokenRepository) -> None:
    repo.write('tok')
    assert repo.exists_and_nonempty() is True


def test_write_strips_surrounding_whitespace(repo: PutioTokenRepository) -> None:
    repo.write('  tok-with-padding  \n')
    assert repo.read() == 'tok-with-padding'


def test_write_overwrites_previous_value(repo: PutioTokenRepository) -> None:
    repo.write('first-token')
    repo.write('second-token')
    assert repo.read() == 'second-token'


def test_exists_and_nonempty_false_when_file_is_blank(repo: PutioTokenRepository, paths: WorkspacePaths) -> None:
    paths.putio_token_path.parent.mkdir(parents=True, exist_ok=True)
    paths.putio_token_path.write_text('', encoding='utf-8')
    assert repo.exists_and_nonempty() is False


@pytest.mark.skipif(platform.system() == 'Windows', reason='POSIX mode bits do not apply on Windows')
def test_write_sets_0600_permissions(repo: PutioTokenRepository, paths: WorkspacePaths) -> None:
    """D-3 (security audit): mirrors test_cookie_repo.py's
    test_atomic_write_sets_0600_permissions — putio_token.txt is a bearer
    token and must not be readable by other local users."""
    repo.write('secret-oauth-token')

    mode = os.stat(paths.putio_token_path).st_mode & 0o777
    assert mode == 0o600, f'expected 0600, got {oct(mode)}'

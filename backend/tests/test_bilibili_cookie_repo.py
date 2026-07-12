"""Tests for BilibiliCookieRepository."""

from __future__ import annotations

import pathlib

import pytest

from app.persistence.bilibili_cookie_repo import BilibiliCookieRepository
from app.persistence.paths import WorkspacePaths


@pytest.fixture
def paths(tmp_path: pathlib.Path) -> WorkspacePaths:
    return WorkspacePaths.detect(working_dir=tmp_path)


@pytest.fixture
def repo(paths: WorkspacePaths) -> BilibiliCookieRepository:
    return BilibiliCookieRepository(paths)


def test_exists_and_nonempty_false_when_missing(repo: BilibiliCookieRepository) -> None:
    assert repo.exists_and_nonempty() is False


def test_write_creates_file(repo: BilibiliCookieRepository, paths: WorkspacePaths) -> None:
    repo.write('SESSDATA=abc123')
    assert paths.bilibili_cookie_path.exists()
    assert repo.exists_and_nonempty() is True


def test_write_netscape_header(repo: BilibiliCookieRepository, paths: WorkspacePaths) -> None:
    repo.write('SESSDATA=abc123')
    content = paths.bilibili_cookie_path.read_text(encoding='utf-8')
    assert content.startswith('# Netscape HTTP Cookie File')


def test_write_single_cookie_produces_one_data_line(repo: BilibiliCookieRepository, paths: WorkspacePaths) -> None:
    repo.write('SESSDATA=mytoken')
    raw_lines = paths.bilibili_cookie_path.read_text(encoding='utf-8').splitlines()
    lines = [ln for ln in raw_lines if not ln.startswith('#') and ln.strip()]
    assert len(lines) == 1
    parts = lines[0].split('\t')
    assert parts[0] == '.bilibili.com'
    assert parts[4].isdigit()
    assert parts[5] == 'SESSDATA'
    assert parts[6] == 'mytoken'


def test_write_multiple_cookies(repo: BilibiliCookieRepository, paths: WorkspacePaths) -> None:
    repo.write('SESSDATA=tok1; buvid3=tok2; bili_jct=tok3')
    raw_lines = paths.bilibili_cookie_path.read_text(encoding='utf-8').splitlines()
    lines = [ln for ln in raw_lines if not ln.startswith('#') and ln.strip()]
    assert len(lines) == 3
    names = {ln.split('\t')[5] for ln in lines}
    assert names == {'SESSDATA', 'buvid3', 'bili_jct'}


def test_write_cookie_with_equals_in_value(repo: BilibiliCookieRepository, paths: WorkspacePaths) -> None:
    repo.write('SESSDATA=base64==; other=x')
    raw_lines = paths.bilibili_cookie_path.read_text(encoding='utf-8').splitlines()
    lines = [ln for ln in raw_lines if not ln.startswith('#') and ln.strip()]
    sessdata_line = next(ln for ln in lines if 'SESSDATA' in ln)
    parts = sessdata_line.split('\t')
    assert parts[6] == 'base64=='


def test_write_netscape_format_correct_fields(repo: BilibiliCookieRepository, paths: WorkspacePaths) -> None:
    repo.write('SESSDATA=val')
    raw_lines = paths.bilibili_cookie_path.read_text(encoding='utf-8').splitlines()
    lines = [ln for ln in raw_lines if not ln.startswith('#') and ln.strip()]
    parts = lines[0].split('\t')
    assert len(parts) == 7
    assert parts[1] == 'TRUE'
    assert parts[2] == '/'
    assert parts[3] == 'TRUE'


def test_path_property(repo: BilibiliCookieRepository, paths: WorkspacePaths) -> None:
    assert repo.path == paths.bilibili_cookie_path

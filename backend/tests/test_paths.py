"""Tests for ``app.persistence.paths.WorkspacePaths``."""

from __future__ import annotations

import pathlib

import pytest

from app.persistence.paths import WorkspacePaths


def test_detect_with_explicit_working_dir_sets_every_subpath(tmp_path: pathlib.Path) -> None:
    paths = WorkspacePaths.detect(working_dir=tmp_path)

    assert paths.working_dir == tmp_path.resolve()
    assert paths.config_path == tmp_path.resolve() / 'config.json'
    assert paths.sn_list_path == tmp_path.resolve() / 'sn_list.txt'
    assert paths.cookie_path == tmp_path.resolve() / 'cookie.txt'
    assert paths.invalid_cookie_path == tmp_path.resolve() / 'invalid_cookie.txt'
    assert paths.putio_token_path == tmp_path.resolve() / 'putio_token.txt'
    assert paths.logs_dir == tmp_path.resolve() / 'logs'
    assert paths.db_path == tmp_path.resolve() / 'aniGamer.db'
    assert paths.bangumi_dir_default == tmp_path.resolve() / 'bangumi'
    assert paths.temp_dir_default == tmp_path.resolve() / 'temp'
    assert paths.ssl_cert_path == tmp_path.resolve() / 'sslkey' / 'server.crt'
    assert paths.ssl_key_path == tmp_path.resolve() / 'sslkey' / 'server.key'


def test_default_detect_returns_backend_dir_with_config_json() -> None:
    paths = WorkspacePaths.detect()
    assert paths.config_path.name == 'config.json'
    # Should point at the backend/ directory — i.e. a sibling of ``app``.
    assert (paths.working_dir / 'app').is_dir()


def test_all_paths_are_absolute_path_objects(tmp_path: pathlib.Path) -> None:
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    for name in (
        'working_dir',
        'config_path',
        'sn_list_path',
        'cookie_path',
        'invalid_cookie_path',
        'putio_token_path',
        'logs_dir',
        'db_path',
        'bangumi_dir_default',
        'temp_dir_default',
        'ssl_cert_path',
        'ssl_key_path',
    ):
        value = getattr(paths, name)
        assert isinstance(value, pathlib.Path), f'{name} should be a Path'
        assert value.is_absolute(), f'{name} should be absolute: {value}'


def test_detect_logs_dir_env_override(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logs_dir = tmp_path / 'x-logs'
    monkeypatch.setenv('ANIGAMERPLUS_LOGS_DIR', str(logs_dir))
    workspace_dir = tmp_path / 'workspace'

    paths = WorkspacePaths.detect(working_dir=workspace_dir)

    assert paths.logs_dir == logs_dir.resolve()
    assert paths.working_dir == workspace_dir.resolve()


def test_detect_bangumi_dir_env_override(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bangumi_dir = tmp_path / 'x-bangumi'
    monkeypatch.setenv('ANIGAMERPLUS_BANGUMI_DIR', str(bangumi_dir))
    workspace_dir = tmp_path / 'workspace'

    paths = WorkspacePaths.detect(working_dir=workspace_dir)

    assert paths.bangumi_dir_default == bangumi_dir.resolve()
    assert paths.working_dir == workspace_dir.resolve()


def test_detect_env_overrides_are_independent(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logs_dir = tmp_path / 'x-logs'
    bangumi_dir = tmp_path / 'x-bangumi'
    monkeypatch.setenv('ANIGAMERPLUS_LOGS_DIR', str(logs_dir))
    monkeypatch.setenv('ANIGAMERPLUS_BANGUMI_DIR', str(bangumi_dir))
    workspace_dir = tmp_path / 'workspace'

    paths = WorkspacePaths.detect(working_dir=workspace_dir)

    assert paths.logs_dir == logs_dir.resolve()
    assert paths.bangumi_dir_default == bangumi_dir.resolve()
    assert paths.db_path == workspace_dir.resolve() / 'aniGamer.db'
    assert paths.config_path == workspace_dir.resolve() / 'config.json'


def test_detect_no_env_override_uses_workspace_default(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('ANIGAMERPLUS_LOGS_DIR', raising=False)
    monkeypatch.delenv('ANIGAMERPLUS_BANGUMI_DIR', raising=False)

    paths = WorkspacePaths.detect(working_dir=tmp_path)

    assert paths.logs_dir == tmp_path.resolve() / 'logs'
    assert paths.bangumi_dir_default == tmp_path.resolve() / 'bangumi'

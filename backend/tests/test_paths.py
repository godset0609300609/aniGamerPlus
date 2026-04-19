"""Tests for ``app.persistence.paths.WorkspacePaths``."""

from __future__ import annotations

import pathlib

from app.persistence.paths import WorkspacePaths


def test_detect_with_explicit_working_dir_sets_every_subpath(tmp_path: pathlib.Path) -> None:
    paths = WorkspacePaths.detect(working_dir=tmp_path)

    assert paths.working_dir == tmp_path.resolve()
    assert paths.config_path == tmp_path.resolve() / 'config.json'
    assert paths.sn_list_path == tmp_path.resolve() / 'sn_list.txt'
    assert paths.cookie_path == tmp_path.resolve() / 'cookie.txt'
    assert paths.invalid_cookie_path == tmp_path.resolve() / 'invalid_cookie.txt'
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

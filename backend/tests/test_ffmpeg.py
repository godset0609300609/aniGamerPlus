"""Tests for ``FFmpegRunner``."""

from __future__ import annotations

import pathlib
import subprocess
from unittest import mock

import pytest

from app.downloader.ffmpeg import FFmpegRunner
from app.logging_ import Logger


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


def test_which_prefers_shutil_which(tmp_path: pathlib.Path, logger: Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: '/usr/bin/ffmpeg')
    runner = FFmpegRunner(tmp_path, logger)
    assert runner.which() == pathlib.Path('/usr/bin/ffmpeg')


def test_which_falls_back_to_working_dir_on_windows(
    tmp_path: pathlib.Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: None)
    monkeypatch.setattr('app.downloader.ffmpeg.platform.system', lambda: 'Windows')
    local = tmp_path / 'ffmpeg.exe'
    local.write_bytes(b'')
    runner = FFmpegRunner(tmp_path, logger)
    assert runner.which() == local


def test_which_falls_back_to_working_dir_on_posix(
    tmp_path: pathlib.Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: None)
    monkeypatch.setattr('app.downloader.ffmpeg.platform.system', lambda: 'Linux')
    local = tmp_path / 'ffmpeg'
    local.write_bytes(b'')
    runner = FFmpegRunner(tmp_path, logger)
    assert runner.which() == local


def test_which_raises_when_missing(tmp_path: pathlib.Path, logger: Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: None)
    monkeypatch.setattr('app.downloader.ffmpeg.platform.system', lambda: 'Linux')
    runner = FFmpegRunner(tmp_path, logger)
    with pytest.raises(FileNotFoundError):
        runner.which()


def test_run_never_uses_shell_true(tmp_path: pathlib.Path, logger: Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: '/usr/bin/ffmpeg')
    captured: dict[str, object] = {}

    def _fake_run(cmd, **kwargs):
        captured['cmd'] = cmd
        captured['kwargs'] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

    monkeypatch.setattr('app.downloader.ffmpeg.subprocess.run', _fake_run)

    runner = FFmpegRunner(tmp_path, logger)
    runner.run(['-i', 'input.m3u8', '-c', 'copy', 'out.mp4'])

    assert isinstance(captured['cmd'], list)
    kwargs = captured['kwargs']
    assert 'shell' not in kwargs or kwargs.get('shell') is False
    assert kwargs['capture_output'] is True
    assert kwargs['text'] is True


def test_build_segment_merge_cmd_contains_expected_flags(
    tmp_path: pathlib.Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: '/usr/bin/ffmpeg')
    runner = FFmpegRunner(tmp_path, logger)
    m3u8_path = tmp_path / 'in.m3u8'
    out_path = tmp_path / 'out.mp4'
    args = runner.build_segment_merge_cmd(
        m3u8=m3u8_path,
        output=out_path,
        faststart=True,
        audio_lang=True,
    )

    # Returns just the flags — no binary prepended.
    assert args[0] != '/usr/bin/ffmpeg'
    assert not any(pathlib.Path(token).name in {'ffmpeg', 'ffmpeg.exe'} for token in args)
    assert '-allowed_extensions' in args and 'ALL' in args
    assert '-protocol_whitelist' in args
    assert 'file,http,https,tcp,tls,crypto' in args
    assert '-i' in args
    assert str(m3u8_path) in args
    assert str(out_path) in args
    assert '-c' in args and 'copy' in args
    assert '-movflags' in args and 'faststart' in args
    assert '-metadata:s:a:0' in args


def test_run_decodes_utf8_stderr_on_cp950_locale(
    tmp_path: pathlib.Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: on Windows-TW (cp950) locale, ffmpeg stderr is UTF-8 and
    Python's ``subprocess`` decoded it with the preferred encoding — boom,
    ``UnicodeDecodeError``. ``FFmpegRunner.run`` must pin ``encoding="utf-8"``
    + ``errors="replace"`` so the decode is stable regardless of host locale.
    """
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: '/usr/bin/ffmpeg')

    # Bytes that are valid UTF-8 but would fail strict-decode as cp950.
    # ``\xc2\xa0`` (U+00A0 NBSP) is a good, stable trigger: in cp950 the lead
    # byte 0xC2 expects a trailing byte in [0x40-0x7E, 0xA1-0xFE]; 0xA0 is not
    # valid, so ``errors="strict"`` raises.
    utf8_stderr_bytes = b'\xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e\xc2\xa0end\n'

    captured: dict[str, object] = {}

    def _fake_run(cmd, **kwargs):
        captured['cmd'] = cmd
        captured['kwargs'] = kwargs
        # Mirror ``subprocess.run(text=True, encoding=..., errors=...)``:
        # raw stderr bytes are decoded with the caller's requested codec.
        encoding = kwargs.get('encoding') or 'cp950'
        errors = kwargs.get('errors') or 'strict'
        stderr_text = utf8_stderr_bytes.decode(encoding, errors=errors)
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr=stderr_text)

    monkeypatch.setattr('app.downloader.ffmpeg.subprocess.run', _fake_run)

    runner = FFmpegRunner(tmp_path, logger)
    # Without encoding="utf-8", the fake would decode with cp950 and raise
    # UnicodeDecodeError — reproducing the production bug.
    result = runner.run(['-i', 'input.m3u8'])

    kwargs = captured['kwargs']
    assert kwargs.get('encoding') == 'utf-8'
    assert kwargs.get('errors') == 'replace'
    assert '日本語' in result.stderr


def test_build_segment_merge_cmd_without_optional_flags(
    tmp_path: pathlib.Path, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: '/usr/bin/ffmpeg')
    runner = FFmpegRunner(tmp_path, logger)
    args = runner.build_segment_merge_cmd(
        m3u8=tmp_path / 'in.m3u8',
        output=tmp_path / 'out.mp4',
        faststart=False,
        audio_lang=False,
    )
    assert '-movflags' not in args
    assert '-metadata:s:a:0' not in args

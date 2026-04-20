"""Tests for ``FtpUploader``."""

from __future__ import annotations

import ftplib
import io
import pathlib
from typing import Any

import pytest

from app.downloader.uploader_ftp import FtpUploader
from app.logging_ import Logger
from app.models import FtpSettings


class _FakeSock:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def settimeout(self, value: float) -> None:
        self.timeout = value


class _FakeFTP:
    """Stubbed ``ftplib.FTP`` / ``ftplib.FTP_TLS`` — records method calls."""

    instances: list[_FakeFTP] = []

    def __init__(
        self,
        *,
        is_tls: bool = False,
        existing_dirs: set[str] | None = None,
        remote_sizes: dict[str, int] | None = None,
        connect_fail_times: int = 0,
        prot_p_supported: bool = True,
    ) -> None:
        self.is_tls = is_tls
        self.encoding = 'ascii'
        self.sock = _FakeSock()
        self.cwd_stack: list[str] = []
        self.made_dirs: list[str] = []
        self.existing_dirs: set[str] = existing_dirs or set()
        self.remote_sizes: dict[str, int] = remote_sizes or {}
        self.connect_fail_times = connect_fail_times
        self._connect_attempts = 0
        self.prot_p_called = False
        self.prot_p_supported = prot_p_supported
        self.login_args: tuple[str, str] | None = None
        self.stor_uploads: list[tuple[str, int, bytes]] = []
        self.voidcmds: list[str] = []
        self.quit_called = False
        self.close_called = False
        self._type_was_set = False
        self._closed = False

    def connect(self, server: str, port: int) -> None:
        self._connect_attempts += 1
        if self._connect_attempts <= self.connect_fail_times:
            raise ftplib.error_temp('cannot connect')

    def login(self, user: str, pwd: str) -> None:
        self.login_args = (user, pwd)

    def prot_p(self) -> None:
        if not self.prot_p_supported:
            raise ftplib.error_perm('prot_p unsupported')
        self.prot_p_called = True

    def voidcmd(self, cmd: str) -> str:
        self.voidcmds.append(cmd)
        return cmd

    def cwd(self, dirname: str) -> None:
        if dirname in self.existing_dirs:
            self.cwd_stack.append(dirname)
            return
        raise ftplib.error_perm(f'550 {dirname}: no such directory')

    def mkd(self, dirname: str) -> str:
        self.made_dirs.append(dirname)
        self.existing_dirs.add(dirname)
        return dirname

    def size(self, filename: str) -> int | None:
        if filename in self.remote_sizes:
            return self.remote_sizes[filename]
        raise ftplib.error_perm('550 file not found')

    def transfercmd(self, cmd: str, rest: int | None = None) -> _FakeTransferConn:
        return _FakeTransferConn(self, cmd, rest or 0)

    def voidresp(self) -> str:
        return '226 OK'

    def quit(self) -> str:
        self.quit_called = True
        self._closed = True
        return '221 bye'

    def close(self) -> None:
        self.close_called = True
        self._closed = True


class _FakeTransferConn:
    def __init__(self, ftp: _FakeFTP, cmd: str, rest: int) -> None:
        self._ftp = ftp
        self._cmd = cmd
        self._rest = rest
        self._buffer = io.BytesIO()

    def sendall(self, block: bytes) -> None:
        self._buffer.write(block)

    def close(self) -> None:
        # STOR remote_name
        parts = self._cmd.split(' ', 1)
        remote_name = parts[1] if len(parts) == 2 else ''
        self._ftp.stor_uploads.append((remote_name, self._rest, self._buffer.getvalue()))
        # After STOR completes, remote size is rest + payload length.
        total = self._rest + len(self._buffer.getvalue())
        self._ftp.remote_sizes[remote_name] = total


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


def _ftp_settings(**overrides: Any) -> FtpSettings:
    base: dict[str, Any] = {
        'server': 'ftp.example.com',
        'port': 21,
        'user': 'u',
        'pwd': 'p',
        'tls': False,
        'cwd': '',
        'show_error_detail': False,
        'max_retry_num': 2,
    }
    base.update(overrides)
    return FtpSettings(**base)


def _local_file(tmp_path: pathlib.Path, content: bytes = b'abcdef') -> pathlib.Path:
    path = tmp_path / 'local.mp4'
    path.write_bytes(content)
    return path


def test_tls_uses_ftp_tls_and_calls_prot_p(
    tmp_path: pathlib.Path,
    logger: Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_FakeFTP] = []

    # The uploader calls ``isinstance(ftp, ftplib.FTP_TLS)`` after login to
    # decide whether to invoke ``prot_p()``. Make our fake class inherit
    # from ``ftplib.FTP_TLS`` so the isinstance check succeeds.
    class _FakeTLS(_FakeFTP, ftplib.FTP_TLS):  # type: ignore[misc]
        def __init__(self) -> None:  # noqa: D401 — test fake
            _FakeFTP.__init__(self, is_tls=True)
            created.append(self)

    monkeypatch.setattr('app.downloader.uploader_ftp.ftplib.FTP_TLS', _FakeTLS)
    monkeypatch.setattr(
        'app.downloader.uploader_ftp.ftplib.FTP',
        lambda: pytest.fail('should not use plain FTP'),
    )

    uploader = FtpUploader(_ftp_settings(tls=True), logger)
    ok = uploader.upload(
        local_path=_local_file(tmp_path),
        filename='out.mp4',
        bangumi_tag='',
        bangumi_name='show',
        sn=1,
    )
    assert ok is True
    assert len(created) == 1
    assert created[0].prot_p_called is True


def test_ensure_dir_creates_missing_and_ignores_existing(
    tmp_path: pathlib.Path,
    logger: Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeFTP(existing_dirs={'tagA'})  # tagA exists; "show" does not

    monkeypatch.setattr('app.downloader.uploader_ftp.ftplib.FTP', lambda: fake)

    uploader = FtpUploader(_ftp_settings(tls=False), logger)
    ok = uploader.upload(
        local_path=_local_file(tmp_path),
        filename='f.mp4',
        bangumi_tag='tagA',
        bangumi_name='show',
        sn=1,
    )
    assert ok is True
    # "show" was not in existing_dirs — must have been created.
    assert 'show' in fake.made_dirs
    # "tagA" was already there — must not be re-created.
    assert 'tagA' not in fake.made_dirs


def test_resume_uses_rest_offset_when_remote_smaller(
    tmp_path: pathlib.Path,
    logger: Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b'0123456789' * 100  # 1000 bytes
    local_path = tmp_path / 'local.mp4'
    local_path.write_bytes(payload)

    # Remote already has 300 bytes — expect a resume from 300.
    fake = _FakeFTP(existing_dirs={'tagA', 'show'}, remote_sizes={'f.mp4': 300})
    monkeypatch.setattr('app.downloader.uploader_ftp.ftplib.FTP', lambda: fake)

    uploader = FtpUploader(_ftp_settings(tls=False), logger)
    ok = uploader.upload(
        local_path=local_path,
        filename='f.mp4',
        bangumi_tag='tagA',
        bangumi_name='show',
        sn=1,
    )
    assert ok is True
    assert len(fake.stor_uploads) == 1
    name, rest, sent = fake.stor_uploads[0]
    assert name == 'f.mp4'
    assert rest == 300
    # Sent bytes should be the tail (bytes 300..end).
    assert sent == payload[300:]


def test_upload_retries_then_gives_up(
    tmp_path: pathlib.Path,
    logger: Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[_FakeFTP] = []

    def make_ftp() -> _FakeFTP:
        ftp = _FakeFTP(connect_fail_times=10)  # always fails
        attempts.append(ftp)
        return ftp

    monkeypatch.setattr('app.downloader.uploader_ftp.ftplib.FTP', make_ftp)

    settings = _ftp_settings(tls=False, max_retry_num=2)
    uploader = FtpUploader(settings, logger)
    ok = uploader.upload(
        local_path=_local_file(tmp_path),
        filename='f.mp4',
        bangumi_tag='',
        bangumi_name='show',
        sn=1,
    )
    assert ok is False
    # 1 original + 2 retries = 3 attempts.
    assert len(attempts) == 3


def test_socket_timeout_scoped_to_ftp_sock(
    tmp_path: pathlib.Path,
    logger: Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeFTP(existing_dirs={'show'})
    monkeypatch.setattr('app.downloader.uploader_ftp.ftplib.FTP', lambda: fake)

    # Assert ``socket.setdefaulttimeout`` is NOT called by the uploader.
    import socket

    calls: list[float | None] = []

    def fail_setdefault(value: float | None) -> None:
        calls.append(value)

    monkeypatch.setattr(socket, 'setdefaulttimeout', fail_setdefault)

    uploader = FtpUploader(_ftp_settings(tls=False), logger)
    ok = uploader.upload(
        local_path=_local_file(tmp_path),
        filename='f.mp4',
        bangumi_tag='',
        bangumi_name='show',
        sn=1,
    )
    assert ok is True
    assert fake.sock.timeout == 20
    assert calls == []


def test_show_error_detail_controls_traceback(
    tmp_path: pathlib.Path,
    logger: Logger,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Always fail connect so the uploader logs an error.
    def make_ftp() -> _FakeFTP:
        return _FakeFTP(connect_fail_times=10)

    monkeypatch.setattr('app.downloader.uploader_ftp.ftplib.FTP', make_ftp)

    # Capture logger calls in-memory — the default ``Logger`` writes to
    # stdout. Use the instance's own logging dir.
    log_dir = tmp_path / 'logs'
    logger = Logger(log_dir, save_logs=True, quantity_of_logs=7)

    settings_off = _ftp_settings(tls=False, max_retry_num=0, show_error_detail=False)
    uploader = FtpUploader(settings_off, logger)
    uploader.upload(
        local_path=_local_file(tmp_path),
        filename='f.mp4',
        bangumi_tag='',
        bangumi_name='show',
        sn=1,
    )

    log_files = list(log_dir.glob('*.log'))
    off_text = '\n'.join(f.read_text(encoding='utf-8') for f in log_files)
    assert 'Traceback' not in off_text
    for f in log_files:
        f.unlink()

    settings_on = _ftp_settings(tls=False, max_retry_num=0, show_error_detail=True)
    uploader_on = FtpUploader(settings_on, logger)
    uploader_on.upload(
        local_path=_local_file(tmp_path),
        filename='f.mp4',
        bangumi_tag='',
        bangumi_name='show',
        sn=1,
    )

    log_files = list(log_dir.glob('*.log'))
    on_text = '\n'.join(f.read_text(encoding='utf-8') for f in log_files)
    assert 'Traceback' in on_text

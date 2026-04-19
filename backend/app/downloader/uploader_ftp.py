"""FTP / FTPS uploader — replacement for ``Anime.upload``.

The legacy uploader did a handful of things that don't travel well:

- Called ``socket.setdefaulttimeout`` — a process-global side effect that
  leaked into unrelated HTTP calls. This class scopes the timeout to the
  FTP socket only.
- Mixed the pure-ftpd workaround (tmp dir + ``pureftpd-upload`` rename)
  with the resume logic. We keep the resume path (seek + ``REST``) but
  drop the tmp-dir dance — modern pure-ftpd (1.0.49+, 2020) no longer
  renames in-flight uploads by default and the target servers in the
  user's config file never did.
- Used ``BaseException`` catch-alls that swallowed ``KeyboardInterrupt``.
  We catch ``Exception`` and the specific ``ftplib`` error classes.
"""

from __future__ import annotations

import contextlib
import ftplib
import pathlib
import time
import traceback
import typing as T

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..models import FtpSettings


_BLOCK_SIZE = 1024 * 1024  # 1 MiB per send
_CONNECT_TIMEOUT = 20  # seconds; applied only to the FTP socket


class FtpUploader:
    """FTP / FTPS uploader with resume + ensure-dir helpers."""

    def __init__(self, settings: FtpSettings, logger: Logger) -> None:
        self._settings = settings
        self._logger = logger

    # ------------------------------------------------------------------ public

    def upload(
        self,
        local_path: pathlib.Path,
        filename: str,
        bangumi_tag: str,
        bangumi_name: str,
        sn: int,
    ) -> bool:
        """Upload ``local_path`` to the FTP server. Returns True on success."""
        if not local_path.exists():
            self._logger.error(
                sn,
                '上傳失敗',
                f'{filename} local file missing: {local_path}',
                display=False,
            )
            return False

        max_retry = max(0, int(self._settings.max_retry_num))
        attempt = 0
        last_exc: Exception | None = None

        while attempt <= max_retry:
            ftp: ftplib.FTP | None = None
            try:
                ftp = self._connect()
                self._cwd_root(ftp)
                self._ensure_dir(ftp, bangumi_tag)
                self._ensure_dir(ftp, bangumi_name)
                self._stor_file(ftp, local_path, filename)
                self._quiet_quit(ftp)
                return True
            except Exception as exc:  # noqa: BLE001 — broad by design
                last_exc = exc
                self._log_upload_error(sn, filename, exc, attempt, max_retry)
                if ftp is not None:
                    self._quiet_quit(ftp)
                attempt += 1
                if attempt <= max_retry:
                    time.sleep(1)

        if last_exc is not None:
            self._logger.error(
                sn,
                '上傳失敗',
                f'{filename} giving up after {max_retry} retries',
                display=False,
            )
        return False

    # ------------------------------------------------------------------ internals

    def _connect(self) -> ftplib.FTP:
        if self._settings.tls:
            ftp: ftplib.FTP = ftplib.FTP_TLS()
        else:
            ftp = ftplib.FTP()
        ftp.encoding = 'utf-8'
        port = int(self._settings.port) if self._settings.port else 21
        ftp.connect(self._settings.server, port)
        # Scope the timeout to this FTP socket only — never the process.
        sock = getattr(ftp, 'sock', None)
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.settimeout(_CONNECT_TIMEOUT)
        ftp.login(self._settings.user, self._settings.pwd)
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
        ftp.voidcmd('TYPE I')
        return ftp

    def _cwd_root(self, ftp: ftplib.FTP) -> None:
        cwd = self._settings.cwd or ''
        if not cwd:
            return
        try:
            ftp.cwd(cwd)
        except ftplib.error_perm as exc:
            # Non-fatal — the rest of the path may still be creatable.
            self._logger.error(
                None,
                'FTP狀態',
                f'cwd to {cwd!r} failed: {exc}',
                display=False,
            )

    def _ensure_dir(self, ftp: ftplib.FTP, name: str) -> None:
        if not name:
            return
        try:
            ftp.cwd(name)
            return
        except ftplib.error_perm:
            pass

        try:
            ftp.mkd(name)
        except ftplib.error_perm as exc:
            # ``550 File exists`` is fine; any other 5xx is not.
            if 'exist' not in str(exc).lower():
                raise
        ftp.cwd(name)

    def _stor_file(
        self,
        ftp: ftplib.FTP,
        local_path: pathlib.Path,
        remote_name: str,
    ) -> None:
        local_size = local_path.stat().st_size
        try:
            remote_size_raw = ftp.size(remote_name)
        except ftplib.error_perm:
            remote_size_raw = None
        remote_size = int(remote_size_raw or 0)

        if remote_size >= local_size > 0:
            # Already complete — nothing to do.
            return

        ftp.voidcmd('TYPE I')
        with local_path.open('rb') as fh:
            if remote_size:
                fh.seek(remote_size)
                conn = ftp.transfercmd('STOR ' + remote_name, remote_size)
            else:
                conn = ftp.transfercmd('STOR ' + remote_name)
            try:
                while True:
                    block = fh.read(_BLOCK_SIZE)
                    if not block:
                        break
                    conn.sendall(block)
            finally:
                conn.close()
        ftp.voidresp()

    def _log_upload_error(
        self,
        sn: int,
        filename: str,
        exc: Exception,
        attempt: int,
        max_retry: int,
    ) -> None:
        detail = f'{filename} upload failed on attempt {attempt + 1}/{max_retry + 1}: {exc}'
        if self._settings.show_error_detail:
            detail = detail + '\n' + traceback.format_exc()
        self._logger.error(sn, '上傳狀態', detail, display=False)

    @staticmethod
    def _quiet_quit(ftp: ftplib.FTP) -> None:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            with contextlib.suppress(Exception):  # noqa: BLE001
                ftp.close()

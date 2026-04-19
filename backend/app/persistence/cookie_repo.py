"""Repository wrapping ``cookie.txt`` / ``invalid_cookie.txt``.

Legacy serialisation format: a single line of ``key=value`` pairs joined
by ``"; "`` (semicolon + space). The parser must tolerate ``=`` inside
values, so we split on the first ``=`` only.
"""

from __future__ import annotations

import codecs
import collections.abc
import datetime
import os
import threading
import typing as T

from .file_utils import atomic_write_text

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from .paths import WorkspacePaths


class CookieRepository:
    """Reads / renews / invalidates the login cookie file."""

    def __init__(self, paths: WorkspacePaths, logger: Logger) -> None:
        self._paths = paths
        self._logger = logger
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ read

    def load(self) -> dict[str, str]:
        """Return the cookie as a dict. Empty dict if the file is missing/blank.

        Tolerates UTF-8 BOM headers — the legacy ``check_encoding`` path
        strips them silently, and we match that behaviour.
        """
        path = self._paths.cookie_path
        if not path.exists():
            return {}
        data = path.read_bytes()
        if data.startswith(codecs.BOM_UTF8):
            data = data[len(codecs.BOM_UTF8) :]
        text = data.decode('utf-8', errors='replace').strip()
        if not text:
            return {}
        return _parse_cookie_line(text)

    def modified_at(self) -> datetime.datetime:
        """Return the cookie file's mtime as a local ``datetime``."""
        return datetime.datetime.fromtimestamp(self._paths.cookie_path.stat().st_mtime)

    # ------------------------------------------------------------------ mutate

    def renew(self, new_cookie: collections.abc.Mapping[str, str]) -> None:
        """Overwrite ``cookie.txt`` with ``new_cookie`` as a single line.

        Idempotent and thread-safe — concurrent calls are serialised by an
        internal lock so the file is never partially written.
        """
        line = '; '.join(f'{k}={v}' for k, v in new_cookie.items())
        with self._lock:
            atomic_write_text(self._paths.cookie_path, line)

    def write(self, text: str) -> None:
        """Overwrite ``cookie.txt`` with an arbitrary cookie string.

        The caller is responsible for supplying a valid cookie string; this
        method performs no parsing — it writes *text* verbatim (with a
        trailing newline stripped). Thread-safe via the internal lock.
        """
        with self._lock:
            atomic_write_text(self._paths.cookie_path, text.strip())

    def exists_and_nonempty(self) -> bool:
        """Return ``True`` if ``cookie.txt`` exists and has non-blank content."""
        path = self._paths.cookie_path
        return path.exists() and path.stat().st_size > 0

    def invalidate(self) -> None:
        """Move ``cookie.txt`` to ``invalid_cookie.txt`` atomically.

        If the destination already exists it is overwritten (matches legacy
        ``Config.invalid_cookie``). A missing source is a no-op.
        """
        src = self._paths.cookie_path
        dst = self._paths.invalid_cookie_path
        with self._lock:
            if not src.exists():
                return
            # On Windows ``os.replace`` already overwrites an existing dest,
            # but be explicit: the legacy code removed the old invalid file
            # first.
            try:
                os.replace(src, dst)
            except OSError as exc:
                self._logger.error(
                    None,
                    'cookie狀態',
                    f'failed to mark cookie invalid: {exc}',
                    display=False,
                )
                raise


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_cookie_line(line: str) -> dict[str, str]:
    """Split ``"a=1; b=2; c=x=y"`` into ``{"a": "1", "b": "2", "c": "x=y"}``."""
    out: dict[str, str] = {}
    for piece in line.split(';'):
        piece = piece.strip()
        if not piece:
            continue
        key, sep, value = piece.partition('=')
        if not sep:
            # Key with no ``=`` — legacy behaviour was to explode; we keep
            # it and store an empty value, which callers can filter on.
            out[key.strip()] = ''
        else:
            out[key.strip()] = value
    return out

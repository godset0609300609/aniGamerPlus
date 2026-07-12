"""Repository wrapping ``putio_token.txt`` — the Put.io OAuth bearer token.

Unlike ``cookie.txt`` (a ``k=v; k=v`` string) or ``bilibili_cookie.txt``
(Netscape format), this is a single opaque token string with no internal
structure — no parsing on read, no formatting on write.
"""

from __future__ import annotations

import pathlib
import threading
import typing as T

from .file_utils import atomic_write_text

if T.TYPE_CHECKING:
    from .paths import WorkspacePaths


class PutioTokenRepository:
    """Reads / writes the Put.io OAuth token file."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths
        self._lock = threading.Lock()

    @property
    def path(self) -> pathlib.Path:
        return self._paths.putio_token_path

    def write(self, text: str) -> None:
        """Overwrite ``putio_token.txt`` with *text* verbatim (trailing whitespace stripped)."""
        with self._lock:
            atomic_write_text(self._paths.putio_token_path, text.strip())

    def read(self) -> str:
        """Return the stored token, or ``''`` if the file is missing/blank."""
        path = self._paths.putio_token_path
        if not path.exists():
            return ''
        return path.read_text(encoding='utf-8').strip()

    def exists_and_nonempty(self) -> bool:
        """Return ``True`` if ``putio_token.txt`` exists and has non-blank content."""
        path = self._paths.putio_token_path
        return path.exists() and path.stat().st_size > 0

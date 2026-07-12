"""Repository for ``bilibili_cookie.txt`` (Netscape format for yt-dlp)."""

from __future__ import annotations

import pathlib
import time
import typing as T

from .file_utils import atomic_write_text

if T.TYPE_CHECKING:
    from .paths import WorkspacePaths

_NETSCAPE_HEADER = '# Netscape HTTP Cookie File\n'
_EXPIRY_DELTA = 365 * 86400


class BilibiliCookieRepository:
    """Reads / writes the Bilibili Netscape cookie file."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    @property
    def path(self) -> pathlib.Path:
        return self._paths.bilibili_cookie_path

    def write(self, raw_str: str) -> None:
        """Parse one-line ``k=v; k=v;`` cookie string and write Netscape format."""
        cookies = _parse_cookie_line(raw_str)
        expiry = int(time.time()) + _EXPIRY_DELTA
        lines = [_NETSCAPE_HEADER]
        for name, value in cookies.items():
            lines.append(f'.bilibili.com\tTRUE\t/\tTRUE\t{expiry}\t{name}\t{value}\n')
        atomic_write_text(self._paths.bilibili_cookie_path, ''.join(lines))

    def exists_and_nonempty(self) -> bool:
        path = self._paths.bilibili_cookie_path
        return path.exists() and path.stat().st_size > 0


def _parse_cookie_line(line: str) -> dict[str, str]:
    """Split ``"a=1; b=2; c=x=y"`` into ``{"a": "1", "b": "2", "c": "x=y"}``."""
    out: dict[str, str] = {}
    for piece in line.split(';'):
        piece = piece.strip()
        if not piece:
            continue
        key, sep, value = piece.partition('=')
        if not sep:
            out[key.strip()] = ''
        else:
            out[key.strip()] = value
    return out

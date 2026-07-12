"""FFmpeg runner — no-shell binary discovery + command builder.

Legacy ``Anime.download`` used ``subprocess.Popen('ffmpeg -h', shell=True, …)``
to probe for ffmpeg. The implicit shell resolution is a Windows foot-gun
(``cmd.exe`` returns successfully for commands that don't exist, depending
on the PATHEXT dance) and the shell invocation leaks a new process that's
awkward to clean up. This class:

- Uses ``shutil.which('ffmpeg')`` first.
- Falls back to ``working_dir / 'ffmpeg.exe'`` on Windows, ``ffmpeg`` on
  POSIX.
- Raises ``FileNotFoundError`` if neither exists.
- Always invokes ffmpeg with a list argv — never a shell string.
"""

from __future__ import annotations

import collections.abc
import os
import pathlib
import platform
import shutil
import subprocess
import typing as T

if T.TYPE_CHECKING:
    from ..logging_ import Logger


def resolve_ffmpeg_path() -> str | None:
    """Return the ffmpeg binary path if available, else None.

    Search order:
    1. PATH (via shutil.which).
    2. ``ffmpeg.exe`` (Windows) or ``ffmpeg`` (POSIX) relative to cwd.
    """
    found = shutil.which('ffmpeg')
    if found:
        return found
    candidate = pathlib.Path('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    if candidate.exists():
        return str(candidate.resolve())
    return None


class FFmpegRunner:
    """Locate ``ffmpeg`` once, then build / run its commands safely."""

    def __init__(self, working_dir: pathlib.Path, logger: Logger) -> None:
        self._working_dir = pathlib.Path(working_dir)
        self._logger = logger
        self._cached_path: pathlib.Path | None = None

    # ------------------------------------------------------------------ public

    def which(self) -> pathlib.Path:
        """Return the resolved ffmpeg binary path, caching the lookup."""
        if self._cached_path is not None:
            return self._cached_path

        system_path = shutil.which('ffmpeg')
        if system_path:
            self._cached_path = pathlib.Path(system_path)
            return self._cached_path

        candidate = self._working_dir / ('ffmpeg.exe' if 'Windows' in platform.system() else 'ffmpeg')
        if candidate.exists():
            self._cached_path = candidate
            return candidate

        raise FileNotFoundError(f'ffmpeg not found on PATH nor in {self._working_dir}')

    def version(self) -> str:
        """Return ffmpeg's ``-version`` stdout (first line)."""
        result = self.run(['-version'], timeout=15)
        first = (result.stdout or '').splitlines()
        return first[0] if first else ''

    def run(
        self,
        args: collections.abc.Sequence[str],
        *,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Execute ``ffmpeg`` with ``args``. Never shells out."""
        ffmpeg = str(self.which())
        cmd = [ffmpeg, *args]
        return subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )

    def build_segment_merge_cmd(
        self,
        m3u8: pathlib.Path,
        output: pathlib.Path,
        *,
        faststart: bool,
        audio_lang: bool,
    ) -> list[str]:
        """Build the ffmpeg argument list used to merge downloaded segments.

        Returns ONLY the flags/arguments — the binary is not included.
        Pass the result to :meth:`run`, which prepends the binary.

        Mirrors the legacy merge step in ``__segment_download_mode``. The
        ``audio_lang`` flag only sets a metadata tag; the caller is still
        responsible for deciding whether the title is 中文 vs JP.
        """
        args: list[str] = [
            '-allowed_extensions',
            'ALL',
            '-protocol_whitelist',
            'file,http,https,tcp,tls,crypto',
            '-i',
            str(m3u8),
            '-c',
            'copy',
        ]
        if faststart:
            args.extend(['-movflags', 'faststart'])
        if audio_lang:
            args.extend(['-metadata:s:a:0', 'language=jpn'])
        args.extend([str(output), '-y'])
        return args

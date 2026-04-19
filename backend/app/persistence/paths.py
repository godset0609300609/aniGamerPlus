"""Workspace path resolution.

Centralises the legacy ``working_dir`` / ``config_path`` / ``sn_list_path``
constants that ``Config.py`` currently hoists at module load time, and makes
them discoverable from the new OO layer without dragging in the legacy
module.
"""

from __future__ import annotations

import dataclasses
import pathlib
import sys


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspacePaths:
    """Every filesystem path the backend cares about.

    Paths are resolved once at startup (or at test time) and passed to
    consumers; nothing here touches the disk. Consumers are responsible for
    creating directories as needed.
    """

    working_dir: pathlib.Path
    config_path: pathlib.Path
    sn_list_path: pathlib.Path
    cookie_path: pathlib.Path
    invalid_cookie_path: pathlib.Path
    logs_dir: pathlib.Path
    db_path: pathlib.Path
    bangumi_dir_default: pathlib.Path
    temp_dir_default: pathlib.Path
    ssl_cert_path: pathlib.Path
    ssl_key_path: pathlib.Path

    @classmethod
    def detect(cls, *, working_dir: pathlib.Path | None = None) -> WorkspacePaths:
        """Resolve all known paths.

        - If ``working_dir`` is given, use it verbatim (after ``resolve()``).
        - Otherwise, if ``sys.frozen`` is set (PyInstaller bundle), use the
          directory of ``sys.executable``.
        - Otherwise, fall back to ``backend/`` (two levels up from this file).
        """
        if working_dir is not None:
            root = pathlib.Path(working_dir).resolve()
        elif getattr(sys, 'frozen', False):
            root = pathlib.Path(sys.executable).resolve().parent
        else:
            # app/persistence/paths.py -> app/persistence -> app -> backend
            root = pathlib.Path(__file__).resolve().parents[2]

        return cls(
            working_dir=root,
            config_path=root / 'config.json',
            sn_list_path=root / 'sn_list.txt',
            cookie_path=root / 'cookie.txt',
            invalid_cookie_path=root / 'invalid_cookie.txt',
            logs_dir=root / 'logs',
            db_path=root / 'aniGamer.db',
            bangumi_dir_default=root / 'bangumi',
            temp_dir_default=root / 'temp',
            ssl_cert_path=root / 'sslkey' / 'server.crt',
            ssl_key_path=root / 'sslkey' / 'server.key',
        )

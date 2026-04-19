"""Typed repository around ``config.json`` — the v17.2 :class:`AppSettings`.

Wraps the legacy ``Config.read_settings`` / ``Config.write_settings`` flow
in three clean operations — ``load``/``save``/``reset`` — with all the BOM,
encoding, migration, and normalisation steps driven through explicit code
paths instead of the legacy retry loop.
"""

from __future__ import annotations

import codecs
import contextlib
import json
import os
import pathlib
import tempfile
import typing as T

import chardet

from ..models import AppSettings
from . import settings_migration

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from .paths import WorkspacePaths


_DEFAULT_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'
)
_MAX_MULTI_THREAD = 5
_MAX_MULTI_DOWNLOADING_SEGMENT = 5


class SettingsRepository:
    """Load/save :class:`AppSettings` from a ``config.json`` on disk.

    ``paths.config_path`` is written utf-8, no BOM, indent=4,
    ``ensure_ascii=False`` — matching the legacy ``Config.write_settings``
    shape so the two implementations can round-trip the same file during
    the migration window.
    """

    def __init__(self, paths: WorkspacePaths, logger: Logger) -> None:
        self._paths = paths
        self._logger = logger

    # ------------------------------------------------------------------ load

    def load(self) -> AppSettings:
        if not self._paths.config_path.exists():
            self.reset()

        raw = self._read_json(self._paths.config_path)
        migrated = settings_migration.migrate(raw)
        settings = AppSettings.model_validate(migrated)
        return self._normalise(settings)

    def save(self, settings: AppSettings) -> None:
        """Write ``settings`` to disk in the legacy-compatible shape."""
        payload = self._denormalise(settings).model_dump(by_alias=True, exclude_none=False)
        self._atomic_write(
            self._paths.config_path,
            json.dumps(payload, ensure_ascii=False, indent=4),
        )

    def reset(self) -> AppSettings:
        """Overwrite ``config.json`` with defaults and return them."""
        defaults = AppSettings()
        self.save(defaults)
        # Reload so the caller gets the same normalisation any other load
        # would apply.
        return self.load()

    # ------------------------------------------------------------------ internals

    def _read_json(self, path: pathlib.Path) -> dict[str, T.Any]:
        """Read a config JSON file, coercing to utf-8 if needed.

        The legacy ``check_encoding`` path used chardet to detect the
        encoding and rewrite the file as utf-8 on a non-utf-8 hit. We
        preserve that behaviour but scope it to a single retry and skip
        the random ``time.sleep`` loop.
        """
        data = path.read_bytes()

        # Strip a leading BOM if present — always silently, matches the
        # legacy ``del_bom`` flow.
        if data.startswith(codecs.BOM_UTF8):
            data = data[len(codecs.BOM_UTF8) :]
            path.write_bytes(data)

        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            detected = chardet.detect(data).get('encoding') or 'utf-8'
            self._logger.info(
                None,
                'config.json',
                f'non-utf-8 encoding detected ({detected}); converting',
                display=False,
            )
            text = data.decode(detected)
            path.write_bytes(text.encode('utf-8'))

        result: dict[str, T.Any] = json.loads(text)
        return result

    def _atomic_write(self, path: pathlib.Path, content: str) -> None:
        """Write ``content`` to ``path`` via a temp file + os.replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + '.', dir=str(path.parent))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(content)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # ------------------------------------------------------------------ normalise

    def _normalise(self, settings: AppSettings) -> AppSettings:
        """Apply the legacy ``read_settings`` fixups after validation.

        - Replace empty/missing ``bangumi_dir`` / ``temp_dir`` with the
          workspace defaults (same behaviour as legacy: existing absolute
          path stays; blank/bogus falls back to ``<working>/bangumi`` or
          ``<working>/temp``).
        - Replace an empty ``ua`` with the legacy default.
        - Clamp ``multi_thread`` and ``multi_downloading_segment`` to 5.
        """
        changes: dict[str, T.Any] = {}

        bangumi = settings.bangumi_dir.strip()
        if not bangumi or not pathlib.Path(bangumi).exists():
            changes['bangumi_dir'] = str(self._paths.bangumi_dir_default)

        temp = settings.temp_dir.strip()
        if not temp or not pathlib.Path(temp).exists():
            changes['temp_dir'] = str(self._paths.temp_dir_default)

        if not settings.ua.strip():
            changes['ua'] = _DEFAULT_UA

        if settings.multi_thread > _MAX_MULTI_THREAD:
            changes['multi_thread'] = _MAX_MULTI_THREAD

        if settings.multi_downloading_segment > _MAX_MULTI_DOWNLOADING_SEGMENT:
            changes['multi_downloading_segment'] = _MAX_MULTI_DOWNLOADING_SEGMENT

        if not changes:
            return settings
        return settings.model_copy(update=changes)

    def _denormalise(self, settings: AppSettings) -> AppSettings:
        """Undo ``_normalise`` for on-disk storage (matches legacy).

        If ``bangumi_dir`` / ``temp_dir`` point at the workspace defaults,
        round-trip them back to ``""`` so the file on disk stays stable.
        """
        changes: dict[str, T.Any] = {}
        default_bangumi = os.path.normcase(str(self._paths.bangumi_dir_default))
        default_temp = os.path.normcase(str(self._paths.temp_dir_default))

        if settings.bangumi_dir and os.path.normcase(settings.bangumi_dir) == default_bangumi:
            changes['bangumi_dir'] = ''
        if settings.temp_dir and os.path.normcase(settings.temp_dir) == default_temp:
            changes['temp_dir'] = ''

        if not changes:
            return settings
        return settings.model_copy(update=changes)

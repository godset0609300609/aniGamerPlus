"""Config service: bridges :class:`SettingsRepository` with the pydantic
:class:`~app.models.WebSettings` model the frontend consumes.
"""

from __future__ import annotations

import typing as T

import anyio.to_thread

from ..models import AppSettings, WebSettings
from ..settings_id_list import WEB_SETTINGS_KEYS
from ._factory import container_bound

if T.TYPE_CHECKING:
    from ..persistence.settings_repo import SettingsRepository


class ConfigService:
    """Object-oriented wrapper around the config persistence layer.

    The frontend operates on a flat 26-key subset of the full ``config.json``
    schema (see :data:`WEB_SETTINGS_KEYS`). This service projects onto that
    subset for reads, and merges an incoming :class:`WebSettings` back onto
    the full :class:`AppSettings` for writes.
    """

    WEB_KEYS: tuple[str, ...] = tuple(WEB_SETTINGS_KEYS)

    def __init__(self, settings_repo: SettingsRepository) -> None:
        self._repo = settings_repo

    # -- read ---------------------------------------------------------------

    async def read(self) -> WebSettings:
        return await anyio.to_thread.run_sync(lambda: self._repo.load().web_subset())

    def schema_keys(self) -> list[str]:
        return list(self.WEB_KEYS)

    # -- write --------------------------------------------------------------

    async def write(self, settings: WebSettings) -> None:
        """Merge ``settings`` (web subset) back onto the full :class:`AppSettings`.

        Reads the current full settings, overlays ONLY the fields defined on
        :class:`WebSettings`, and persists via the repo — preserving every
        non-web key (FTP creds, nested models like ``dashboard``) exactly as
        they were on disk.

        Uses per-field assignment rather than ``model_copy(update=...)``;
        the latter does a shallow merge, so if a future payload ever
        contained a nested model the whole sub-model would be replaced.
        """

        def _do_write() -> None:
            current = self._repo.load()
            incoming = settings.model_dump(by_alias=False)

            # Only the intersection of the payload and ``WebSettings``' field
            # set is trusted; extra keys in the payload are dropped.
            # We merge onto the full AppSettings dict so nested models are
            # re-validated by AppSettings.model_validate — this preserves
            # every non-web key while correctly coercing nested sub-models
            # (e.g. telegram) from dicts to their typed pydantic instances.
            allowed_fields = set(WebSettings.model_fields.keys())
            current_blob = current.model_dump(by_alias=False)
            for field_name in allowed_fields:
                if field_name in incoming:
                    current_blob[field_name] = incoming[field_name]

            updated = AppSettings.model_validate(current_blob)
            self._repo.save(updated)

        await anyio.to_thread.run_sync(_do_write)


get_config_service = container_bound(lambda c: ConfigService(c.settings_repo))
"""FastAPI dependency resolver for :class:`ConfigService`."""

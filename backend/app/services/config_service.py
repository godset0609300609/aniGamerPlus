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

        ``telegram`` is special-cased: :class:`WebSettings.telegram` is a
        :class:`~app.models.TelegramSettingsPublic` (secrets excluded), so a
        naive whole-field replace would wipe ``bot_token`` /
        ``webhook_secret`` on every save.  Instead we merge only the public
        sub-fields onto the current full telegram dict, leaving the secrets
        untouched — they can only be changed via
        ``set_telegram_bot_token`` / ``set_telegram_webhook_secret``.
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
                if field_name not in incoming:
                    continue
                if field_name == 'telegram':
                    merged_telegram = dict(current_blob.get('telegram') or {})
                    merged_telegram.update(incoming['telegram'])
                    current_blob['telegram'] = merged_telegram
                else:
                    current_blob[field_name] = incoming[field_name]

            updated = AppSettings.model_validate(current_blob)
            self._repo.save(updated)

        await anyio.to_thread.run_sync(_do_write)

    # -- telegram secrets (write-only) ---------------------------------------

    async def set_telegram_bot_token(self, bot_token: str) -> None:
        """Persist a new Telegram bot token.  Never echoed back to callers."""

        def _do() -> None:
            current = self._repo.load()
            updated_telegram = current.telegram.model_copy(update={'bot_token': bot_token})
            self._repo.save(current.model_copy(update={'telegram': updated_telegram}))

        await anyio.to_thread.run_sync(_do)

    async def telegram_bot_token_configured(self) -> bool:
        return await anyio.to_thread.run_sync(lambda: bool(self._repo.load().telegram.bot_token))

    async def set_telegram_webhook_secret(self, webhook_secret: str) -> None:
        """Persist a new Telegram webhook secret.  Never echoed back to callers."""

        def _do() -> None:
            current = self._repo.load()
            updated_telegram = current.telegram.model_copy(update={'webhook_secret': webhook_secret})
            self._repo.save(current.model_copy(update={'telegram': updated_telegram}))

        await anyio.to_thread.run_sync(_do)

    async def telegram_webhook_secret_configured(self) -> bool:
        return await anyio.to_thread.run_sync(lambda: bool(self._repo.load().telegram.webhook_secret))


get_config_service = container_bound(lambda c: ConfigService(c.settings_repo))
"""FastAPI dependency resolver for :class:`ConfigService`."""

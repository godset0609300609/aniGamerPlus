"""Fires ``/start`` to the app's own notification bot from a user's freshly
bound Telegram User API session.

This merges the tg_downloader session bind with the existing Bot-API
notification binding (``users.telegram_chat_id``, set the legacy way via
``/api/profile/telegram/start-link``) automatically — the user does not have
to separately go find and start the bot a second time.

Failure is always non-fatal: the caller (``qr_login`` / ``phone_login`` via
``_login_common.persist_login_success``) already wraps :meth:`bind` in
``contextlib.suppress(Exception)``, but :meth:`bind` also degrades
gracefully on its own (returns a failure :class:`NotificationBindOutcome`)
when no bot username is configured, so a bare call from a script/test
doesn't need the wrapper either. Unlike the previous bare-``bool`` return,
every failure now carries a :class:`NotificationBindResult` so callers (and
the "重試通知綁定" retry endpoint) can surface *why* the bind failed instead
of a generic "通知綁定失敗".
"""

from __future__ import annotations

import dataclasses
import enum
import re
import typing as T

import hydrogram.errors

from ..security.log_scrub import scrub_exception_for_log

if T.TYPE_CHECKING:
    import collections.abc

    import hydrogram

    from ..logging_ import Logger

_LOG_TAG = 'TG通知綁定'

#: Telegram username spec: 5-32 chars, alnum/underscore, but hydrogram/Telegram
#: also accept 4-char usernames for bots in practice — kept permissive at
#: 4-32 to match the frontend's ``^@\w{4,32}$`` validation (see
#: SettingsView.vue's bot_username normalization).
_BOT_USERNAME_RE = re.compile(r'^@\w{4,32}$')


class NotificationBindResult(enum.Enum):
    SUCCESS = 'success'
    BOT_USERNAME_NOT_CONFIGURED = 'bot_username_not_configured'
    BOT_USERNAME_INVALID = 'bot_username_invalid'  # regex fail
    BOT_NOT_FOUND = 'bot_not_found'  # hydrogram "USERNAME_INVALID" / "USERNAME_NOT_OCCUPIED" / "PEER_ID_INVALID"
    FLOOD_WAIT = 'flood_wait'
    TELEGRAM_ERROR = 'telegram_error'  # catch-all hydrogram RPCError
    UNKNOWN_ERROR = 'unknown_error'  # non-hydrogram exception


@dataclasses.dataclass(slots=True)
class NotificationBindOutcome:
    result: NotificationBindResult
    detail: str | None = None  # optional human-readable error message


class NotificationBinder:
    """Sends ``/start`` to the configured notification bot via a user session."""

    def __init__(
        self,
        bot_username_provider: collections.abc.Callable[[], str | None],
        *,
        logger: Logger | None = None,
    ) -> None:
        self._bot_username_provider = bot_username_provider
        self._logger = logger

    async def bind(self, client: hydrogram.Client) -> NotificationBindOutcome:
        """Send ``/start`` to the app's notification bot via *client*.

        Returns a failure :class:`NotificationBindOutcome` (without raising)
        when no ``bot_username`` is configured yet — the admin hasn't set up
        the notification bot, so there is nothing to bind to — or when the
        configured value doesn't match Telegram's username format. Any
        hydrogram-side failure (bot username doesn't resolve, flood-wait,
        network error, ...) also returns a failure outcome instead of
        propagating, matching this class's "notification bind is always
        best-effort" contract.
        """
        bot_username = self._bot_username_provider()
        if not bot_username or not bot_username.strip():
            return NotificationBindOutcome(NotificationBindResult.BOT_USERNAME_NOT_CONFIGURED)
        bot_username = bot_username.strip()
        handle = bot_username if bot_username.startswith('@') else f'@{bot_username}'
        if not _BOT_USERNAME_RE.match(handle):
            self._log_error(f'bot_username 格式錯誤: {handle!r}')
            return NotificationBindOutcome(NotificationBindResult.BOT_USERNAME_INVALID, detail=handle)

        try:
            await client.send_message(handle, '/start')
        except hydrogram.errors.FloodWait as exc:
            self._log_error(f'/start 送出失敗 (bot={handle}): flood wait — {scrub_exception_for_log(exc)}')
            return NotificationBindOutcome(NotificationBindResult.FLOOD_WAIT, detail=str(exc))
        except (
            hydrogram.errors.UsernameInvalid,
            hydrogram.errors.UsernameNotOccupied,
            hydrogram.errors.PeerIdInvalid,
        ) as exc:
            self._log_error(f'/start 送出失敗 (bot={handle}): 找不到 bot — {scrub_exception_for_log(exc)}')
            return NotificationBindOutcome(NotificationBindResult.BOT_NOT_FOUND, detail=str(exc))
        except hydrogram.errors.RPCError as exc:
            self._log_error(f'/start 送出失敗 (bot={handle}): {scrub_exception_for_log(exc)}')
            return NotificationBindOutcome(NotificationBindResult.TELEGRAM_ERROR, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
            self._log_error(f'/start 送出失敗 (bot={handle}): 未知錯誤 — {scrub_exception_for_log(exc)}')
            return NotificationBindOutcome(NotificationBindResult.UNKNOWN_ERROR, detail=str(exc))
        return NotificationBindOutcome(NotificationBindResult.SUCCESS)

    def _log_error(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)

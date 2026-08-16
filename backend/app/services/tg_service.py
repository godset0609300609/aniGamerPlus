"""Orchestrates the Telegram User API downloader pipeline.

Owns the collaborators wired in ``app.core.Container`` (client pool, QR/phone
login flows, notification binder, download watcher) and exposes the
lifecycle + per-user operations the API layer (``app.api.tg_api``) and
process startup (``app.core.build_container`` / ``app.main``) need.

Repo/DB calls are sync and offloaded via ``anyio.to_thread.run_sync``;
hydrogram calls are natively async and awaited directly — this class's own
methods are ``async def`` throughout so route handlers can call it directly.
"""

from __future__ import annotations

import contextlib
import functools
import typing as T

import anyio.to_thread

from ..security.log_scrub import scrub_exception_for_log
from ..tg_downloader import hydrogram_compat as _hydrogram_compat

if T.TYPE_CHECKING:
    import hydrogram

    from ..logging_ import Logger
    from ..models import TgWatchedChat, TgWatchedChatCreate, TgWatchedChatUpdate
    from ..persistence.tg_downloaded_media_repo import TgDownloadedMediaEntry, TgDownloadedMediaRepository
    from ..persistence.tg_session_repo import TgSessionEntry, TgSessionRepository
    from ..persistence.tg_watched_chat_repo import TgWatchedChatRepository
    from ..persistence.user_repo import UserRow
    from ..tg_downloader.client_pool import TgClientPool
    from ..tg_downloader.downloader import TgDownloadWatcher
    from ..tg_downloader.notification_binder import NotificationBinder, NotificationBindOutcome
    from ..tg_downloader.phone_login import PhoneLoginService
    from ..tg_downloader.qr_login import QrLoginService

# Importing ``app.tg_downloader`` above already runs its __init__, which
# applies this same patch — but this module is the one that actually calls
# ``client.get_dialogs()`` (in ``list_available_chats`` below), so call it
# again explicitly rather than relying on that import ordering. It's
# idempotent (see ``hydrogram_compat.apply_patches``), so this costs
# nothing on the normal "package __init__ already ran" path; it's only
# insurance against a future refactor of ``app.tg_downloader.__init__``
# dropping the call. See ``hydrogram_compat``'s docstring for what defect
# this works around.
_hydrogram_compat.apply_patches()

_LOG_TAG = 'TG服務'


class TgService:
    """Composition point for every Telegram User API operation."""

    def __init__(
        self,
        session_repo: TgSessionRepository,
        watched_chat_repo: TgWatchedChatRepository,
        downloaded_media_repo: TgDownloadedMediaRepository,
        client_pool: TgClientPool,
        qr_login: QrLoginService,
        phone_login: PhoneLoginService,
        notification_binder: NotificationBinder,
        watcher: TgDownloadWatcher,
        *,
        logger: Logger | None = None,
    ) -> None:
        self._session_repo = session_repo
        self._watched_chat_repo = watched_chat_repo
        self._downloaded_media_repo = downloaded_media_repo
        self._client_pool = client_pool
        self._qr_login = qr_login
        self._phone_login = phone_login
        self._notification_binder = notification_binder
        self._watcher = watcher
        self._logger = logger

    # ------------------------------------------------------------------ lifecycle

    async def startup(self) -> None:
        """Reconnect every active session and register its download watcher.

        Called once at process startup (the API process — the scheduler
        process does not need live hydrogram clients). Best-effort per user:
        one user's dead/expired session must not prevent the others from
        connecting.
        """
        active = await anyio.to_thread.run_sync(self._session_repo.list_active)
        for entry in active:
            with contextlib.suppress(Exception):
                await self._connect_and_register(entry.user_id)

    async def shutdown(self) -> None:
        await self._client_pool.disconnect_all()

    async def _connect_and_register(self, user_id: str) -> None:
        client = await self._client_pool.get(user_id)
        if client is None:
            return
        await anyio.to_thread.run_sync(functools.partial(self._watcher.register, user_id, client))

    # ------------------------------------------------------------------ session status

    async def get_status(self, user_id: str) -> TgSessionEntry | None:
        return await anyio.to_thread.run_sync(functools.partial(self._session_repo.get_by_user_id, user_id))

    async def revoke_session(self, user_id: str) -> None:
        client = await self._client_pool.get(user_id)
        if client is not None:
            self._watcher.unregister(user_id, client)
        await self._client_pool.disconnect(user_id)
        await anyio.to_thread.run_sync(functools.partial(self._session_repo.revoke, user_id))

    async def rebind_notification(self, user_id: str) -> NotificationBindOutcome | None:
        """Re-run the notification-bind ``/start`` for an already-bound session.

        Backs the Settings UI's "重試通知綁定" button (``POST
        /api/tg/session/rebind-notification``) for the "帳號已綁定，通知綁定
        失敗" state. Returns ``None`` when there is no active session to bind
        with (``_client_pool.get`` returns ``None`` both when the user never
        bound and when a stored session just failed to reconnect — either
        way there is nothing to retry until the user re-binds their
        account); the API layer turns that into a 404. Otherwise the fresh
        outcome is persisted to the session row (independent of the session
        string itself) and returned.
        """
        client = await self._client_pool.get(user_id)
        if client is None:
            return None
        outcome = await self._notification_binder.bind(client)
        await anyio.to_thread.run_sync(
            functools.partial(
                self._session_repo.update_notification_bind_status,
                user_id,
                status=outcome.result.value,
                error=outcome.detail,
            )
        )
        return outcome

    # ------------------------------------------------------------------ QR login

    async def start_qr_login(self, user_id: str) -> tuple[str, str, str]:
        return await self._qr_login.start(user_id)

    async def poll_qr_login(self, login_token: str, user_id: str) -> dict[str, T.Any]:
        result = await self._qr_login.poll(login_token, user_id)
        await self._on_login_result(result)
        return result

    async def submit_qr_password(self, login_token: str, password: str, user_id: str) -> dict[str, T.Any]:
        result = await self._qr_login.submit_password(login_token, password, user_id)
        await self._on_login_result(result)
        return result

    # ------------------------------------------------------------------ phone login

    async def send_phone_code(self, user_id: str, phone: str) -> str:
        return await self._phone_login.send_code(user_id, phone)

    async def submit_phone_code(self, login_token: str, code: str, user_id: str) -> dict[str, T.Any]:
        result = await self._phone_login.submit_code(login_token, code, user_id)
        await self._on_login_result(result)
        return result

    async def submit_phone_password(self, login_token: str, password: str, user_id: str) -> dict[str, T.Any]:
        result = await self._phone_login.submit_password(login_token, password, user_id)
        await self._on_login_result(result)
        return result

    async def _on_login_result(self, result: dict[str, T.Any]) -> None:
        """Warm the client pool + register the download watcher right after a bind succeeds.

        ``result['user_id']`` is only present when ``status == 'success'``
        (see ``QrLoginService._status_payload`` / ``PhoneLoginService._status_payload``).
        """
        if result.get('status') != 'success':
            return
        user_id = result.get('user_id')
        if not user_id:
            return
        with contextlib.suppress(Exception):
            await self._connect_and_register(user_id)

    # ------------------------------------------------------------------ watched chats

    async def list_watched_chats(self, user_id: str) -> list[TgWatchedChat]:
        return await anyio.to_thread.run_sync(functools.partial(self._watched_chat_repo.list_by_user, user_id))

    async def add_watched_chat(self, user_id: str, payload: TgWatchedChatCreate) -> TgWatchedChat:
        result = await anyio.to_thread.run_sync(functools.partial(self._watched_chat_repo.insert, user_id, payload))
        await self._refresh_watcher(user_id)
        if result.backfill_enabled:
            # _trigger_backfill mutates the row (marks it 'pending') — the
            # Pydantic model returned by insert() above is an immutable
            # snapshot from before that write, so the caller must use the
            # fresh value it hands back or the API response would report a
            # stale (pre-trigger) backfill_status.
            result = await self._trigger_backfill(user_id, result)
        return result

    async def update_watched_chat(
        self, user_id: str, watched_chat_id: int, payload: TgWatchedChatUpdate
    ) -> TgWatchedChat | None:
        before = await anyio.to_thread.run_sync(
            functools.partial(self._watched_chat_repo.get_by_id, user_id, watched_chat_id)
        )
        result = await anyio.to_thread.run_sync(
            functools.partial(self._watched_chat_repo.update, user_id, watched_chat_id, payload)
        )
        await self._refresh_watcher(user_id)
        # Only fire on the False -> True transition — an update that leaves
        # backfill_enabled untouched (or already True) must not re-trigger a
        # scan on every unrelated edit (e.g. toggling a media-type filter).
        if result is not None and result.backfill_enabled and (before is None or not before.backfill_enabled):
            result = await self._trigger_backfill(user_id, result)
        return result

    async def delete_watched_chat(self, user_id: str, watched_chat_id: int) -> None:
        await anyio.to_thread.run_sync(functools.partial(self._watched_chat_repo.delete, user_id, watched_chat_id))
        await self._refresh_watcher(user_id)

    async def _refresh_watcher(self, user_id: str) -> None:
        """Re-register the message handler so a chat-list edit takes effect immediately."""
        if not self._client_pool.is_connected(user_id):
            return
        with contextlib.suppress(Exception):
            await self._connect_and_register(user_id)

    # ------------------------------------------------------------------ historical backfill

    async def retry_backfill(self, user_id: str, watched_chat_id: int) -> TgWatchedChat | None:
        """Manually (re-)trigger a backfill scan for *watched_chat_id*.

        Backs both the "重試" button for a ``failed`` run and "重新回填" for
        an already-``done`` one (the frontend shows a confirm dialog before
        calling this for the latter — re-scanning relies on
        ``UNIQUE(user_id, chat_id, message_id)`` to skip files still on
        record, see ``app.tg_downloader.backfill.TgBackfillService``).

        A no-op (returns the row unchanged) when a scan is already
        ``pending``/``running`` — retrying mid-flight would double-dispatch
        the actor. Returns ``None`` if *watched_chat_id* doesn't belong to
        *user_id*.
        """
        watched = await anyio.to_thread.run_sync(
            functools.partial(self._watched_chat_repo.get_by_id, user_id, watched_chat_id)
        )
        if watched is None:
            return None
        if watched.backfill_status in ('pending', 'running'):
            return watched
        return await self._trigger_backfill(user_id, watched, force=True)

    async def _trigger_backfill(self, user_id: str, watched: TgWatchedChat, *, force: bool = False) -> TgWatchedChat:
        """Mark *watched* 'pending', dispatch ``tg_backfill_actor``, and return the updated row.

        Skips (no-op, returns *watched* unchanged) when a scan is already
        ``pending``/``running``/``done`` unless *force* is set — the
        automatic call sites (``add_watched_chat`` / ``update_watched_chat``
        above) must never re-trigger an already-completed backfill just
        because the caller re-submitted ``backfill_enabled=True``;
        ``retry_backfill`` passes ``force=True`` to bypass that for an
        explicit user request.
        """
        if not force and watched.backfill_status in ('pending', 'running', 'done'):
            return watched
        await anyio.to_thread.run_sync(
            functools.partial(self._watched_chat_repo.mark_backfill_pending, user_id, watched.id)
        )
        try:
            from ..tasks.tg_backfill_tick import tg_backfill_actor

            tg_backfill_actor.send(user_id, watched.chat_id, watched.backfill_days)
        except Exception as exc:  # noqa: BLE001 — no broker configured (tests / CLI mode without Redis)
            if self._logger is not None:
                self._logger.error(
                    None,
                    _LOG_TAG,
                    f'user_id={user_id} chat_id={watched.chat_id} 回填任務派送失敗: {exc}',
                    display=False,
                )
        # mark_backfill_pending above just wrote the fresh status — read it
        # back so callers (add_watched_chat/update_watched_chat's API
        # response, retry_backfill) never hand out the stale pre-trigger
        # snapshot they passed in as *watched*.
        updated = await anyio.to_thread.run_sync(
            functools.partial(self._watched_chat_repo.get_by_id, user_id, watched.id)
        )
        return updated if updated is not None else watched

    async def list_available_chats(
        self, user_id: str, *, limit: int = 500
    ) -> tuple[list[hydrogram.types.Dialog], bool]:
        """Live-query the chats *user_id*'s Telegram account is currently a member of.

        Returns ``(dialogs, truncated)`` — *dialogs* holds at most *limit*
        entries; *truncated* is ``True`` when more were available than that
        (B-09/G-07 of the security audit: an unbounded fetch here let an
        account with a huge dialog list force an unbounded live MTProto
        walk + an unbounded JSON response on every call).

        Channels the user can't access (Telegram's raw ``ChannelForbidden``
        type — e.g. a channel they were kicked from) used to crash
        ``client.get_dialogs()`` outright with an ``AttributeError`` deep
        inside hydrogram: ``hydrogram.types.Chat._parse_channel_chat``
        unconditionally reads attributes, e.g. ``channel.verified``, that
        only exist on the regular ``Channel`` type, and Telegram's
        ``messages.getDialogs`` is paginated in batches of up to 100 with
        hydrogram building every ``Dialog`` in a batch before yielding any
        of them — so one bad dialog anywhere in a batch left the whole
        generator unusable, with no way to resume past it. The primary fix
        for that now lives in ``app.tg_downloader.hydrogram_compat``
        (applied at import time of ``app.tg_downloader`` — see that
        package's and that module's docstrings): it patches
        ``Chat._parse_channel_chat`` to tolerate ``ChannelForbidden``
        directly, so a forbidden channel's dialog parses into a minimal
        ``Chat`` instead of raising, and the walk over every dialog
        completes normally with nothing silently missing.

        The ``except AttributeError`` below is therefore no longer the
        normal path for forbidden channels — it's a backstop against some
        *other*, not-yet-special-cased attribute hydrogram reads
        unconditionally on some other raw type turning out to be missing.
        Should that happen, this still degrades gracefully to "return
        whatever dialogs were already fetched from earlier batches" rather
        than 500ing the entire listing.
        """
        client = await self._client_pool.get(user_id)
        if client is None:
            return [], False
        dialogs: list[hydrogram.types.Dialog] = []
        # hydrogram's stub types get_dialogs() as Optional; at runtime it always
        # yields a generator (errors raise instead). Guard for stub tightness.
        dialogs_gen = client.get_dialogs(limit=limit + 1)
        if dialogs_gen is None:
            return dialogs, False
        try:
            async for dialog in dialogs_gen:
                dialogs.append(dialog)
        except AttributeError as exc:
            # Unexpected fallback: the ChannelForbidden case this used to be
            # is now handled by hydrogram_compat's patch (see this method's
            # docstring), so reaching here means some other, not-yet-patched
            # hydrogram parsing gap was hit.
            self._log_warning(
                f'user_id={user_id} 略過無法解析的 dialog（非預期的解析失敗，'
                f'hydrogram_compat 補丁未涵蓋此情況）: {scrub_exception_for_log(exc)}'
            )
        truncated = len(dialogs) > limit
        if truncated:
            dialogs = dialogs[:limit]
        return dialogs, truncated

    # ------------------------------------------------------------------ downloads

    async def list_downloads(
        self, user_id: str, *, page: int = 1, size: int = 50
    ) -> tuple[list[TgDownloadedMediaEntry], int]:
        return await anyio.to_thread.run_sync(
            functools.partial(self._downloaded_media_repo.list_by_user, user_id, page=page, size=size)
        )

    # ------------------------------------------------------------------ logging

    def _log_warning(self, message: str) -> None:
        """Log at warning-ish severity — ``Logger`` has no ``.warning``, so this
        is ``.error`` with ``display=False`` (never surfaced to the CLI/dashboard
        as an error banner, only written to the log file), matching the
        ``_log_error`` convention used throughout ``app.tg_downloader.*``."""
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)


def resolve_bot_username(settings_provider: T.Callable[[], T.Any]) -> str | None:
    """Adapter: read ``telegram.bot_username`` off whatever settings object *settings_provider* returns.

    Small free function (rather than a method) so ``NotificationBinder`` can
    be constructed with a plain zero-arg callable without depending on the
    concrete settings/repo types — mirrors ``core.py``'s
    ``_rate_limit_provider`` closures for other "read a live setting" needs.
    """
    settings = settings_provider()
    bot_username = getattr(getattr(settings, 'telegram', None), 'bot_username', '') or ''
    return bot_username or None


def user_row_to_owner_label(user: UserRow) -> str:
    """Best-effort human label for a user — used in log lines only."""
    return f'{user.username} ({user.id})'

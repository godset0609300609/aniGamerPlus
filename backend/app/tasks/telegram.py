"""Async Telegram outbound actors with built-in 429 retry.

Every outbound Telegram API call (send / edit / delete a message) goes
through one of these actors so dramatiq's retry middleware handles 429
``Too Many Requests`` automatically with exponential backoff.

Bot-blocked / chat-not-found errors are NOT retried — those are
permanent and trigger binding cleanup in the Notifier-side handler.
"""

from __future__ import annotations

import typing as T

import dramatiq

from .. import dramatiq_setup as _setup
from ..services.telegram_client import (
    TelegramApiError,
    TelegramBotBlockedError,
    TelegramChatNotFoundError,
)
from ..services.telegram_client_cache import resolve_telegram_client
from ..services.telegram_outbound_limiter import get_telegram_outbound_limiter

if T.TYPE_CHECKING:
    from ..core import Container
    from ..services.telegram_notifier import TelegramNotifier

_setup.init_broker()

_BT_EVENTS = frozenset(
    {'bt_dispatched', 'bt_status_update', 'bt_landing_progress', 'bt_landed', 'bt_failed'}
)


def _retry_when_429(retries_so_far: int, exc: Exception) -> bool:
    """dramatiq retry predicate: retry on 429, give up on permanent errors."""
    if isinstance(exc, (TelegramBotBlockedError, TelegramChatNotFoundError)):
        return False
    if isinstance(exc, TelegramApiError):
        return exc.status_code == 429
    return False


@dramatiq.actor(
    queue_name='telegram',
    max_retries=5,
    min_backoff=1_000,
    max_backoff=60_000,
    retry_when=_retry_when_429,
)
async def send_message_actor(
    chat_id: int,
    text: str,
    *,
    bot_token: str,
    reply_markup: dict[str, object] | None = None,
    disable_web_page_preview: bool = True,
) -> dict[str, object] | None:
    """Send a single message; returns the raw API result (incl. message_id)."""
    client = resolve_telegram_client(bot_token)
    if client is None:
        return None
    await get_telegram_outbound_limiter().acquire(chat_id)
    return await client.send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )


@dramatiq.actor(
    queue_name='telegram',
    max_retries=5,
    min_backoff=1_000,
    max_backoff=60_000,
    retry_when=_retry_when_429,
)
async def edit_message_actor(
    chat_id: int,
    message_id: int,
    text: str,
    *,
    bot_token: str,
    reply_markup: dict[str, object] | None = None,
) -> None:
    """Edit a message; idempotent on 'message is not modified' (treated as success)."""
    client = resolve_telegram_client(bot_token)
    if client is None:
        return
    await get_telegram_outbound_limiter().acquire(chat_id)
    try:
        await client.edit_message_text(chat_id, message_id, text, reply_markup=reply_markup)
    except TelegramApiError as exc:
        if 'message is not modified' in str(exc).lower():
            return  # treat as success — no need to retry
        raise


@dramatiq.actor(
    queue_name='telegram',
    max_retries=2,
    min_backoff=500,
    retry_when=_retry_when_429,
)
async def delete_message_actor(
    chat_id: int,
    message_id: int,
    *,
    bot_token: str,
) -> None:
    """Best-effort delete; missing messages return success."""
    client = resolve_telegram_client(bot_token)
    if client is None:
        return
    await get_telegram_outbound_limiter().acquire(chat_id)
    try:
        await client.delete_message(chat_id, message_id)
    except TelegramApiError as exc:
        # 'message to delete not found' / 'message can't be deleted' → drop
        msg = str(exc).lower()
        if 'not found' in msg or "can't be deleted" in msg or 'cant be deleted' in msg:
            return
        raise


@dramatiq.actor(queue_name='telegram', max_retries=0)
async def notify_event_actor(**kwargs: T.Any) -> None:
    """Dispatch a download (or BT downloader) lifecycle event through TelegramNotifier.

    Decouples the sync worker thread from the async notifier — the worker
    calls ``notify_event_actor.send_with_options(kwargs={...})`` and returns
    immediately; this actor runs in the dramatiq worker process where an
    asyncio event loop is already running via the AsyncIO middleware.

    ``event`` picks the payload shape: 'bt_dispatched' / 'bt_status_update' /
    'bt_landing_progress' / 'bt_landed' / 'bt_failed' carry BT-specific
    kwargs (title/feed_name/... — no owner_id/sn) and route to
    ``notify_bt_event``; everything else is a per-download owner event
    routed to ``notify_download_event``.
    """
    from ..core import build_container

    container = build_container()
    notifier = _build_notifier(container)
    if notifier is None:
        return
    if kwargs.get('event') in _BT_EVENTS:
        await notifier.notify_bt_event(**kwargs)
    else:
        await notifier.notify_download_event(**kwargs)


def _build_notifier(container: Container) -> TelegramNotifier | None:
    """Construct a TelegramNotifier from the given container, or None when disabled."""
    from ..models import TelegramSettings as _TelegramSettings
    from ..services.telegram_notifier import TelegramNotifier as _TelegramNotifier

    tg_client = container.telegram_client
    if tg_client is None:
        return None

    def _settings_provider() -> _TelegramSettings:
        return container.settings_repo.load().telegram

    return _TelegramNotifier(
        client=tg_client,
        user_repo=container.user_repo,
        settings_provider=_settings_provider,
        live_messages=container.live_messages,
        bt_live_messages=container.bt_live_messages,
        logger=container.logger,
    )

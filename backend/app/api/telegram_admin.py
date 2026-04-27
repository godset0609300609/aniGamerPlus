"""Admin endpoints for Telegram bot / webhook management.

Mounted at ``/api/admin/telegram``. All routes require the ``admin`` role.
"""

from __future__ import annotations

import logging
import typing as T

import fastapi

from ..models import AppSettings
from ..persistence.user_repo import UserRow
from ..services.telegram_client import TelegramApiError, TelegramClient
from ..services.telegram_client_cache import resolve_telegram_client
from ..services.telegram_commands import BOT_MENU_COMMANDS
from .deps import get_settings, require_admin_user

_log = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix='/api/admin/telegram', tags=['telegram-admin'])


def _get_telegram_client(
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
) -> TelegramClient | None:
    """Resolve a TelegramClient from the CURRENT settings.telegram.bot_token.

    Recomputes every request via the module-level singleton cache, so an
    admin who just saved a new token sees the new client without a process
    restart.
    """
    return resolve_telegram_client(settings.telegram.bot_token)


def _require_client(
    client: T.Annotated[TelegramClient | None, fastapi.Depends(_get_telegram_client)],
) -> TelegramClient:
    """Raise 400 when bot_token is not configured."""
    if client is None:
        raise fastapi.HTTPException(
            status_code=400,
            detail='bot_token is not configured in telegram settings',
        )
    return client


# ---------------------------------------------------------------------------
# Webhook endpoints
# ---------------------------------------------------------------------------


@router.post('/webhook/register')
async def register_webhook(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
    client: T.Annotated[TelegramClient, fastapi.Depends(_require_client)],
) -> dict[str, object]:
    """Register the webhook URL with Telegram using the configured settings."""
    tg = settings.telegram
    pairs = [('public_url', tg.public_url), ('webhook_secret', tg.webhook_secret), ('bot_token', tg.bot_token)]
    missing = [f for f, v in pairs if not v]
    if missing:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f'Missing telegram settings: {", ".join(missing)}',
        )
    url = f'{tg.public_url.rstrip("/")}/api/webhooks/telegram/{tg.webhook_secret}'
    await client.set_webhook(
        url,
        secret_token=tg.webhook_secret,
        allowed_updates=['message', 'callback_query'],
    )
    # Push the bot's "/" menu in the same flow so admins don't have to do it
    # manually. Best-effort: if the menu push fails the webhook is already
    # registered and is the more important side-effect.
    commands_pushed = await _push_bot_commands(client)
    # The scheduler process's TelegramNotifier reads its client from the
    # container at startup and is NOT affected by the cache used here.
    # Remind the admin to restart the scheduler after a token rotation.
    return {
        'ok': True,
        'url': url,
        'commands_pushed': commands_pushed,
        'scheduler_restart_hint': '若剛才變更過 bot token，請重新啟動 scheduler 以讓下載通知使用新 token。',
    }


@router.post('/webhook/delete')
async def delete_webhook(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    client: T.Annotated[TelegramClient, fastapi.Depends(_require_client)],
) -> dict[str, object]:
    """Remove the currently registered webhook from Telegram."""
    # Clear the "/" menu first so the bot reverts to a no-menu state if the
    # admin is decommissioning it. Best-effort: delete_webhook is the
    # primary action and must run even if menu clear fails.
    commands_cleared = await _clear_bot_commands(client)
    await client.delete_webhook(drop_pending_updates=True)
    return {'ok': True, 'commands_cleared': commands_cleared}


@router.get('/webhook/info')
async def webhook_info(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    client: T.Annotated[TelegramClient, fastapi.Depends(_require_client)],
) -> dict[str, object]:
    """Return current webhook info from Telegram."""
    return await client.get_webhook_info()


# ---------------------------------------------------------------------------
# Bot info
# ---------------------------------------------------------------------------


@router.get('/bot/me')
async def bot_me(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    client: T.Annotated[TelegramClient, fastapi.Depends(_require_client)],
) -> dict[str, object]:
    """Return bot identity info — used to verify the bot token is valid."""
    return await client.get_me()


# ---------------------------------------------------------------------------
# Bot commands ("/" menu)
# ---------------------------------------------------------------------------


@router.post('/commands/refresh')
async def refresh_bot_commands(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    client: T.Annotated[TelegramClient, fastapi.Depends(_require_client)],
) -> dict[str, object]:
    """Push the canonical command list to Telegram (populates the "/" menu).

    Idempotent — safe to call any time. Useful when ``BOT_MENU_COMMANDS``
    changes without the admin needing to re-register the webhook.
    """
    await client.set_my_commands(BOT_MENU_COMMANDS)
    return {'ok': True, 'count': len(BOT_MENU_COMMANDS)}


# ---------------------------------------------------------------------------
# Internal best-effort helpers
# ---------------------------------------------------------------------------


async def _push_bot_commands(client: TelegramClient) -> bool:
    """Push BOT_MENU_COMMANDS; log on failure but never raise."""
    try:
        await client.set_my_commands(BOT_MENU_COMMANDS)
    except TelegramApiError as exc:
        _log.warning('setMyCommands failed (best-effort): %s', exc)
        return False
    return True


async def _clear_bot_commands(client: TelegramClient) -> bool:
    """Clear the bot's "/" menu; log on failure but never raise."""
    try:
        await client.delete_my_commands()
    except TelegramApiError as exc:
        _log.warning('deleteMyCommands failed (best-effort): %s', exc)
        return False
    return True

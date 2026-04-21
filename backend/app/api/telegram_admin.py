"""Admin endpoints for Telegram bot / webhook management.

Mounted at ``/api/admin/telegram``. All routes require the ``admin`` role.
"""

from __future__ import annotations

import typing as T

import fastapi

from ..models import AppSettings
from ..persistence.user_repo import UserRow
from ..services._factory import container_bound
from ..services.telegram_client import TelegramClient
from .deps import get_settings, require_admin_user

router = fastapi.APIRouter(prefix='/api/admin/telegram', tags=['telegram-admin'])

# Container-bound dependency: resolves the TelegramClient (may be None).
_get_telegram_client: T.Callable[[], TelegramClient | None] = container_bound(
    lambda c: getattr(c, 'telegram_client', None)
)


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
    return {'ok': True, 'url': url}


@router.post('/webhook/delete')
async def delete_webhook(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    client: T.Annotated[TelegramClient, fastapi.Depends(_require_client)],
) -> dict[str, object]:
    """Remove the currently registered webhook from Telegram."""
    await client.delete_webhook(drop_pending_updates=True)
    return {'ok': True}


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

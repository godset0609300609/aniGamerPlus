"""User-facing profile endpoints for Telegram notification binding.

Mounted at ``/api/profile/telegram``. All routes require a logged-in user
(admin OR downloader role) — these endpoints operate on the caller's own
account only.
"""

from __future__ import annotations

import datetime
import logging
import secrets
import typing as T

import anyio.to_thread
import fastapi
import pydantic

from ..models import AppSettings
from ..persistence.user_repo import UserRepository, UserRow
from ..services._factory import container_bound
from ..services.telegram_client import TelegramClient
from ..services.telegram_client_cache import resolve_telegram_client
from .deps import get_settings, require_any_user

_log = logging.getLogger(__name__)

_TOKEN_TTL_SECONDS = 600  # 10 minutes

router = fastapi.APIRouter(prefix='/api/profile/telegram', tags=['profile-telegram'])


def _get_telegram_client(
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
) -> TelegramClient | None:
    """Resolve a TelegramClient from the CURRENT settings.telegram.bot_token.

    Recomputes every request via the module-level singleton cache, so
    an admin who just saved a new token sees the new client without a
    process restart.
    """
    return resolve_telegram_client(settings.telegram.bot_token)


_get_user_repo: T.Callable[[], UserRepository] = container_bound(lambda c: c.user_repo)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token_is_expired(expires_at: datetime.datetime | None) -> bool:
    """Return True when *expires_at* is in the past or is None."""
    if expires_at is None:
        return True
    now = datetime.datetime.now(datetime.UTC)
    # expires_at stored as naive UTC datetime from SQLite; normalise.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.UTC)
    return now >= expires_at


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class _NotifyEnabledBody(pydantic.BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post('/start-link')
async def start_link(
    current_user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
    telegram_client: T.Annotated[TelegramClient | None, fastapi.Depends(_get_telegram_client)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(_get_user_repo)],
) -> dict[str, object]:
    """Generate a one-time Telegram binding link for the current user.

    Requires ``settings.telegram.bot_token`` and ``settings.telegram.public_url``
    to be configured; responds 400 with ``detail='telegram_not_configured'``
    otherwise.

    Always regenerates (overwrites) a prior pending token even if it hasn't
    expired — the user may have lost the previous link.
    """
    tg = settings.telegram
    if not tg.bot_token or not tg.public_url:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail='telegram_not_configured',
        )

    if telegram_client is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail='telegram_not_configured',
        )

    # Resolve bot username once per request.
    try:
        me = await telegram_client.get_me()
    except Exception as exc:
        _log.exception('profile/telegram/start-link: failed to call getMe')
        reason = type(exc).__name__  # short, safe — don't leak token in string form
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail=f'telegram_bot_unreachable: {reason}',
        ) from exc

    bot_username = str(me.get('username', ''))
    if not bot_username:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail='telegram_bot_username_missing',
        )

    token = secrets.token_urlsafe(16)
    now = datetime.datetime.now(datetime.UTC)
    expires_at = now + datetime.timedelta(seconds=_TOKEN_TTL_SECONDS)

    uid = current_user.id
    exp = expires_at
    await anyio.to_thread.run_sync(lambda: user_repo.set_telegram_link_token(uid, token, exp))

    link_url = f'https://t.me/{bot_username}?start={token}'
    return {
        'link_url': link_url,
        'expires_in_seconds': _TOKEN_TTL_SECONDS,
    }


@router.post('/unlink')
async def unlink(
    current_user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(_get_user_repo)],
) -> dict[str, bool]:
    """Clear the Telegram chat ID and any pending link token for the current user."""
    uid = current_user.id
    await anyio.to_thread.run_sync(lambda: user_repo.clear_telegram_binding(uid))
    return {'ok': True}


@router.get('/status')
async def status(
    current_user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(_get_user_repo)],
) -> dict[str, object]:
    """Return the Telegram binding status for the current user."""
    uid = current_user.id
    user = await anyio.to_thread.run_sync(lambda: user_repo.get(uid))
    if user is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail='User not found',
        )

    bound = user.telegram_chat_id is not None
    link_pending = user.telegram_link_token is not None and not _token_is_expired(user.telegram_link_token_expires_at)
    return {
        'bound': bound,
        'chat_id': user.telegram_chat_id,
        'enabled': user.telegram_notify_enabled,
        'link_pending': link_pending,
    }


@router.patch('/notify-enabled')
async def notify_enabled(
    body: _NotifyEnabledBody,
    current_user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(_get_user_repo)],
) -> dict[str, bool]:
    """Update the per-user Telegram notification opt-in flag."""
    uid = current_user.id
    enabled = body.enabled
    await anyio.to_thread.run_sync(lambda: user_repo.set_telegram_notify_enabled(uid, enabled))
    return {'ok': True}

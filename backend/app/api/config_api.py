"""Endpoints for reading and updating the web-visible config keys."""

from __future__ import annotations

import typing as T

import anyio.to_thread
import fastapi

from ..models import (
    BilibiliCookieUpdateRequest,
    ConfigSchema,
    CookieUpdateRequest,
    PutioTokenUpdateRequest,
    SimpleStatus,
    TelegramBotTokenUpdateRequest,
    TelegramWebhookSecretUpdateRequest,
    WebSettings,
)
from ..persistence.bilibili_cookie_repo import BilibiliCookieRepository
from ..persistence.cookie_repo import CookieRepository
from ..persistence.putio_token_repo import PutioTokenRepository
from ..persistence.user_repo import UserRow
from ..services._factory import container_bound
from ..services.config_service import ConfigService, get_config_service
from .deps import require_admin_user, require_any_user

router = fastapi.APIRouter(tags=['config'])

# ---------------------------------------------------------------------------
# Cookie repo dependencies — container-bound, same pattern as other services.
# ---------------------------------------------------------------------------

get_cookie_repo: T.Callable[[], CookieRepository] = container_bound(lambda c: c.cookie_repo)
"""FastAPI dependency resolver for :class:`CookieRepository`."""

get_bilibili_cookie_repo: T.Callable[[], BilibiliCookieRepository] = container_bound(lambda c: c.bilibili_cookie_repo)
"""FastAPI dependency resolver for :class:`BilibiliCookieRepository`."""

get_putio_token_repo: T.Callable[[], PutioTokenRepository] = container_bound(lambda c: c.putio_token_repo)
"""FastAPI dependency resolver for :class:`PutioTokenRepository`."""


@router.get('/config/schema', response_model=ConfigSchema)
async def config_schema(
    _: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[ConfigService, fastapi.Depends(get_config_service)],
) -> ConfigSchema:
    return ConfigSchema(keys=service.schema_keys())


@router.get('/config', response_model=WebSettings)
async def read_config(
    _: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[ConfigService, fastapi.Depends(get_config_service)],
) -> WebSettings:
    return await service.read()


@router.put('/config', response_model=SimpleStatus)
async def write_config(
    payload: WebSettings,
    _: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[ConfigService, fastapi.Depends(get_config_service)],
) -> SimpleStatus:
    await service.write(payload)
    return SimpleStatus()


@router.put('/config/cookie', response_model=SimpleStatus)
async def put_cookie(
    payload: CookieUpdateRequest,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[CookieRepository, fastapi.Depends(get_cookie_repo)],
) -> SimpleStatus:
    """Set the Bahamut cookie string.  Admin only.  Never returned back."""
    cookie = payload.cookie
    await anyio.to_thread.run_sync(lambda: repo.write(cookie))
    return SimpleStatus()


@router.get('/config/cookie/status')
async def cookie_status(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[CookieRepository, fastapi.Depends(get_cookie_repo)],
) -> dict[str, bool]:
    """Return whether a cookie is currently configured (true/false only)."""
    configured = await anyio.to_thread.run_sync(repo.exists_and_nonempty)
    return {'configured': configured}


@router.put('/config/bilibili-cookie', response_model=SimpleStatus)
async def put_bilibili_cookie(
    payload: BilibiliCookieUpdateRequest,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[BilibiliCookieRepository, fastapi.Depends(get_bilibili_cookie_repo)],
) -> SimpleStatus:
    """Set the Bilibili cookie string (k=v; k=v; format).  Admin only.  Never returned back."""
    cookie = payload.cookie
    await anyio.to_thread.run_sync(lambda: repo.write(cookie))
    return SimpleStatus()


@router.get('/config/bilibili-cookie/status')
async def bilibili_cookie_status(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[BilibiliCookieRepository, fastapi.Depends(get_bilibili_cookie_repo)],
) -> dict[str, bool]:
    """Return whether a Bilibili cookie is currently configured (true/false only)."""
    configured = await anyio.to_thread.run_sync(repo.exists_and_nonempty)
    return {'configured': configured}


@router.put('/config/putio-token', response_model=SimpleStatus)
async def put_putio_token(
    payload: PutioTokenUpdateRequest,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[PutioTokenRepository, fastapi.Depends(get_putio_token_repo)],
) -> SimpleStatus:
    """Set the Put.io OAuth bearer token.  Admin only.  Never returned back."""
    token = payload.token
    await anyio.to_thread.run_sync(lambda: repo.write(token))
    return SimpleStatus()


@router.get('/config/putio-token/status')
async def putio_token_status(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[PutioTokenRepository, fastapi.Depends(get_putio_token_repo)],
) -> dict[str, bool]:
    """Return whether a Put.io token is currently configured (true/false only)."""
    configured = await anyio.to_thread.run_sync(repo.exists_and_nonempty)
    return {'configured': configured}


@router.put('/config/telegram-bot-token', response_model=SimpleStatus)
async def put_telegram_bot_token(
    payload: TelegramBotTokenUpdateRequest,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[ConfigService, fastapi.Depends(get_config_service)],
) -> SimpleStatus:
    """Set the Telegram bot token.  Admin only.  Never returned back."""
    await service.set_telegram_bot_token(payload.bot_token)
    return SimpleStatus()


@router.get('/config/telegram-bot-token/status')
async def telegram_bot_token_status(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[ConfigService, fastapi.Depends(get_config_service)],
) -> dict[str, bool]:
    """Return whether a Telegram bot token is currently configured (true/false only)."""
    configured = await service.telegram_bot_token_configured()
    return {'configured': configured}


@router.put('/config/telegram-webhook-secret', response_model=SimpleStatus)
async def put_telegram_webhook_secret(
    payload: TelegramWebhookSecretUpdateRequest,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[ConfigService, fastapi.Depends(get_config_service)],
) -> SimpleStatus:
    """Set the Telegram webhook secret.  Admin only.  Never returned back."""
    await service.set_telegram_webhook_secret(payload.webhook_secret)
    return SimpleStatus()


@router.get('/config/telegram-webhook-secret/status')
async def telegram_webhook_secret_status(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[ConfigService, fastapi.Depends(get_config_service)],
) -> dict[str, bool]:
    """Return whether a Telegram webhook secret is currently configured (true/false only)."""
    configured = await service.telegram_webhook_secret_configured()
    return {'configured': configured}

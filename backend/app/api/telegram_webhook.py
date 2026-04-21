"""Telegram webhook receiver.

FastAPI router mounted at ``/api/webhooks/telegram``.

Verification order (each failure → 403 ``{"detail": "forbidden"}``):
1. IP allowlist — Telegram's published CIDR ranges only (or localhost when
   ``settings.telegram.allow_localhost`` is True).
2. Path secret — constant-time compare against ``settings.telegram.webhook_secret``.
3. Header secret — ``X-Telegram-Bot-Api-Secret-Token`` constant-time compare.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import typing as T

import fastapi
import pydantic

from ..models import AppSettings
from .deps import get_settings

_log = logging.getLogger(__name__)

# Telegram's published IP ranges (https://core.telegram.org/bots/webhooks#the-short-version)
_TELEGRAM_CIDRS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network('149.154.160.0/20'),
    ipaddress.ip_network('91.108.4.0/22'),
)

_LOCALHOST: ipaddress.IPv4Network | ipaddress.IPv6Network = ipaddress.ip_network('127.0.0.1/32')

# ---------------------------------------------------------------------------
# Telegram Update pydantic models
# ---------------------------------------------------------------------------


class _TelegramUser(pydantic.BaseModel):
    id: int
    is_bot: bool = False
    first_name: str | None = None
    username: str | None = None


class _TelegramChat(pydantic.BaseModel):
    id: int
    type: str


class _TelegramMessage(pydantic.BaseModel):
    message_id: int
    from_: _TelegramUser | None = pydantic.Field(None, alias='from')
    chat: _TelegramChat
    date: int
    text: str | None = None

    model_config = pydantic.ConfigDict(populate_by_name=True, extra='ignore')


class _TelegramCallbackQuery(pydantic.BaseModel):
    id: str
    from_: _TelegramUser = pydantic.Field(..., alias='from')
    data: str | None = None
    message: _TelegramMessage | None = None

    model_config = pydantic.ConfigDict(populate_by_name=True, extra='ignore')


class TelegramUpdate(pydantic.BaseModel):
    update_id: int
    message: _TelegramMessage | None = None
    callback_query: _TelegramCallbackQuery | None = None

    model_config = pydantic.ConfigDict(extra='ignore')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: fastapi.Request) -> str:
    """Return the real client IP, preferring X-Real-IP set by nginx."""
    xri = request.headers.get('X-Real-IP')
    if xri:
        return xri.strip()
    xff = request.headers.get('X-Forwarded-For')
    if xff:
        return xff.split(',')[-1].strip()
    return request.client.host if request.client else ''


def _ip_allowed(ip_str: str, *, allow_localhost: bool) -> bool:
    """Return True if ``ip_str`` is in Telegram's CIDRs (or localhost when allowed)."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if allow_localhost and addr in _LOCALHOST:
        return True
    return any(addr in net for net in _TELEGRAM_CIDRS)


def _update_type(update: TelegramUpdate) -> str:
    if update.message is not None:
        return 'message'
    if update.callback_query is not None:
        return 'callback_query'
    return 'unknown'


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = fastapi.APIRouter(prefix='/api/webhooks/telegram', tags=['telegram-webhook'])


@router.post('/{secret}')
async def receive(
    secret: str,
    update: TelegramUpdate,
    request: fastapi.Request,
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
) -> dict[str, bool]:
    """Receive a Telegram Update.

    Verifies IP allowlist, path secret, and header secret before processing.
    Returns ``{"ok": true}`` immediately — actual update handling is deferred
    to later PRs.
    """
    tg = settings.telegram

    # 1. IP allowlist
    client_ip = _client_ip(request)
    if not _ip_allowed(client_ip, allow_localhost=tg.allow_localhost):
        _log.warning('Telegram webhook: rejected request from disallowed IP %s', client_ip)
        raise fastapi.HTTPException(status_code=403, detail='forbidden')

    # 2. Path secret
    expected = tg.webhook_secret
    if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
        _log.warning('Telegram webhook: path secret mismatch from IP %s', client_ip)
        raise fastapi.HTTPException(status_code=403, detail='forbidden')

    # 3. Header secret
    header_secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not hmac.compare_digest(header_secret.encode(), expected.encode()):
        _log.warning('Telegram webhook: header secret mismatch from IP %s', client_ip)
        raise fastapi.HTTPException(status_code=403, detail='forbidden')

    # All checks passed — log update type and return OK.
    _log.info('Telegram webhook: received update_id=%d type=%s', update.update_id, _update_type(update))
    return {'ok': True}

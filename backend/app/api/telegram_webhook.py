"""Telegram webhook receiver.

FastAPI router mounted at ``/api/webhooks/telegram``.

Verification order (each failure → 403 ``{"detail": "forbidden"}``):
1. IP allowlist — Telegram's published CIDR ranges only (or localhost when
   ``settings.telegram.allow_localhost`` is True).
2. Path secret — constant-time compare against ``settings.telegram.webhook_secret``.
3. Header secret — ``X-Telegram-Bot-Api-Secret-Token`` constant-time compare.
"""

from __future__ import annotations

import datetime
import hmac
import ipaddress
import logging
import re
import typing as T

import anyio.to_thread
import fastapi
import pydantic

from ..models import AppSettings
from ..persistence.user_repo import UserRepository
from ..services._factory import container_bound
from ..services.telegram_client import (
    TelegramBotBlockedError,
    TelegramChatNotFoundError,
    TelegramClient,
    escape_markdown_v2,
)
from ..services.telegram_client_cache import resolve_telegram_client
from ..services.telegram_commands import TelegramCommandDispatcher
from ..services.telegram_rate_limiter import TelegramRateLimiter
from .deps import get_settings


def _get_telegram_client(
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
) -> TelegramClient | None:
    """Resolve a TelegramClient from the CURRENT settings.telegram.bot_token.

    Uses the module-level singleton cache so token rotations take effect
    without restarting the API process.
    """
    return resolve_telegram_client(settings.telegram.bot_token)


_get_user_repo: T.Callable[[], UserRepository] = container_bound(lambda c: c.user_repo)
_get_dispatcher: T.Callable[[], TelegramCommandDispatcher | None] = container_bound(
    lambda c: getattr(c, 'telegram_command_dispatcher', None)
)
_get_rate_limiter: T.Callable[[], TelegramRateLimiter | None] = container_bound(
    lambda c: getattr(c, 'telegram_rate_limiter', None)
)

_START_RE = re.compile(r'^/start (?P<token>[A-Za-z0-9_-]+)$')

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


def _token_is_expired(expires_at: datetime.datetime | None) -> bool:
    """Return True when *expires_at* is in the past or is None."""
    if expires_at is None:
        return True
    now = datetime.datetime.now(datetime.UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.UTC)
    return now >= expires_at


async def _handle_message(
    message: _TelegramMessage,
    user_repo: UserRepository,
    telegram_client: TelegramClient | None,
    dispatcher: TelegramCommandDispatcher | None = None,
    rate_limiter: TelegramRateLimiter | None = None,
) -> None:
    """Dispatch incoming message updates.

    Handles:
    - ``/start <token>`` — binding flow
    - ``/start`` (no args) — hint
    - bound user commands — dispatcher path
    - unbound user other messages — minimal hint
    """
    text = (message.text or '').strip()
    chat_id = message.chat.id

    async def _send(msg: str, *, parse_mode: str | None = None) -> None:
        if telegram_client is None:
            return
        try:
            await telegram_client.send_message(chat_id, msg, parse_mode=parse_mode)
        except TelegramBotBlockedError, TelegramChatNotFoundError:
            _log.warning(
                'Telegram webhook: could not send reply to chat_id=%d (blocked/not found)',
                chat_id,
            )
        except Exception:
            _log.exception('Telegram webhook: failed to send reply to chat_id=%d', chat_id)

    m = _START_RE.match(text)
    if m:
        token = m.group('token')
        user = await anyio.to_thread.run_sync(lambda: user_repo.find_by_telegram_link_token(token))
        if user is None or _token_is_expired(user.telegram_link_token_expires_at):
            await _send('⚠️ 綁定連結無效或已過期。請回到網站重新產生。')
            return
        uid = user.id
        await anyio.to_thread.run_sync(lambda: user_repo.finalize_telegram_binding(uid, chat_id))
        await _send('✅ 綁定成功！你會在這裡收到 SN 下載完成的通知。\n用 /help 看可用指令。')
        _log.info('Telegram webhook: user %s bound to chat_id=%d', uid, chat_id)
        return

    if text == '/start':
        await _send('👋 請回到網站點擊「綁定 Telegram」按鈕，再掃描或開啟連結。')
        return

    # --- Bound-user command dispatcher path ---
    if message.from_ is not None and dispatcher is not None:
        tg_user_id = message.from_.id
        bound_user = await anyio.to_thread.run_sync(lambda: user_repo.find_by_telegram_chat_id(tg_user_id))

        if bound_user is None:
            # Not bound — tell them to bind via web UI
            await _send(
                escape_markdown_v2('請先到網站的「設定」頁面綁定 Telegram，才能使用指令。'),
                parse_mode='MarkdownV2',
            )
            return

        # Rate limit check
        if rate_limiter is not None and not rate_limiter.allow(bound_user.id):
            retry_s = rate_limiter.retry_after_seconds(bound_user.id)
            await _send(
                escape_markdown_v2(f'🚦 請求太頻繁，請 {retry_s} 秒後再試。'),
                parse_mode='MarkdownV2',
            )
            return

        await dispatcher.dispatch(chat_id=chat_id, user=bound_user, text=text)
        return

    # All other messages — minimal hint
    await _send('/help 可查看可用指令（綁定完成後才可用）。')


@router.post('/{secret}')
async def receive(
    secret: str,
    update: TelegramUpdate,
    request: fastapi.Request,
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
    telegram_client: T.Annotated[TelegramClient | None, fastapi.Depends(_get_telegram_client)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(_get_user_repo)],
    dispatcher: T.Annotated[TelegramCommandDispatcher | None, fastapi.Depends(_get_dispatcher)],
    rate_limiter: T.Annotated[TelegramRateLimiter | None, fastapi.Depends(_get_rate_limiter)],
) -> dict[str, bool]:
    """Receive a Telegram Update.

    Verifies IP allowlist, path secret, and header secret before processing.
    Handles ``/start <token>`` binding flow and bound-user commands.
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

    # All checks passed — dispatch update.
    _log.info('Telegram webhook: received update_id=%d type=%s', update.update_id, _update_type(update))

    if update.message is not None:
        await _handle_message(
            update.message,
            user_repo,
            telegram_client,
            dispatcher=dispatcher,
            rate_limiter=rate_limiter,
        )

    if update.callback_query is not None:
        cq = update.callback_query
        if telegram_client is None:
            return {'ok': True}

        bound_user = await anyio.to_thread.run_sync(lambda: user_repo.find_by_telegram_chat_id(cq.from_.id))
        if bound_user is None:
            await telegram_client.answer_callback_query(cq.id, text='請先綁定帳號', show_alert=True)
            return {'ok': True}

        if rate_limiter is not None and not rate_limiter.allow(bound_user.id):
            await telegram_client.answer_callback_query(cq.id, text='請求太頻繁', show_alert=True)
            return {'ok': True}

        if dispatcher is not None:
            await dispatcher.handle_callback_query(user=bound_user, callback_query=cq)

    return {'ok': True}

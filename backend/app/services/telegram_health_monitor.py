"""Health-check tick — DM admins on disk-low / cookie-expired conditions.

Runs every 5 minutes via APScheduler.  Uses Redis for per-rule cooldown
(``alert_cooldown:{rule}`` key with TTL = 6h) so we don't spam admins
when a degraded condition persists.

Two rules:
1. ``disk_low``: ``shutil.disk_usage(bangumi_dir).free < 10 GiB``
2. ``cookie_expired``: >= 3 distinct sn with ``status='失敗'`` and ``retries>=3``
   in the active progress snapshot — heuristic for a stale Bahamut cookie.
"""

from __future__ import annotations

import datetime
import shutil
import typing as T

import dramatiq
import redis.asyncio

from .. import dramatiq_setup as _setup
from ..core import build_container

if T.TYPE_CHECKING:
    from ..core import Container

_setup.init_broker()

_DISK_LOW_THRESHOLD_BYTES = 10 * 1024**3
_COOKIE_FAILURE_THRESHOLD = 3
_ALERT_COOLDOWN_SECONDS = 6 * 60 * 60


@dramatiq.actor(queue_name='meta', max_retries=0, time_limit=60_000)
async def health_check_tick() -> None:
    """Periodic health probe — fires send_message_actor to admins when rules trip."""
    container = build_container()
    settings = container.settings_repo.load()
    tg_settings = settings.telegram
    if not tg_settings.enabled or not tg_settings.health_alerts:
        return
    if container.redis_client_async is None:
        return

    await _check_disk_low(container, settings, tg_settings)
    await _check_cookie_expired(container, tg_settings)


async def _check_disk_low(container: Container, settings: object, tg_settings: object) -> None:
    """Disk space rule with 6h cooldown."""
    redis_client = container.redis_client_async
    if redis_client is None:
        return
    if await _is_in_cooldown(redis_client, 'disk_low'):
        return
    bangumi_dir: str = getattr(settings, 'bangumi_dir', '')
    if not bangumi_dir:
        return
    try:
        usage = shutil.disk_usage(bangumi_dir)
    except OSError:
        return
    if usage.free >= _DISK_LOW_THRESHOLD_BYTES:
        return
    free_gib = usage.free / (1024**3)
    text = f'⚠️ *磁碟空間不足*\n\n`{_md_escape(bangumi_dir)}`\n剩餘: {free_gib:.1f} GiB \\(< 10 GiB\\)'
    await _broadcast_to_admins(container, tg_settings, text)
    await _set_cooldown(redis_client, 'disk_low')


async def _check_cookie_expired(container: Container, tg_settings: object) -> None:
    """Cookie expiry heuristic with 6h cooldown."""
    redis_client = container.redis_client_async
    if redis_client is None:
        return
    if await _is_in_cooldown(redis_client, 'cookie_expired'):
        return
    if container.redis_progress_reader is None:
        return
    snap = await container.redis_progress_reader.snapshot()
    failed_sns = {sn for sn, entry in snap.items() if entry.status == '失敗' and entry.retries >= 3}
    if len(failed_sns) < _COOKIE_FAILURE_THRESHOLD:
        return
    text = (
        '⚠️ *Cookie 可能過期*\n\n'
        f'過去檢查中有 {len(failed_sns)} 個 sn 連續失敗 \\(retries ≥ 3\\)，'
        '請確認 Bahamut cookie 是否需要更新。'
    )
    await _broadcast_to_admins(container, tg_settings, text)
    await _set_cooldown(redis_client, 'cookie_expired')


async def _broadcast_to_admins(container: Container, tg_settings: object, text: str) -> None:
    """Send via send_message_actor to every admin who is bound + opted-in + not muted."""
    from ..tasks.telegram import send_message_actor

    now = datetime.datetime.now(datetime.UTC)
    all_users = container.user_repo.list_all()
    admins = [
        u
        for u in all_users
        if u.role == 'admin'
        and u.telegram_chat_id is not None
        and u.telegram_notify_enabled
        and (u.telegram_mute_until is None or _ensure_aware(u.telegram_mute_until) <= now)
    ]
    bot_token: str = getattr(tg_settings, 'bot_token', '')
    for u in admins:
        send_message_actor.send_with_options(
            kwargs={
                'chat_id': u.telegram_chat_id,
                'text': text,
                'bot_token': bot_token,
            },
        )


def _ensure_aware(dt: datetime.datetime) -> datetime.datetime:
    """Return a tz-aware datetime; attach UTC if naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.UTC)
    return dt


async def _is_in_cooldown(client: redis.asyncio.Redis, rule: str) -> bool:
    result = await client.exists(f'alert_cooldown:{rule}')
    return bool(result)


async def _set_cooldown(client: redis.asyncio.Redis, rule: str) -> None:
    await client.setex(f'alert_cooldown:{rule}', _ALERT_COOLDOWN_SECONDS, '1')


def _md_escape(text: str) -> str:
    from .telegram_client import escape_markdown_v2

    return escape_markdown_v2(text)

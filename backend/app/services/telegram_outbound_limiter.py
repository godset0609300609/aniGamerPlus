"""Redis-backed token bucket gating outbound Telegram API calls (fix #22).

``send_message_actor`` / ``edit_message_actor`` / ``delete_message_actor``
(:mod:`app.tasks.telegram`) already retry reactively on a 429 from Telegram,
but nothing stopped a burst (e.g. an admin broadcast to every bound user, or
a flood of BT-downloader lifecycle events) from firing far more requests per
second than Telegram allows in the first place. :class:`TelegramOutboundRateLimiter`
is a proactive gate called *before* every outbound API call so the actors
stay under Telegram's documented limits:

* ~30 messages/second, bot-wide (``_GLOBAL_LIMIT`` / ``_GLOBAL_WINDOW_SECONDS``)
* ~20 messages/minute, per chat (``_CHAT_LIMIT`` / ``_CHAT_WINDOW_SECONDS``)

This is a *sibling* to :class:`~app.services.telegram_rate_limiter.TelegramRateLimiter`,
not an extension of it — that class is an in-memory sliding-window limiter
bounding *inbound* bot commands within a single process. Outbound sends are
fired from dramatiq worker processes (potentially several replicas), so the
counters here live in Redis (fixed-window INCR + EXPIRE) rather than
in-process memory, and the two classes share no state or code.

Fails open on any Redis error (connection down, etc.) — an outbound send
should never hang or crash because the throttle's backing store is
unavailable; dramatiq's existing 429 retry (``retry_when=_retry_when_429``
in :mod:`app.tasks.telegram`) remains the backstop.
"""

from __future__ import annotations

import asyncio
import collections.abc
import time
import typing as T

import redis.asyncio

from .. import dramatiq_setup


class TelegramOutboundRateLimiter:
    """Async token-bucket gate; call :meth:`acquire` before every send/edit/delete."""

    _GLOBAL_LIMIT = 30
    _GLOBAL_WINDOW_SECONDS = 1.0
    _CHAT_LIMIT = 20
    _CHAT_WINDOW_SECONDS = 60.0
    _POLL_INTERVAL_SECONDS = 0.05
    #: Give up waiting and let the call through rather than block a worker
    #: forever — Telegram's own 429 retry is the backstop if this races.
    _MAX_WAIT_SECONDS = 5.0

    def __init__(
        self,
        client: redis.asyncio.Redis,
        *,
        global_limit: int | None = None,
        global_window_seconds: float | None = None,
        chat_limit: int | None = None,
        chat_window_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
        max_wait_seconds: float | None = None,
        now_fn: collections.abc.Callable[[], float] = time.time,
        sleep_fn: collections.abc.Callable[[float], collections.abc.Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._global_limit = global_limit if global_limit is not None else self._GLOBAL_LIMIT
        self._global_window_seconds = (
            global_window_seconds if global_window_seconds is not None else self._GLOBAL_WINDOW_SECONDS
        )
        self._chat_limit = chat_limit if chat_limit is not None else self._CHAT_LIMIT
        self._chat_window_seconds = (
            chat_window_seconds if chat_window_seconds is not None else self._CHAT_WINDOW_SECONDS
        )
        self._poll_interval_seconds = (
            poll_interval_seconds if poll_interval_seconds is not None else self._POLL_INTERVAL_SECONDS
        )
        self._max_wait_seconds = max_wait_seconds if max_wait_seconds is not None else self._MAX_WAIT_SECONDS
        self._now_fn = now_fn
        self._sleep_fn = sleep_fn if sleep_fn is not None else asyncio.sleep

    async def acquire(self, chat_id: int) -> None:
        """Block until a slot is free in both the global and per-chat windows.

        Polls Redis every ``poll_interval_seconds`` up to ``max_wait_seconds``;
        past the deadline the call is let through anyway (see module docstring
        on why blocking forever is worse than an occasional 429). Any Redis
        error is treated the same way — fail open immediately.
        """
        deadline = self._now_fn() + self._max_wait_seconds
        while True:
            try:
                allowed = await self._try_reserve(chat_id)
            except Exception:  # noqa: BLE001 — Redis unreachable; fail open
                return
            if allowed:
                return
            if self._now_fn() >= deadline:
                return
            await self._sleep_fn(self._poll_interval_seconds)

    async def _try_reserve(self, chat_id: int) -> bool:
        now = self._now_fn()
        global_key = f'tgout:global:{int(now // self._global_window_seconds)}'
        chat_key = f'tgout:chat:{chat_id}:{int(now // self._chat_window_seconds)}'

        global_count = await self._incr_with_expiry(global_key, self._global_window_seconds)
        if global_count > self._global_limit:
            return False

        chat_count = await self._incr_with_expiry(chat_key, self._chat_window_seconds)
        return chat_count <= self._chat_limit

    async def _incr_with_expiry(self, key: str, ttl_seconds: float) -> int:
        count = T.cast('int', await self._client.incr(key))
        if count == 1:
            await self._client.expire(key, max(1, int(ttl_seconds) + 1))
        return count


_SINGLETON: TelegramOutboundRateLimiter | None = None


def get_telegram_outbound_limiter() -> TelegramOutboundRateLimiter:
    """Return the process-wide outbound limiter, building it on first call.

    Mirrors :mod:`app.services.telegram_client_cache`'s per-process
    singleton pattern. The backing ``redis.asyncio.Redis`` connection pool
    is created lazily (no eager connect), so this never raises even when
    Redis is unreachable — failures surface (and are swallowed) inside
    ``acquire()`` instead.
    """
    global _SINGLETON
    if _SINGLETON is None:
        client = redis.asyncio.Redis.from_url(dramatiq_setup.get_redis_url())
        _SINGLETON = TelegramOutboundRateLimiter(client)
    return _SINGLETON

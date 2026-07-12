"""Tests for :class:`TelegramOutboundRateLimiter` (fix #22).

Uses ``fakeredis.aioredis.FakeRedis`` for real INCR/EXPIRE semantics (same
pattern as ``test_redis_state.py``) plus an injectable fake clock + sleep
function so pacing is verified deterministically without any real waiting.
"""

from __future__ import annotations

import collections.abc

import fakeredis.aioredis
import pytest

from app.services.telegram_outbound_limiter import (
    TelegramOutboundRateLimiter,
    get_telegram_outbound_limiter,
)


class FakeClock:
    """Controllable monotonic-ish clock — ``time.time``-shaped (returns float seconds)."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _instant_sleep_factory(clock: FakeClock) -> collections.abc.Callable[[float], collections.abc.Awaitable[None]]:
    """A sleep_fn that advances the fake clock instead of really waiting."""

    async def _sleep(seconds: float) -> None:
        clock.advance(seconds)

    return _sleep


@pytest.fixture
def client() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis()


# ---------------------------------------------------------------------------
# Basic reservation behaviour
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_acquire_allows_calls_under_the_limit_without_waiting(
    anyio_backend: str, client: fakeredis.aioredis.FakeRedis
) -> None:
    clock = FakeClock()
    limiter = TelegramOutboundRateLimiter(
        client,
        global_limit=5,
        global_window_seconds=1.0,
        chat_limit=5,
        chat_window_seconds=60.0,
        now_fn=clock,
        sleep_fn=_instant_sleep_factory(clock),
    )

    start = clock.now
    for _ in range(5):
        await limiter.acquire(chat_id=1)

    assert clock.now == start  # no pacing needed — never had to sleep


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_acquire_paces_when_global_limit_hit(anyio_backend: str, client: fakeredis.aioredis.FakeRedis) -> None:
    """A call beyond the global cap waits (fake-sleeps) until the window rolls over."""
    clock = FakeClock()
    limiter = TelegramOutboundRateLimiter(
        client,
        global_limit=2,
        global_window_seconds=1.0,
        chat_limit=100,
        chat_window_seconds=60.0,
        poll_interval_seconds=0.1,
        max_wait_seconds=5.0,
        now_fn=clock,
        sleep_fn=_instant_sleep_factory(clock),
    )

    await limiter.acquire(chat_id=1)
    await limiter.acquire(chat_id=1)

    start = clock.now
    await limiter.acquire(chat_id=1)  # 3rd call exceeds the 2-per-window global cap

    assert clock.now > start  # pacing occurred
    assert clock.now - start >= 1.0  # had to wait for the window to roll over


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_acquire_gives_up_waiting_past_max_wait_seconds(
    anyio_backend: str, client: fakeredis.aioredis.FakeRedis
) -> None:
    """If the window never frees up within max_wait_seconds, acquire() returns anyway (fail open)."""
    clock = FakeClock()
    limiter = TelegramOutboundRateLimiter(
        client,
        global_limit=1,
        # A window far longer than max_wait_seconds — it will never roll
        # over during the test, so this exercises the give-up path.
        global_window_seconds=1000.0,
        chat_limit=100,
        chat_window_seconds=60.0,
        poll_interval_seconds=0.1,
        max_wait_seconds=0.5,
        now_fn=clock,
        sleep_fn=_instant_sleep_factory(clock),
    )

    await limiter.acquire(chat_id=1)  # consumes the single global slot

    start = clock.now
    await limiter.acquire(chat_id=1)  # never gets a slot — must give up at the deadline

    assert clock.now - start >= 0.5


# ---------------------------------------------------------------------------
# Per-chat isolation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_per_chat_limit_is_independent_of_other_chats(
    anyio_backend: str, client: fakeredis.aioredis.FakeRedis
) -> None:
    clock = FakeClock()
    limiter = TelegramOutboundRateLimiter(
        client,
        global_limit=100,
        global_window_seconds=1.0,
        chat_limit=1,
        chat_window_seconds=60.0,
        poll_interval_seconds=0.1,
        max_wait_seconds=5.0,
        now_fn=clock,
        sleep_fn=_instant_sleep_factory(clock),
    )

    await limiter.acquire(chat_id=1)  # chat 1's only slot this window

    start = clock.now
    await limiter.acquire(chat_id=2)  # different chat — must not wait on chat 1's cap
    assert clock.now == start


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_per_chat_limit_paces_repeat_sends_to_the_same_chat(
    anyio_backend: str, client: fakeredis.aioredis.FakeRedis
) -> None:
    clock = FakeClock()
    limiter = TelegramOutboundRateLimiter(
        client,
        global_limit=100,
        global_window_seconds=1.0,
        chat_limit=1,
        chat_window_seconds=2.0,
        poll_interval_seconds=0.1,
        max_wait_seconds=5.0,
        now_fn=clock,
        sleep_fn=_instant_sleep_factory(clock),
    )

    await limiter.acquire(chat_id=1)

    start = clock.now
    await limiter.acquire(chat_id=1)  # same chat, same window — must wait
    assert clock.now - start >= 2.0


# ---------------------------------------------------------------------------
# Fail-open on Redis errors
# ---------------------------------------------------------------------------


class _BrokenRedis:
    """Stand-in whose ``incr`` always raises — simulates Redis being down."""

    async def incr(self, key: str) -> int:  # noqa: ARG002
        raise ConnectionError('redis unreachable')

    async def expire(self, key: str, ttl: int) -> None:  # noqa: ARG002
        raise ConnectionError('redis unreachable')


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_acquire_fails_open_when_redis_unreachable(anyio_backend: str) -> None:
    clock = FakeClock()
    limiter = TelegramOutboundRateLimiter(
        _BrokenRedis(),  # type: ignore[arg-type]
        now_fn=clock,
        sleep_fn=_instant_sleep_factory(clock),
    )

    start = clock.now
    await limiter.acquire(chat_id=1)  # must not raise, must not wait

    assert clock.now == start


# ---------------------------------------------------------------------------
# get_telegram_outbound_limiter — process-wide singleton
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton() -> collections.abc.Iterator[None]:
    import app.services.telegram_outbound_limiter as mod

    original = mod._SINGLETON
    mod._SINGLETON = None
    yield
    mod._SINGLETON = original


def test_get_telegram_outbound_limiter_returns_cached_singleton() -> None:
    first = get_telegram_outbound_limiter()
    second = get_telegram_outbound_limiter()
    assert first is second
    assert isinstance(first, TelegramOutboundRateLimiter)

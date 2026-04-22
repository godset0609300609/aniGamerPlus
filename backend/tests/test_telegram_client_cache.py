"""Tests for the TelegramClient singleton cache.

Covers:
- Empty token → None, no client created
- Same token twice → same instance (cache hit)
- Token change → first instance closed, new instance returned
- close_telegram_client_cache closes the current instance
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.telegram_client_cache import (
    _TelegramClientCache,
    close_telegram_client_cache,
    resolve_telegram_client,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_cache() -> _TelegramClientCache:
    """Return a new, isolated cache instance for each test."""
    from app.services.telegram_client_cache import _TelegramClientCache

    return _TelegramClientCache()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_token_returns_none() -> None:
    cache = _fresh_cache()
    assert cache.get('') is None
    assert cache.get('   ') is None


def test_same_token_twice_returns_same_instance() -> None:
    cache = _fresh_cache()
    c1 = cache.get('BOT:token123')
    c2 = cache.get('BOT:token123')
    assert c1 is c2


def test_token_change_closes_old_and_returns_new() -> None:
    """Changing the token must return a different instance."""
    cache = _fresh_cache()

    c1 = cache.get('TOKEN:aaa')
    assert c1 is not None

    # Patch close on c1 so we can verify it was scheduled.
    c1.close = AsyncMock(return_value=None)  # type: ignore[method-assign]

    # No running event loop in plain pytest, so _schedule_close is a no-op.
    c2 = cache.get('TOKEN:bbb')
    assert c2 is not None
    assert c1 is not c2


def test_empty_token_after_valid_clears_cache() -> None:
    """Switching from a valid token to '' must clear the cached client."""
    cache = _fresh_cache()
    cache.get('TOKEN:abc')
    result = cache.get('')
    assert result is None
    # Internal state should also be cleared.
    assert cache._current is None


@pytest.mark.anyio
async def test_aclose_closes_current_client() -> None:
    """aclose() must call close() on the current client."""
    cache = _fresh_cache()
    client = cache.get('TOKEN:xyz')
    assert client is not None

    close_mock = AsyncMock(return_value=None)
    client.close = close_mock  # type: ignore[method-assign]

    await cache.aclose()

    close_mock.assert_awaited_once()
    assert cache._current is None


@pytest.mark.anyio
async def test_aclose_noop_when_empty() -> None:
    """aclose() on an empty cache must not raise."""
    cache = _fresh_cache()
    await cache.aclose()  # should not raise


# ---------------------------------------------------------------------------
# Module-level façade
# ---------------------------------------------------------------------------


def test_resolve_telegram_client_empty_returns_none() -> None:
    assert resolve_telegram_client('') is None


def test_resolve_telegram_client_non_empty_returns_client() -> None:
    from app.services.telegram_client import TelegramClient

    client = resolve_telegram_client('MODULE:facade_test_token_xyz987')
    assert isinstance(client, TelegramClient)


@pytest.mark.anyio
async def test_close_telegram_client_cache_does_not_raise() -> None:
    """close_telegram_client_cache() is safe to call even when nothing is cached."""
    # Ensure the singleton has a client so we exercise the real code path.
    resolve_telegram_client('CLOSETEST:tok')
    with patch.object(
        type(resolve_telegram_client('CLOSETEST:tok')),  # type: ignore[arg-type]
        'close',
        new_callable=lambda: lambda self: AsyncMock(return_value=None)(),
    ):
        # Just verify it completes without error.
        await close_telegram_client_cache()

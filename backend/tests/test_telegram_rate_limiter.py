"""Tests for TelegramRateLimiter sliding-window behaviour."""

from __future__ import annotations

from unittest.mock import patch

from app.services.telegram_rate_limiter import TelegramRateLimiter


def test_allow_up_to_max() -> None:
    """All N requests within the window are allowed."""
    rl = TelegramRateLimiter(max_per_minute=3)
    assert rl.allow('u1') is True
    assert rl.allow('u1') is True
    assert rl.allow('u1') is True


def test_deny_after_max() -> None:
    """The (N+1)th request within the window is denied."""
    rl = TelegramRateLimiter(max_per_minute=3)
    for _ in range(3):
        rl.allow('u1')
    assert rl.allow('u1') is False


def test_allow_resumes_after_window_slides() -> None:
    """After the oldest timestamp exits the 60-second window, a new slot opens."""
    rl = TelegramRateLimiter(max_per_minute=2)
    base = 1000.0

    with patch('time.monotonic', return_value=base):
        rl.allow('u1')  # t=1000

    with patch('time.monotonic', return_value=base + 1):
        rl.allow('u1')  # t=1001 — window full

    with patch('time.monotonic', return_value=base + 1):
        assert rl.allow('u1') is False  # still full

    # Advance 60 seconds past the first request
    with patch('time.monotonic', return_value=base + 61):
        assert rl.allow('u1') is True  # first slot has expired, allowed


def test_different_users_do_not_share_windows() -> None:
    """Rate limit is per-user — one user's exhaustion does not affect others."""
    rl = TelegramRateLimiter(max_per_minute=1)
    rl.allow('alice')
    assert rl.allow('alice') is False
    assert rl.allow('bob') is True  # bob is unaffected


def test_retry_after_seconds_returns_zero_when_under_limit() -> None:
    """retry_after_seconds returns 0 when no request has been made."""
    rl = TelegramRateLimiter(max_per_minute=5)
    assert rl.retry_after_seconds('u1') == 0


def test_retry_after_seconds_estimates_correctly() -> None:
    """retry_after_seconds returns a positive estimate when at the limit."""
    rl = TelegramRateLimiter(max_per_minute=1)
    base = 5000.0

    with patch('time.monotonic', return_value=base):
        rl.allow('u1')

    # 10 seconds later, the slot expires in ~50 more seconds
    with patch('time.monotonic', return_value=base + 10):
        eta = rl.retry_after_seconds('u1')

    # Should be approximately 51 seconds (60 - 10 + 1 for ceiling)
    assert 40 <= eta <= 60, f'Expected ~51 seconds, got {eta}'


def test_retry_after_zero_after_window_expires() -> None:
    """retry_after_seconds returns 0 once the window has fully cleared."""
    rl = TelegramRateLimiter(max_per_minute=1)
    base = 1000.0

    with patch('time.monotonic', return_value=base):
        rl.allow('u1')

    with patch('time.monotonic', return_value=base + 61):
        assert rl.retry_after_seconds('u1') == 0


def test_thread_safety() -> None:
    """Concurrent calls from many threads don't raise or corrupt state."""
    import threading

    rl = TelegramRateLimiter(max_per_minute=100)
    errors: list[Exception] = []

    def _worker(uid: str) -> None:
        try:
            for _ in range(20):
                rl.allow(uid)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(f'user-{i}',)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []

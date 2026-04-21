"""In-memory sliding-window rate limiter for Telegram commands."""

from __future__ import annotations

import collections
import threading
import time


class TelegramRateLimiter:
    """Sliding-window per-user rate limiter.

    Thread-safe (``_lock``). Per-user max N requests per 60 seconds,
    configurable via ``TelegramSettings.rate_limit_per_minute``.
    """

    _WINDOW = 60.0  # seconds

    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._windows: dict[str, collections.deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: str) -> bool:
        """Record a request and return True if under the limit."""
        now = time.monotonic()
        cutoff = now - self._WINDOW
        with self._lock:
            dq = self._windows.setdefault(user_id, collections.deque())
            # Evict timestamps older than the window.
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                return False
            dq.append(now)
            return True

    def retry_after_seconds(self, user_id: str) -> int:
        """Estimate seconds until the next slot opens. 0 = immediate."""
        now = time.monotonic()
        cutoff = now - self._WINDOW
        with self._lock:
            dq = self._windows.get(user_id)
            if not dq:
                return 0
            # Find oldest timestamp still inside window.
            oldest = None
            for ts in dq:
                if ts > cutoff:
                    oldest = ts
                    break
            if oldest is None:
                return 0
            wait = (oldest + self._WINDOW) - now
            return max(0, int(wait) + 1)

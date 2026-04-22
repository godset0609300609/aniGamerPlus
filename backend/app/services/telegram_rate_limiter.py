"""In-memory sliding-window rate limiter for Telegram commands."""

from __future__ import annotations

import collections
import threading
import time
import typing as T


class TelegramRateLimiter:
    """Sliding-window per-user rate limiter.

    Thread-safe (``_lock``). Per-user max N requests per 60 seconds.
    ``max_provider`` is called on every ``allow()`` / ``retry_after_seconds()``
    so that config changes (e.g. admin edits ``rate_limit_per_minute``) take
    effect immediately without restarting the process.
    """

    _WINDOW = 60.0  # seconds

    def __init__(self, max_provider: T.Callable[[], int]) -> None:
        self._max_provider = max_provider
        self._windows: dict[str, collections.deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: str) -> bool:
        """Record a request and return True if under the limit."""
        max_per_minute = max(1, self._max_provider())  # defensive: clamp to ≥1
        now = time.monotonic()
        cutoff = now - self._WINDOW
        with self._lock:
            dq = self._windows.setdefault(user_id, collections.deque())
            # Evict timestamps older than the window.
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= max_per_minute:
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

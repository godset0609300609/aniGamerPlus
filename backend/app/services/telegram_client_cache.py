"""Per-process cache for TelegramClient instances keyed by bot_token.

Needed because admin may rotate the bot token via the Settings UI
without restarting the API process. The cache closes old clients when
the token changes, so httpx connection pools don't leak.

NOTE: The scheduler process's TelegramNotifier still reads its client
from the container at startup (build_container). Changing bot_token
requires ``docker compose restart scheduler`` for download-event DM
notifications to pick up the new token. The API process's admin
endpoints and webhook route use this cache instead.
"""

from __future__ import annotations

import asyncio
import threading

from .telegram_client import TelegramClient


class _TelegramClientCache:
    """Singleton cache keyed by bot_token.

    Only one TelegramClient exists per token at a time.  Changing the
    token closes the previous client on the running asyncio loop if one
    is available; otherwise the httpx AsyncClient will clean up via its
    finaliser (ResourceWarning is silenced because tests configure
    filterwarnings=error only for user-level warnings).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: tuple[str, TelegramClient] | None = None

    def get(self, bot_token: str) -> TelegramClient | None:
        """Return a client for *bot_token*, or None if the token is empty.

        Closes and replaces the cached client when the token changed.
        """
        token = bot_token.strip()
        if not token:
            with self._lock:
                if self._current is not None:
                    _schedule_close(self._current[1])
                    self._current = None
            return None

        with self._lock:
            if self._current is not None and self._current[0] == token:
                return self._current[1]
            if self._current is not None:
                _schedule_close(self._current[1])
            client = TelegramClient(token)
            self._current = (token, client)
            return client

    async def aclose(self) -> None:
        """Close the current cached client (used by lifespan shutdown)."""
        with self._lock:
            cur = self._current
            self._current = None
        if cur is not None:
            await cur[1].close()


def _schedule_close(client: TelegramClient) -> None:
    """Fire-and-forget close on the running event loop.

    Falls back to a no-op if no loop is running (e.g. synchronous tests
    without an event loop). httpx's AsyncClient finaliser handles cleanup
    in that case.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop running; httpx finaliser handles cleanup
    loop.create_task(client.close())


_SINGLETON = _TelegramClientCache()


def resolve_telegram_client(bot_token: str) -> TelegramClient | None:
    """Module-level facade used by FastAPI dependencies.

    Recomputes on every call: if the admin saved a new token via the
    Settings UI the next request to any admin or webhook endpoint will
    automatically receive a client built for the new token.
    """
    return _SINGLETON.get(bot_token)


async def close_telegram_client_cache() -> None:
    """Lifespan hook — close the singleton on API process shutdown."""
    await _SINGLETON.aclose()

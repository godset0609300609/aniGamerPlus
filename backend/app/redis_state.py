"""Cross-process Redis state used by the API and async download actor.

``MessageIdRegistry`` is async-first so that the download actor (which runs
in an asyncio event loop via the AsyncIO middleware) can call ``set()`` on
entry without bridging to a sync client.  The API cancel path awaits ``get``
and ``clear`` in the same way.
"""

from __future__ import annotations

import redis.asyncio


class MessageIdRegistry:
    """Async-first registry — used both by api (cancel_task) and the
    dramatiq async download actor (which awaits set() on entry).
    """

    _PREFIX = 'msgid:'
    _TTL_SECONDS = 60 * 60  # 1 hour — long enough for the slowest download

    def __init__(self, client: redis.asyncio.Redis) -> None:
        self._client = client

    async def set(self, sn: int, message_id: str) -> None:
        await self._client.setex(f'{self._PREFIX}{int(sn)}', self._TTL_SECONDS, message_id)

    async def get(self, sn: int) -> str | None:
        raw = await self._client.get(f'{self._PREFIX}{int(sn)}')
        if raw is None:
            return None
        return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw)

    async def clear(self, sn: int) -> None:
        await self._client.delete(f'{self._PREFIX}{int(sn)}')

"""Per-user menu message-id store — used to delete the previous /menu
message when a user re-opens the menu, avoiding history clutter."""

from __future__ import annotations

import typing as T

if T.TYPE_CHECKING:
    import redis.asyncio


_PREFIX = 'menu_msg:'
_TTL_SECONDS = 7 * 24 * 60 * 60  # menu lasts a week — ample


class LiveMenuRegistry:
    def __init__(self, client: redis.asyncio.Redis) -> None:
        self._client = client

    async def set(self, user_id: str, message_id: int) -> None:
        await self._client.setex(f'{_PREFIX}{user_id}', _TTL_SECONDS, str(message_id))

    async def get(self, user_id: str) -> int | None:
        raw = await self._client.get(f'{_PREFIX}{user_id}')
        if raw is None:
            return None
        try:
            return int(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
        except ValueError:
            return None

    async def clear(self, user_id: str) -> None:
        await self._client.delete(f'{_PREFIX}{user_id}')

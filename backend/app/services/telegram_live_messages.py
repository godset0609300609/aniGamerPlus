"""Per-(sn, chat_id) live progress message tracking.

When ``TelegramNotifier`` sends a 'started' DM, it stores the returned
``message_id`` here so subsequent progress edits + the terminal
'completed/failed/cancelled' edit can hit the same message instead of
spamming a new bubble per event.

The state is per-recipient because admin_broadcast may DM multiple
admins for the same sn — each chat needs its own message_id.

Stored in Redis hash ``tgmsg:{sn}:{chat_id}`` with TTL = 1 day so a
crashed scheduler doesn't leak rows forever.
"""

from __future__ import annotations

import json
import typing as T
from collections.abc import Awaitable

if T.TYPE_CHECKING:
    import redis.asyncio


_PREFIX = 'tgmsg:'
_TTL_SECONDS = 24 * 60 * 60


class LiveMessageRegistry:
    def __init__(self, client: redis.asyncio.Redis) -> None:
        self._client = client

    async def set(
        self,
        sn: int,
        chat_id: int,
        *,
        message_id: int,
        last_edit_at: float,
        last_rate: float,
    ) -> None:
        key = f'{_PREFIX}{int(sn)}:{int(chat_id)}'
        await T.cast(Awaitable[int], self._client.hset(
            key,
            mapping={
                'message_id': str(message_id),
                'last_edit_at': json.dumps(last_edit_at),
                'last_rate': json.dumps(last_rate),
            },
        ))
        await T.cast(Awaitable[bool], self._client.expire(key, _TTL_SECONDS))

    async def get(self, sn: int, chat_id: int) -> tuple[int, float, float] | None:
        key = f'{_PREFIX}{int(sn)}:{int(chat_id)}'
        raw = await T.cast(Awaitable[dict[bytes, bytes]], self._client.hgetall(key))
        if not raw:
            return None
        data = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }
        try:
            message_id = int(data['message_id'])
            last_edit_at = float(json.loads(data.get('last_edit_at', '0')))
            last_rate = float(json.loads(data.get('last_rate', '0')))
            return (message_id, last_edit_at, last_rate)
        except Exception:  # noqa: BLE001
            return None

    async def clear(self, sn: int, chat_id: int) -> None:
        await self._client.delete(f'{_PREFIX}{int(sn)}:{int(chat_id)}')

    async def list_for_sn(self, sn: int) -> list[tuple[int, int, float, float]]:
        """Return [(chat_id, message_id, last_edit_at, last_rate), ...] for a given sn."""
        result: list[tuple[int, int, float, float]] = []
        async for raw_key in self._client.scan_iter(match=f'{_PREFIX}{int(sn)}:*'):
            key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
            chat_id_str = key.rsplit(':', 1)[1]
            try:
                chat_id = int(chat_id_str)
            except ValueError:
                continue
            data = await self.get(sn, chat_id)
            if data is None:
                continue
            message_id, last_edit_at, last_rate = data
            result.append((chat_id, message_id, last_edit_at, last_rate))
        return result

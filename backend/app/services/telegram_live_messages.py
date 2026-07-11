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

# BT downloader live-status message tracking — separate prefix from the
# per-download _PREFIX above since BT entries are keyed by entry_id, not sn,
# and per-(entry_id, chat_id) rather than per-(sn, chat_id).
_BT_PREFIX = 'btmsg:'


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
        await T.cast(
            Awaitable[int],
            self._client.hset(
                key,
                mapping={
                    'message_id': str(message_id),
                    'last_edit_at': json.dumps(last_edit_at),
                    'last_rate': json.dumps(last_rate),
                },
            ),
        )
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


class BtLiveMessageRegistry:
    """Per-(entry_id, chat_id) live BT status message tracking.

    Mirrors :class:`LiveMessageRegistry`'s role — ``TelegramNotifier`` uses
    this to edit a single Telegram message in place across a BT feed
    entry's Put.io -> landing lifecycle (dispatch -> queue -> download ->
    land/fail) instead of sending a burst of separate DMs.

    Stored as a Redis hash ``btmsg:{entry_id}:{chat_id}`` (``message_id`` +
    ``last_edit_at``), like :class:`LiveMessageRegistry`, with the same TTL
    so a crashed scheduler doesn't leak rows forever; a missed edit self-heals
    on the next status change by sending a fresh message and re-registering
    (see ``TelegramNotifier._handle_bt_intermediate``).

    ``last_edit_at`` is tracked here for observability / API symmetry with
    :class:`LiveMessageRegistry` — the actual landing-progress edit
    throttling decision lives in ``LandingWorker`` (a local closure var
    scoped to one file's download), not in this registry.

    Migration note: earlier versions stored a plain string via ``SETEX``
    instead of a hash. ``get``/``get_with_timestamp`` treat a pre-existing
    plain-string key as a cache miss (falls through to a fresh send) rather
    than raising, and ``set`` self-heals by deleting + recreating the key as
    a hash. No explicit migration is needed — stale plain-string keys expire
    within the TTL window regardless.
    """

    def __init__(self, client: redis.asyncio.Redis) -> None:
        self._client = client

    async def set(
        self,
        entry_id: int,
        chat_id: int,
        *,
        message_id: int,
        last_edit_at: float | None = None,
    ) -> None:
        key = f'{_BT_PREFIX}{int(entry_id)}:{int(chat_id)}'
        mapping = {
            'message_id': str(message_id),
            'last_edit_at': json.dumps(last_edit_at if last_edit_at is not None else 0.0),
        }
        try:
            await T.cast(Awaitable[int], self._client.hset(key, mapping=mapping))
        except Exception:  # noqa: BLE001 — stale pre-hash string key (WRONGTYPE); clear + retry once
            await self._client.delete(key)
            await T.cast(Awaitable[int], self._client.hset(key, mapping=mapping))
        await T.cast(Awaitable[bool], self._client.expire(key, _TTL_SECONDS))

    async def get(self, entry_id: int, chat_id: int) -> int | None:
        """Return just the ``message_id`` — for callers that don't need the timestamp."""
        result = await self.get_with_timestamp(entry_id, chat_id)
        return None if result is None else result[0]

    async def get_with_timestamp(self, entry_id: int, chat_id: int) -> tuple[int, float] | None:
        key = f'{_BT_PREFIX}{int(entry_id)}:{int(chat_id)}'
        try:
            raw = await T.cast(Awaitable[dict[bytes, bytes]], self._client.hgetall(key))
        except Exception:  # noqa: BLE001 — stale pre-hash string key (WRONGTYPE) — treat as a miss
            return None
        if not raw:
            return None
        data = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }
        try:
            message_id = int(data['message_id'])
        except (KeyError, ValueError):
            return None
        try:
            last_edit_at = float(json.loads(data.get('last_edit_at', '0')))
        except Exception:  # noqa: BLE001
            last_edit_at = 0.0
        return (message_id, last_edit_at)

    async def clear(self, entry_id: int, chat_id: int) -> None:
        await self._client.delete(f'{_BT_PREFIX}{int(entry_id)}:{int(chat_id)}')

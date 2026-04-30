"""Read side of the progress mirror — used by the API process.

Returns the same shape as :meth:`ProgressBus.snapshot` (``dict[int, TaskProgress]``)
so :class:`ProgressService` can consume either source transparently.

Uses the async redis client so the API event loop is not blocked during
SCAN / HGETALL operations that touch many keys.
"""

from __future__ import annotations

import datetime
import json
import typing as T
from collections.abc import Awaitable

import redis.asyncio

from ..downloader.progress import TaskProgress
from ..downloader.redis_progress_mirror import _ACTIVE_ZSET, _HASH_PREFIX  # noqa: F401


def _parse_dt(raw: str) -> datetime.datetime | None:
    if not raw:
        return None
    return datetime.datetime.fromisoformat(raw)


def _parse_float(raw: str) -> float:
    if not raw:
        return 0.0
    return float(json.loads(raw))


def _parse_optfloat(raw: str) -> float | None:
    if not raw:
        return None
    return float(json.loads(raw))


def _parse_optint(raw: str) -> int | None:
    if not raw:
        return None
    return int(json.loads(raw))


def _parse_optstr(raw: str) -> str | None:
    return raw if raw else None


class RedisProgressReader:
    """API-side async reader.  ``snapshot()`` returns the same shape as ProgressBus."""

    def __init__(self, client: redis.asyncio.Redis) -> None:
        self._client = client

    async def snapshot(self) -> dict[int, TaskProgress]:
        # Use async SCAN to avoid blocking on KEYS.
        result: dict[int, TaskProgress] = {}
        async for raw_key in self._client.scan_iter(match=f'{_HASH_PREFIX}*'):
            key = raw_key.decode('utf-8') if isinstance(raw_key, bytes) else str(raw_key)
            sn_str = key[len(_HASH_PREFIX) :]
            try:
                sn = int(sn_str)
            except ValueError:
                continue
            raw = await T.cast(Awaitable[dict[bytes, bytes]], self._client.hgetall(key))
            if not raw:
                continue
            data = {
                (k.decode('utf-8') if isinstance(k, bytes) else k): (v.decode('utf-8') if isinstance(v, bytes) else v)
                for k, v in raw.items()
            }
            try:
                entry = TaskProgress(
                    sn=sn,
                    rate=_parse_float(data.get('rate', '0')),
                    status=data.get('status', ''),
                    filename=data.get('filename', ''),
                    bangumi_name=_parse_optstr(data.get('bangumi_name', '')),
                    episode=_parse_optstr(data.get('episode', '')),
                    resolution=_parse_optstr(data.get('resolution', '')),
                    speed_mbps=_parse_optfloat(data.get('speed_mbps', '')),
                    eta_seconds=_parse_optint(data.get('eta_seconds', '')),
                    retries=int(data.get('retries', '0') or 0),
                    started_at=_parse_dt(data.get('started_at', '')),
                    finished_at=_parse_dt(data.get('finished_at', '')),
                    cooldown_until=_parse_dt(data.get('cooldown_until', '')),
                    owner_id=_parse_optstr(data.get('owner_id', '')),
                )
            except Exception:  # noqa: BLE001 — corrupted entry, skip
                continue
            result[sn] = entry
        return result

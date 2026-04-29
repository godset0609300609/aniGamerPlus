"""Write-through bridge from ProgressBus (worker process) to Redis.

ProgressBus.publish() / publish_finish() call into this mirror which
serialises a ``TaskProgress`` snapshot to a Redis hash keyed by sn and
adds the sn to a ``progress:active`` sorted set (score = updated_at unix
seconds) so the API reader can list active tasks cheaply.

Finished entries are kept in Redis for ``_FINISHED_TTL_SECONDS`` so the
frontend's "recently completed" column survives a brief disconnect, then
expire automatically.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import time
import typing as T

if T.TYPE_CHECKING:
    import redis  # type: ignore[import-not-found]

    from .progress import TaskProgress

_FINISHED_TTL_SECONDS = 7 * 86400
_HASH_PREFIX = 'progress:'
_ACTIVE_ZSET = 'progress:active'


def _serialise(entry: TaskProgress) -> dict[str, str]:
    """Convert a TaskProgress to a flat str→str dict for HSET.

    datetime fields → ISO 8601; None → empty string (Redis hashes have no
    null distinction, the reader treats empty strings as None).
    """
    raw = dataclasses.asdict(entry)
    raw.pop('_cancel_event', None)
    out: dict[str, str] = {}
    for k, v in raw.items():
        if v is None:
            out[k] = ''
        elif isinstance(v, datetime.datetime):
            out[k] = v.isoformat()
        elif isinstance(v, (int, float)):
            out[k] = json.dumps(v)
        else:
            out[k] = str(v)
    return out


class RedisProgressMirror:
    """Write-through mirror — installed on ProgressBus in production."""

    def __init__(self, client: 'redis.Redis[bytes]') -> None:
        self._client = client

    def publish(self, sn: int, entry: 'TaskProgress') -> None:
        key = f'{_HASH_PREFIX}{int(sn)}'
        pipe = self._client.pipeline()
        pipe.hset(key, mapping=_serialise(entry))
        # Active = no finished_at (or empty).  The ZSET is the source of
        # truth for "what to enumerate"; the hash holds the data.
        if entry.finished_at is None:
            pipe.zadd(_ACTIVE_ZSET, {str(int(sn)): time.time()})
        else:
            pipe.zrem(_ACTIVE_ZSET, str(int(sn)))
            pipe.expire(key, _FINISHED_TTL_SECONDS)
        pipe.execute()

    def publish_finish(self, sn: int, entry: 'TaskProgress') -> None:
        # Same logic — entry is now finished, so zrem + expire.
        self.publish(sn, entry)

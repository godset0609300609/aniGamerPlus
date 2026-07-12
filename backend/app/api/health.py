"""Health / liveness endpoint.

After the dramatiq + Redis migration the API no longer talks to the scheduler
container directly — manual tasks go through the Redis broker, progress is
read from Redis hashes.  So the legacy ``SchedulerProxy.is_scheduler_up()``
WebSocket-freshness probe (which targeted a route that no longer exists)
always returned ``False`` and made the UI flash a permanent
"排程服務暫時無回應" banner.

The new health probe pings Redis instead — that is the actual critical
dependency.  When Redis is reachable, downloads enqueue and the worker
processes them; when it isn't, the system is genuinely degraded.
"""

from __future__ import annotations

import asyncio
import datetime
import functools
import typing as T

import fastapi

from ..models import Health

if T.TYPE_CHECKING:
    import redis.asyncio

    from ..persistence.paths import WorkspacePaths

router = fastapi.APIRouter(tags=['health'])


class HealthService:
    """Tiny service returning a :class:`Health` snapshot."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    async def snapshot(self) -> Health:
        # The legacy ``aniGamerPlus_version`` key lived in ``config.json``;
        # the new :class:`AppSettings` schema drops it.
        #
        # ``working_dir`` used to be surfaced here so ops could confirm the
        # service was reading the expected directory, but ``/api/health`` is
        # unauthenticated — leaking the on-disk install path is a needless
        # recon aid, so it's intentionally no longer included.
        return Health(status='ok', version=None)


@functools.lru_cache(maxsize=1)
def _default_health_service() -> HealthService:
    from ..core import build_container

    return HealthService(build_container().paths)


def get_health_service() -> HealthService:
    return _default_health_service()


def _get_redis_client() -> redis.asyncio.Redis | None:
    """Resolve the async redis client from the container.

    Returns ``None`` when Redis isn't wired (single-process / CLI mode), so
    health probes degrade gracefully without crashing.
    """
    from ..core import build_container

    return build_container().redis_client_async


@router.get('/health')
async def health(
    request: fastapi.Request,
    service: T.Annotated[HealthService, fastapi.Depends(get_health_service)],
) -> dict[str, object]:
    """Aggregate health endpoint.

    Pings Redis (the broker + ProgressBus mirror) — that is the only
    dependency the API needs to function in the dramatiq architecture.
    """
    base = await service.snapshot()

    redis_status = 'offline'
    redis_client = _get_redis_client()
    if redis_client is not None:
        try:
            # redis-py async client typestub returns Awaitable[bool] | bool;
            # cast to the awaitable form so asyncio.wait_for accepts it.
            ping_coro = T.cast(T.Awaitable[bool], redis_client.ping())
            await asyncio.wait_for(ping_coro, timeout=2.0)
            redis_status = 'ok'
        except Exception:  # noqa: BLE001
            redis_status = 'offline'

    overall = 'ok' if (base.status == 'ok' and redis_status == 'ok') else 'degraded'

    return {
        'status': overall,
        'api': {'status': base.status, 'version': '2.0.0'},
        'redis': {'status': redis_status},
        # Kept for backward-compat with frontend banner logic that used to read
        # ``scheduler.status`` — surface redis status under both keys so old
        # clients don't false-alarm.
        'scheduler': {'status': redis_status},
        'checked_at': datetime.datetime.now(datetime.UTC).isoformat(),
    }

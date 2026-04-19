"""Health / liveness endpoint."""

from __future__ import annotations

import asyncio
import datetime
import functools
import typing as T

import fastapi

from ..models import Health

if T.TYPE_CHECKING:
    from ..persistence.paths import WorkspacePaths
    from ._scheduler_proxy import SchedulerProxy

router = fastapi.APIRouter(tags=['health'])


class HealthService:
    """Tiny service returning a :class:`Health` snapshot."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    def snapshot(self) -> Health:
        # The legacy ``aniGamerPlus_version`` key lived in ``config.json``;
        # the new :class:`AppSettings` schema drops it. We still surface the
        # workspace root via ``working_dir`` so ops can confirm the service
        # is reading the expected directory.
        return Health(
            status='ok',
            version=None,
            working_dir=str(self._paths.working_dir),
        )


@functools.lru_cache(maxsize=1)
def _default_health_service() -> HealthService:
    from ..core import build_container

    return HealthService(build_container().paths)


def get_health_service() -> HealthService:
    return _default_health_service()


@router.get('/health')
async def health(
    request: fastapi.Request,
    service: T.Annotated[HealthService, fastapi.Depends(get_health_service)],
) -> dict[str, object]:
    """Aggregate health endpoint.

    Returns the overall service health including the scheduler subprocess
    status fetched from ``/internal/health`` via the proxy.
    """
    base = service.snapshot()

    # Attempt to fetch scheduler health via the proxy stored in app state.
    # main.py's lifespan sets ``app.state.scheduler_proxy`` when a proxy is wired.
    scheduler_proxy: SchedulerProxy | None = getattr(getattr(request.app, 'state', None), 'scheduler_proxy', None)

    scheduler_status = 'offline'
    scheduler_info: dict[str, object] = {}

    if scheduler_proxy is not None:
        try:
            if scheduler_proxy.is_scheduler_up():
                raw = await asyncio.wait_for(scheduler_proxy.fetch_health(), timeout=1.0)
                scheduler_status = str(raw.get('status', 'unknown'))
                scheduler_info = {k: v for k, v in raw.items() if k != 'status'}
        except Exception:  # noqa: BLE001
            scheduler_status = 'offline'

    overall = 'ok' if (base.status == 'ok' and scheduler_status == 'ok') else 'degraded'

    return {
        'status': overall,
        'api': {'status': base.status, 'version': '2.0.0'},
        'scheduler': {'status': scheduler_status, **scheduler_info},
        'checked_at': datetime.datetime.now(datetime.UTC).isoformat(),
    }

"""Service exposing the downloader progress table as pydantic snapshots.

When a :class:`~app.api._scheduler_proxy.SchedulerProxy` is wired, the
snapshot is read from the proxy's cached WebSocket data.  Otherwise
(legacy / CLI / scheduler-process mode) it reads directly from the
in-process :class:`ProgressBus`.

RBAC filtering
--------------
* admin: receives every in-flight task entry.
* downloader: receives only entries whose ``owner_id`` matches the caller's
  ``user.id``.

The ``owner_username`` field is populated on admin snapshots by looking up
the username for each ``owner_id`` via the :class:`UserRepository`.

Terminal-status entries
-----------------------
Terminal entries (status in {"下載完成", "任務完成", …}) are **included** in
the snapshot so the frontend can place them in the 近期完成 column via the
WebSocket push (≤ 1 s latency) without waiting for the 60-second DB history
poll.  ``ProgressBus`` keeps finished entries alive for 7 days, and the
``finished_at`` field on the DTO tells the frontend exactly when the task
completed.
"""

from __future__ import annotations

import functools
import typing as T

import anyio.to_thread

from ..models import TaskProgressEntry, TaskProgressSnapshot
from ..persistence.user_repo import UserRow
from ._factory import container_bound
from .redis_progress_reader import RedisProgressReader

if T.TYPE_CHECKING:
    from ..api._scheduler_proxy import SchedulerProxy
    from ..downloader.progress import ProgressBus, TaskProgress
    from ..persistence.user_repo import UserRepository


class ProgressService:
    """Snapshots the downloader's in-memory progress table."""

    def __init__(
        self,
        progress_bus: ProgressBus,
        user_repo: UserRepository | None = None,
        scheduler_proxy: SchedulerProxy | None = None,
        redis_reader: RedisProgressReader | None = None,
    ) -> None:
        self._bus = progress_bus
        self._user_repo = user_repo
        self._proxy = scheduler_proxy
        self._redis_reader = redis_reader

    async def snapshot(self, user: UserRow) -> TaskProgressSnapshot:
        """Return a progress snapshot filtered by the caller's role.

        Data source priority:
        1. ``redis_reader.snapshot()`` — API process, post-migration (Redis
           mirror available).
        2. ``scheduler_proxy.latest_snapshot()`` — API process, pre-migration
           (proxy wired but no Redis reader).
        3. ``progress_bus.snapshot()`` — in-process fallback (CLI / scheduler
           process / proxy not wired).

        If the proxy is wired but the scheduler is down, returns an empty
        snapshot (the frontend shows a disconnect banner).

        * admin: all in-flight tasks; ``owner_username`` is populated for
          each entry whose ``owner_id`` is known.
        * downloader: only tasks whose ``owner_id`` matches ``user.id``.
        """
        # redis_reader.snapshot() is native async; the legacy proxy and bus
        # paths are still sync and need the thread bridge.
        if self._redis_reader is not None:
            raw: dict[int, TaskProgress] = await self._redis_reader.snapshot()
        elif self._proxy is not None:
            raw = await anyio.to_thread.run_sync(self._proxy.latest_snapshot)
        else:
            raw = await anyio.to_thread.run_sync(self._bus.snapshot)

        if user.role == 'admin':
            # Build a username cache to avoid N+1 repo queries.
            owner_ids = {e.owner_id for e in raw.values() if e.owner_id is not None}
            username_cache: dict[str, str] = {}
            user_repo = self._user_repo
            if user_repo is not None:
                for uid in owner_ids:
                    row = await anyio.to_thread.run_sync(functools.partial(user_repo.get, uid))
                    if row is not None:
                        username_cache[uid] = row.username

            tasks: dict[str, TaskProgressEntry] = {
                str(sn): TaskProgressEntry(
                    sn=sn,
                    rate=entry.rate,
                    status=entry.status,
                    filename=entry.filename,
                    bangumi_name=entry.bangumi_name,
                    episode=entry.episode,
                    resolution=entry.resolution,
                    speed_mbps=entry.speed_mbps,
                    eta_seconds=entry.eta_seconds,
                    retries=entry.retries,
                    started_at=(entry.started_at.isoformat() if entry.started_at is not None else None),
                    finished_at=(entry.finished_at.isoformat() if entry.finished_at is not None else None),
                    cooldown_until=(entry.cooldown_until.isoformat() if entry.cooldown_until is not None else None),
                    owner_id=entry.owner_id,
                    owner_username=username_cache.get(entry.owner_id) if entry.owner_id is not None else None,
                )
                for sn, entry in raw.items()
            }
        else:
            # Downloader: only own tasks.
            tasks = {
                str(sn): TaskProgressEntry(
                    sn=sn,
                    rate=entry.rate,
                    status=entry.status,
                    filename=entry.filename,
                    bangumi_name=entry.bangumi_name,
                    episode=entry.episode,
                    resolution=entry.resolution,
                    speed_mbps=entry.speed_mbps,
                    eta_seconds=entry.eta_seconds,
                    retries=entry.retries,
                    started_at=(entry.started_at.isoformat() if entry.started_at is not None else None),
                    finished_at=(entry.finished_at.isoformat() if entry.finished_at is not None else None),
                    cooldown_until=(entry.cooldown_until.isoformat() if entry.cooldown_until is not None else None),
                    owner_id=None,
                    owner_username=None,
                )
                for sn, entry in raw.items()
                if entry.owner_id == user.id
            }

        return TaskProgressSnapshot(tasks=tasks)


get_progress_service = container_bound(
    lambda c: ProgressService(
        c.progress_bus,
        c.user_repo,
        getattr(c, 'scheduler_proxy', None),
        getattr(c, 'redis_progress_reader', None),
    )
)
"""FastAPI dependency resolver for :class:`ProgressService`."""

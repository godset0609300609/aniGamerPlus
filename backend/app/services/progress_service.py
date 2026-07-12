"""Service exposing the downloader progress table as pydantic snapshots.

RBAC filtering
--------------
* admin: receives every in-flight task entry.
* downloader: receives only entries whose ``owner_id`` matches the caller's
  ``user.id``.

The ``owner_username`` and ``owner_avatar_url`` fields are populated on
admin snapshots by looking up each ``owner_id`` via the
:class:`UserRepository` (one lookup per distinct owner per snapshot).

Terminal-status entries
-----------------------
Terminal entries (status in {"下載完成", "任務完成", …}) are **included** in
the snapshot so the frontend can place them in the 近期完成 column via the
WebSocket push (≤ 1 s latency) without waiting for the 60-second DB history
poll.  ``ProgressBus`` keeps finished entries alive for 7 days, and the
``finished_at`` field on the DTO tells the frontend exactly when the task
completed.

Dismissal (``force_finish``)
-----------------------------
Dismissing a card from the monitor ("X" button) is best-effort: it sends a
``dramatiq_abort`` signal (which only reaches a genuinely live worker — a
ghost's owning process is gone, so the signal has nothing to land on) and
then unconditionally closes out the Redis mirror so the card disappears
either way. See :meth:`ProgressService.force_finish` for the two-phase
breakdown.
"""

from __future__ import annotations

import contextlib
import functools
import typing as T

import anyio.to_thread
import fastapi

from ..models import TaskProgressEntry, TaskProgressSnapshot
from ..persistence.user_repo import UserRow
from ._factory import container_bound
from .redis_progress_reader import RedisProgressReader

if T.TYPE_CHECKING:
    from ..downloader.progress import ProgressBus, TaskProgress
    from ..persistence.user_repo import UserRepository
    from ..redis_state import MessageIdRegistry


class ProgressService:
    """Snapshots the downloader's in-memory progress table."""

    def __init__(
        self,
        progress_bus: ProgressBus,
        user_repo: UserRepository | None = None,
        redis_reader: RedisProgressReader | None = None,
        bt_progress_bus: ProgressBus | None = None,
        message_id_registry: MessageIdRegistry | None = None,
    ) -> None:
        self._bus = progress_bus
        self._user_repo = user_repo
        self._redis_reader = redis_reader
        # Optional: only force_finish() needs this, to route a BT-sourced sn
        # at the BT bus rather than the shared one (see Container.bt_progress_bus's
        # docstring in core.py for why they are separate ProgressBus instances).
        self._bt_bus = bt_progress_bus
        # Optional: only force_finish() needs this, to best-effort signal a
        # genuinely live worker (via dramatiq_abort) before closing the
        # mirror. None in any process/test that doesn't wire Redis.
        self._message_id_registry = message_id_registry

    async def _raw_snapshot(self) -> dict[int, TaskProgress]:
        """Unfiltered ``sn -> entry`` snapshot from whichever source is wired.

        Data source priority:
        1. ``redis_reader.snapshot()`` — API process, post-migration (Redis
           mirror available).
        2. ``progress_bus.snapshot()`` — in-process fallback (CLI / scheduler
           process).
        """
        # redis_reader.snapshot() is native async; the bus path is still sync
        # and needs the thread bridge.
        if self._redis_reader is not None:
            return await self._redis_reader.snapshot()
        return await anyio.to_thread.run_sync(self._bus.snapshot)

    async def snapshot(self, user: UserRow) -> TaskProgressSnapshot:
        """Return a progress snapshot filtered by the caller's role.

        * admin: all in-flight tasks; ``owner_username`` is populated for
          each entry whose ``owner_id`` is known.
        * downloader: only tasks whose ``owner_id`` matches ``user.id``.
        """
        raw = await self._raw_snapshot()

        if user.role == 'admin':
            # Build username/avatar caches from a single repo lookup per
            # owner_id to avoid N+1 queries — UserRow already carries both
            # fields, so one fetch populates both caches.
            owner_ids = {e.owner_id for e in raw.values() if e.owner_id is not None}
            username_cache: dict[str, str] = {}
            avatar_cache: dict[str, str | None] = {}
            user_repo = self._user_repo
            if user_repo is not None:
                for uid in owner_ids:
                    row = await anyio.to_thread.run_sync(functools.partial(user_repo.get, uid))
                    if row is not None:
                        username_cache[uid] = row.username
                        avatar_cache[uid] = row.avatar_url

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
                    owner_avatar_url=avatar_cache.get(entry.owner_id) if entry.owner_id is not None else None,
                    source=entry.source,
                    external_id=entry.external_id,
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
                    owner_avatar_url=None,
                    source=entry.source,
                    external_id=entry.external_id,
                )
                for sn, entry in raw.items()
                if entry.owner_id == user.id
            }

        return TaskProgressSnapshot(tasks=tasks)

    async def force_finish(self, sn: int, user: UserRow, *, status: str) -> None:
        """Force-close a live progress entry so it drops off the monitor.

        Used by the dismiss ("X") button on MonitorView task cards. Two
        phases:

        1. **Best-effort abort** — try to reach a genuinely *live* worker via
           ``dramatiq_abort`` (looked up through ``message_id_registry``) so
           it actually stops downloading instead of wasting bandwidth and
           later overwriting the mirror with a rosy ``'下載完成'`` that would
           make the dismissed card reappear. This step is a no-op for a
           ghost card: its owning process is already dead, so there is
           nothing on the other end of the pubsub signal, and any Redis
           blip or missing broker is swallowed rather than failing the
           dismiss.
        2. **Unconditional mirror close** — ``ProgressBus.force_finish()``
           closes out the Redis mirror directly, which is the only way to
           dismiss a ghost card that a plain ``cancel()`` cannot reach (a
           ghost's owning process is already dead, so ``ProgressBus.cancel()``
           is itself a silent no-op) — see
           :class:`~app.services.bt_progress_reconciler.BtProgressReconciler`'s
           module docstring for the full story of how a ghost gets into this
           state in the first place. This step always runs, regardless of
           whether the abort in step 1 found anything to signal.

        Authorization: admin may dismiss any entry; a downloader may only
        dismiss an entry they own (``entry.owner_id == user.id``).

        Idempotent: an entry that is already terminal (``finished_at`` set)
        is left untouched. This matters because — unlike ``ProgressBus.finish()``
        — ``force_finish()`` has no reliable local-state guard when called
        from the API process (it never locally ``start()``ed this ``sn``, so
        it always fabricates a fresh entry); without this check here, a
        repeated/racing dismiss click on an entry that has since genuinely
        completed would clobber a real outcome (e.g. ``'下載完成'``) with
        ``'已取消'``.

        Raises 404 if ``sn`` has no visible entry at all, 403 if the caller
        does not own it and is not an admin.
        """
        raw = await self._raw_snapshot()
        entry = raw.get(sn)
        if entry is None:
            raise fastapi.HTTPException(status_code=404, detail=f'Task sn={sn} not found')
        if user.role != 'admin' and entry.owner_id != user.id:
            raise fastapi.HTTPException(status_code=403, detail=f'Not authorized to dismiss task sn={sn}')
        if entry.finished_at is not None:
            return

        bus = self._bt_bus if entry.source == 'bt' and self._bt_bus is not None else self._bus

        # Phase 1: best-effort abort signal. Never allowed to block the
        # mirror cleanup below — a Redis blip, a missing message_id (ghost),
        # or no registry at all must all fall through silently.
        with contextlib.suppress(Exception):
            bus.cancel(sn)
        with contextlib.suppress(Exception):
            if self._message_id_registry is not None:
                message_id = await self._message_id_registry.get(sn)
                if message_id is not None:
                    import dramatiq_abort
                    import dramatiq_abort.middleware

                    await anyio.to_thread.run_sync(
                        functools.partial(
                            dramatiq_abort.abort,
                            message_id,
                            mode=dramatiq_abort.middleware.AbortMode.ABORT,
                            abort_timeout=5000,
                        )
                    )

        # Phase 2: unconditional mirror close so the card disappears either way.
        # ``source=entry.source`` matters for the ghost-card case: this process
        # never locally start()ed sn, so force_finish() has to synthesise a
        # fresh entry — without forwarding the source already visible in the
        # raw (Redis) snapshot, that synthesised entry would carry source=None
        # and the frontend badge would mislabel it as 動畫瘋 (see sourceBadge.ts).
        await anyio.to_thread.run_sync(
            functools.partial(bus.force_finish, sn, status=status, filename=entry.filename, source=entry.source)
        )


get_progress_service = container_bound(
    lambda c: ProgressService(
        c.progress_bus,
        c.user_repo,
        getattr(c, 'redis_progress_reader', None),
        getattr(c, 'bt_progress_bus', None),
        getattr(c, 'message_id_registry', None),
    )
)
"""FastAPI dependency resolver for :class:`ProgressService`."""

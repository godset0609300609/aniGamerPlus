"""Service that enqueues manual download tasks via dramatiq actors."""

from __future__ import annotations

import re
import typing as T

import anyio.to_thread
import fastapi

from ..models import ManualTaskRequest
from ..persistence.user_repo import UserRow
from ._factory import container_bound

if T.TYPE_CHECKING:
    from ..core import Container
    from ..downloader.bilibili.runner import BilibiliRunner
    from ..downloader.progress import ProgressBus
    from ..persistence.settings_repo import SettingsRepository
    from ..persistence.task_id_map_repo import TaskIdMapRepository
    from ..redis_state import MessageIdRegistry
    from ..scheduler.manual_runner import ManualRunner
    from .progress_service import ProgressService


class ManualTaskRunner(T.Protocol):
    """T.Protocol matching :meth:`ManualRunner.run`.

    Kept as a narrow interface so tests can substitute a minimal fake
    without constructing the full ``ManualRunner`` dependency graph.
    """

    def run(
        self,
        sn: int | None,
        *,
        resolution: str = ...,
        mode: str = ...,
        thread_limit: int = ...,
        ep_range: list[str] | None = ...,
        classify: bool = ...,
        get_info: bool = ...,
        realtime_show: bool = ...,
        cui_danmu: bool = ...,
        owner_id: str | None = ...,
        bilingual: bool = ...,
    ) -> None: ...


class TaskService:
    """Encapsulates input normalisation and dispatch to dramatiq actors.

    When a broker is available ``enqueue`` sends the task via
    ``run_download.send_with_options``.  If the actor module cannot be
    imported (e.g. running without Redis in tests / CLI) it falls back to
    calling ``manual_runner`` directly in a background thread for backwards
    compatibility.

    ``cancel_task`` updates the UI immediately via the progress bus, then
    looks up the dramatiq message_id from the registry to call
    ``dramatiq_abort.abort``.
    """

    VALID_RESOLUTIONS = frozenset({'360', '480', '540', '720', '1080'})
    VALID_MODES = frozenset({'single', 'latest', 'all', 'largest-sn'})
    _MAX_MULTI_THREAD = 5

    #: Per-user cap on in-flight (not-yet-finished) tasks (fix #7). A
    #: downloader submitting past this many concurrent tasks is almost
    #: certainly a mistake (double-click, script) or abuse rather than a
    #: legitimate workload, so ``enqueue`` rejects the submission with 429
    #: instead of piling more work onto an already-saturated queue.
    _MAX_INFLIGHT_PER_USER = 20
    #: Admin's cap counts every user's in-flight tasks combined (admin
    #: snapshots are not owner-filtered) — set well above the per-user cap
    #: so a busy multi-user instance isn't throttled by routine admin use.
    _MAX_INFLIGHT_PER_ADMIN = 50

    #: Matches a b23.tv short link — resolving one requires a synchronous
    #: HTTP redirect (up to 10s, see ``url_parser._resolve_b23``). Detecting
    #: it here (regex only, no network) lets ``_enqueue_bilibili`` skip that
    #: resolution and defer it to the dramatiq worker instead of spending an
    #: anyio thread-pool slot on the request path (fix #20).
    _B23_RE: T.ClassVar[re.Pattern[str]] = re.compile(r'b23\.tv/', re.IGNORECASE)

    def __init__(
        self,
        settings_repo: SettingsRepository,
        manual_runner: ManualRunner | ManualTaskRunner,
        *,
        progress_bus: ProgressBus | None = None,
        progress_service: ProgressService | None = None,
        message_id_registry: MessageIdRegistry | None = None,
        task_id_map_repo: TaskIdMapRepository | None = None,
        bilibili_runner: BilibiliRunner | None = None,
    ) -> None:
        self._settings_repo = settings_repo
        self._runner = manual_runner
        self._progress_bus = progress_bus
        self._progress_service = progress_service
        self._message_id_registry = message_id_registry
        self._task_id_map_repo = task_id_map_repo
        self._bilibili_runner = bilibili_runner

    async def enqueue(self, request: ManualTaskRequest, user: UserRow) -> None:
        """Enqueue a manual download task via dramatiq (or in-process fallback).

        Tries to import and send the ``run_download`` actor first.  Falls
        back to spinning up a daemon thread that calls ``manual_runner.run``
        directly when no broker is configured (CLI / test-stub environment).

        Raises ``HTTP 429`` before doing any work if the caller already has
        too many in-flight tasks (fix #7) — see ``_check_inflight_cap``.
        """
        await self._check_inflight_cap(user)
        settings = await anyio.to_thread.run_sync(self._settings_repo.load)
        resolution = self._pick_resolution(request.resolution, settings.download_resolution)
        owner_id = user.id

        # Bilibili branch — independent pipeline from animad.
        if request.source == 'bilibili':
            await self._enqueue_bilibili(request, resolution=resolution, owner_id=owner_id)
            return

        mode = request.mode if request.mode in self.VALID_MODES else 'single'
        thread_limit = min(request.thread, self._MAX_MULTI_THREAD)

        # Dramatiq path.
        try:
            from ..tasks.download import run_download

            run_download.send_with_options(
                kwargs={
                    'sn': int(request.sn),
                    'resolution': resolution,
                    'mode': mode,
                    'thread_limit': thread_limit,
                    'classify': request.classify,
                    'cui_danmu': request.danmu,
                    'owner_id': owner_id,
                    'bilingual': request.bilingual,
                },
            )
            return
        except Exception:  # noqa: BLE001 — no broker / stub broker
            pass

        # Fallback: in-process dispatch (CLI mode / tests without broker).
        import threading

        sn = int(request.sn)

        def _run() -> None:
            self._runner.run(
                sn,
                resolution=resolution,
                mode=mode,
                thread_limit=thread_limit,
                ep_range=[],
                classify=request.classify,
                realtime_show=False,
                cui_danmu=request.danmu,
                owner_id=owner_id,
                bilingual=request.bilingual,
            )

        threading.Thread(target=_run, daemon=True).start()

    async def _enqueue_bilibili(
        self,
        request: ManualTaskRequest,
        *,
        resolution: str,
        owner_id: str,
    ) -> None:
        """Dispatch a Bilibili download task.

        b23.tv short links require a synchronous HTTP redirect (up to 10s,
        see ``url_parser._resolve_b23``) to resolve to a real BV/av id.
        Resolving that on the request path would tie up an anyio
        thread-pool slot for the duration of the redirect — instead, when
        the raw input is a b23.tv link, the whole ``parse_bilibili_input``
        call (including the b23 resolution) is deferred to the dramatiq
        worker / fallback thread, and the task_sn is allocated against the
        raw link so retries of the same short link dedup onto the same
        task_sn.  Non-b23 inputs (full URLs, raw BV/av ids) resolve
        synchronously as before — that path is regex-only, no network call.
        """
        from ..downloader.bilibili.url_parser import parse_bilibili_input

        raw = str(request.sn).strip()
        is_b23 = bool(self._B23_RE.search(raw))

        if self._task_id_map_repo is None:
            raise fastapi.HTTPException(status_code=503, detail='Task ID map 未初始化')

        bvid: str | None = None
        if not is_b23:
            try:
                bvid, _aid, _multi = await anyio.to_thread.run_sync(lambda: parse_bilibili_input(raw))
            except Exception as exc:  # noqa: BLE001
                raise fastapi.HTTPException(
                    status_code=400,
                    detail='無法解析 Bilibili 連結',
                ) from exc

        external_id = bvid if bvid is not None else raw
        task_sn = await anyio.to_thread.run_sync(
            lambda: self._task_id_map_repo.allocate(source='bilibili', external_id=external_id)
        )

        # Dramatiq path.
        try:
            from ..tasks.bilibili_download import run_bilibili_download

            run_bilibili_download.send_with_options(
                kwargs={
                    'task_sn': task_sn,
                    'bvid': bvid or '',
                    'raw_input': raw if bvid is None else None,
                    'resolution': resolution,
                    'classify': request.classify,
                    'owner_id': owner_id,
                },
            )
            return
        except Exception:  # noqa: BLE001 — no broker / stub broker
            pass

        # Fallback: in-process dispatch. Resolution (if deferred) happens
        # inside the background thread, not on the request path.
        import threading

        if self._bilibili_runner is None:
            raise fastapi.HTTPException(status_code=503, detail='Bilibili runner 未初始化')

        runner = self._bilibili_runner

        def _run_bilibili() -> None:
            resolved_bvid = bvid
            if resolved_bvid is None:
                resolved_bvid, _aid, _multi = parse_bilibili_input(raw)
            runner.run(
                task_sn,
                bvid=resolved_bvid,
                resolution=resolution,
                classify=request.classify,
                owner_id=owner_id,
            )

        threading.Thread(target=_run_bilibili, daemon=True).start()

    async def cancel_task(self, sn: int, user: UserRow) -> None:
        """Cancel a running or queued task.

        Authorization:
        * admin can cancel any task.
        * downloader can only cancel tasks they own.

        If the task is not visible to the caller (not in their snapshot),
        a 404 is raised to avoid leaking existence.
        """
        # Verify the caller can see this task — check via progress snapshot.
        if self._progress_service is not None:
            snap = await self._progress_service.snapshot(user)
            if str(sn) not in snap.tasks:
                raise fastapi.HTTPException(
                    status_code=404,
                    detail=f'Task sn={sn} not found',
                )
        elif user.role != 'admin':
            # No progress service wired and not an admin — deny.
            raise fastapi.HTTPException(
                status_code=404,
                detail=f'Task sn={sn} not found',
            )

        # Dramatiq path: update UI immediately, then abort the running actor.
        if self._progress_bus is not None:
            self._progress_bus.cancel(int(sn))

        if self._message_id_registry is not None:
            message_id = await self._message_id_registry.get(int(sn))
            if message_id is not None:
                import dramatiq_abort

                await anyio.to_thread.run_sync(
                    lambda: dramatiq_abort.abort(
                        message_id,
                        mode=dramatiq_abort.AbortMode.ABORT,
                        abort_timeout=5000,
                    )
                )

    # -- helpers ------------------------------------------------------------

    async def _check_inflight_cap(self, user: UserRow) -> None:
        """Reject the submission once the caller has too many in-flight tasks.

        Counts entries from ``progress_service.snapshot(user)`` — already
        RBAC-filtered the same way ``cancel_task`` uses it — whose
        ``finished_at`` is still ``None`` (i.e. genuinely in-flight, not a
        recently-completed entry the bus keeps around for the UI). A
        downloader's snapshot only contains their own tasks; an admin's
        snapshot spans every user, so the admin cap is deliberately higher.

        No-ops when no ``progress_service`` is wired (CLI / test-stub
        environments), matching the rest of this class's optional-collaborator
        pattern.
        """
        if self._progress_service is None:
            return
        limit = self._MAX_INFLIGHT_PER_ADMIN if user.role == 'admin' else self._MAX_INFLIGHT_PER_USER
        snapshot = await self._progress_service.snapshot(user)
        in_flight = sum(1 for entry in snapshot.tasks.values() if entry.finished_at is None)
        if in_flight >= limit:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_429_TOO_MANY_REQUESTS,
                detail='同時進行中的任務過多，請稍後再試',
            )

    def _pick_resolution(self, requested: str, default: str) -> str:
        if requested in self.VALID_RESOLUTIONS:
            return requested
        return str(default)


def _build_task_service(c: Container) -> TaskService:
    from .progress_service import ProgressService

    progress_service = ProgressService(
        c.progress_bus,
        getattr(c, 'user_repo', None),
        getattr(c, 'redis_progress_reader', None),
        getattr(c, 'bt_progress_bus', None),
    )
    return TaskService(
        c.settings_repo,
        c.manual_runner,
        progress_bus=c.progress_bus,
        progress_service=progress_service,
        message_id_registry=getattr(c, 'message_id_registry', None),
        task_id_map_repo=getattr(c, 'task_id_map_repo', None),
        bilibili_runner=getattr(c, 'bilibili_runner', None),
    )


get_task_service = container_bound(_build_task_service)
"""FastAPI dependency resolver for :class:`TaskService`."""


__all__ = ['ManualTaskRunner', 'TaskService', 'get_task_service']

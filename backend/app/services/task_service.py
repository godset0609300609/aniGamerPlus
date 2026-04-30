"""Service that enqueues manual download tasks via dramatiq actors."""

from __future__ import annotations

import typing as T

import anyio.to_thread
import fastapi

from ..models import ManualTaskRequest
from ..persistence.user_repo import UserRow
from ._factory import container_bound

if T.TYPE_CHECKING:
    from ..core import Container
    from ..downloader.progress import ProgressBus
    from ..persistence.settings_repo import SettingsRepository
    from ..redis_state import MessageIdRegistry
    from ..scheduler.manual_runner import ManualRunner
    from .progress_service import ProgressService


class _LegacySchedulerProxy(T.Protocol):
    """Narrow interface for the legacy SchedulerProxy kept for backward-compat tests."""

    async def enqueue_manual(self, request: object, owner_id: str) -> None: ...
    def is_scheduler_up(self) -> bool: ...
    async def cancel_task(self, sn: int) -> None: ...


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

    def __init__(
        self,
        settings_repo: SettingsRepository,
        manual_runner: ManualRunner | ManualTaskRunner,
        # Legacy positional/keyword arg kept for backward compat with existing
        # tests and conftest that pass scheduler_proxy as 3rd arg.
        scheduler_proxy: _LegacySchedulerProxy | None = None,
        *,
        progress_bus: ProgressBus | None = None,
        progress_service: ProgressService | None = None,
        message_id_registry: MessageIdRegistry | None = None,
    ) -> None:
        self._settings_repo = settings_repo
        self._runner = manual_runner
        self._progress_bus = progress_bus
        self._progress_service = progress_service
        self._message_id_registry = message_id_registry
        # Legacy compat: tests pass a FakeSchedulerProxy so cancel_task/enqueue
        # can exercise the old proxy-based path without a real dramatiq broker.
        self._legacy_proxy: _LegacySchedulerProxy | None = scheduler_proxy

    async def enqueue(self, request: ManualTaskRequest, user: UserRow) -> None:
        """Enqueue a manual download task via dramatiq (or in-process fallback).

        Tries to import and send the ``run_download`` actor first.  Falls
        back to spinning up a daemon thread that calls ``manual_runner.run``
        directly when no broker is configured (CLI / test-stub environment).
        """
        settings = await anyio.to_thread.run_sync(self._settings_repo.load)
        resolution = self._pick_resolution(request.resolution, settings.download_resolution)
        mode = request.mode if request.mode in self.VALID_MODES else 'single'
        thread_limit = min(request.thread, self._MAX_MULTI_THREAD)
        owner_id = user.id

        # Legacy proxy path — kept for tests that wire a FakeSchedulerProxy
        # as the third positional argument.
        if self._legacy_proxy is not None:
            from ..api._scheduler_proxy import SchedulerUnreachable

            try:
                from ..models import Resolution

                normalised = ManualTaskRequest(
                    sn=int(request.sn),
                    resolution=T.cast(Resolution, resolution),
                    mode=mode,
                    thread=thread_limit,
                    classify=request.classify,
                    danmu=request.danmu,
                )
                await self._legacy_proxy.enqueue_manual(normalised, owner_id)
            except SchedulerUnreachable as exc:
                raise fastapi.HTTPException(
                    status_code=503,
                    detail='排程服務暫時無回應，請稍後再試',
                ) from exc
            return

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
            )

        threading.Thread(target=_run, daemon=True).start()

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

        # Legacy proxy path — kept for tests wiring a FakeSchedulerProxy.
        if self._legacy_proxy is not None:
            if not self._legacy_proxy.is_scheduler_up():
                raise fastapi.HTTPException(
                    status_code=503,
                    detail='Scheduler 暫時無法連線，請稍後再試',
                )
            try:
                await self._legacy_proxy.cancel_task(sn)
            except Exception as exc:  # noqa: BLE001
                raise fastapi.HTTPException(
                    status_code=503,
                    detail=f'Scheduler 無法連線: {exc}',
                ) from exc
            return

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

    def _pick_resolution(self, requested: str, default: str) -> str:
        if requested in self.VALID_RESOLUTIONS:
            return requested
        return str(default)


def _build_task_service(c: Container) -> TaskService:
    from .progress_service import ProgressService

    progress_service = ProgressService(
        c.progress_bus,
        getattr(c, 'user_repo', None),
        None,
        getattr(c, 'redis_progress_reader', None),
    )
    return TaskService(
        c.settings_repo,
        c.manual_runner,
        progress_bus=c.progress_bus,
        progress_service=progress_service,
        message_id_registry=getattr(c, 'message_id_registry', None),
    )


get_task_service = container_bound(_build_task_service)
"""FastAPI dependency resolver for :class:`TaskService`."""


__all__ = ['ManualTaskRunner', 'TaskService', 'get_task_service']

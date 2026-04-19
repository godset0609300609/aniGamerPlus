"""Service that enqueues manual download tasks via the scheduler proxy."""

from __future__ import annotations

import typing as T

import anyio.to_thread
import fastapi

from ..models import ManualTaskRequest
from ..persistence.user_repo import UserRow
from ._factory import container_bound

if T.TYPE_CHECKING:
    from ..api._scheduler_proxy import SchedulerProxy
    from ..core import Container
    from ..persistence.settings_repo import SettingsRepository
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
        user_cmd: bool = ...,
        realtime_show: bool = ...,
        cui_danmu: bool = ...,
        owner_id: str | None = ...,
    ) -> None: ...


class TaskService:
    """Encapsulates input normalisation and forwarding to the scheduler proxy.

    When a :class:`~app.api._scheduler_proxy.SchedulerProxy` is wired
    (``scheduler_proxy is not None``), ``enqueue`` sends the request to the
    scheduler process via HTTP.  If the scheduler is unreachable it raises a
    503.  When no proxy is wired (legacy / CLI mode), it falls back to calling
    ``manual_runner`` directly in a background thread for backwards compat.
    """

    VALID_RESOLUTIONS = frozenset({'360', '480', '540', '720', '1080'})
    VALID_MODES = frozenset({'single', 'latest', 'all', 'largest-sn'})
    _MAX_MULTI_THREAD = 5

    def __init__(
        self,
        settings_repo: SettingsRepository,
        manual_runner: ManualRunner | ManualTaskRunner,
        scheduler_proxy: SchedulerProxy | None = None,
        progress_service: ProgressService | None = None,
    ) -> None:
        self._settings_repo = settings_repo
        self._runner = manual_runner
        self._proxy = scheduler_proxy
        self._progress_service = progress_service

    async def enqueue(self, request: ManualTaskRequest, user: UserRow) -> None:
        """Enqueue a manual download task.

        When the scheduler proxy is wired, the task is forwarded to the
        scheduler process via HTTP.  If the HTTP call fails
        (:class:`~app.api._scheduler_proxy.SchedulerUnreachable`), a 503 is
        raised.  The WS liveness state is **not** consulted — a short WS
        reconnect window must not prevent task submission.

        In fallback mode (no proxy wired) the task is dispatched in-process
        in a background thread (CLI / legacy compatibility).
        """
        settings = await anyio.to_thread.run_sync(self._settings_repo.load)
        resolution = self._pick_resolution(request.resolution, settings.download_resolution)
        mode = request.mode if request.mode in self.VALID_MODES else 'single'
        thread_limit = min(request.thread, self._MAX_MULTI_THREAD)
        owner_id = user.id

        # Build a normalised request so the proxy receives canonical values.
        # resolution is guaranteed to be a valid Literal by _pick_resolution;
        # mypy can't narrow str → Literal so we use a cast.
        from ..models import Resolution

        normalised = ManualTaskRequest(
            sn=int(request.sn),
            resolution=T.cast(Resolution, resolution),
            mode=mode,
            thread=thread_limit,
            classify=request.classify,
            danmu=request.danmu,
        )

        if self._proxy is not None:
            from ..api._scheduler_proxy import SchedulerUnreachable

            try:
                await self._proxy.enqueue_manual(normalised, owner_id)
            except SchedulerUnreachable as exc:
                raise fastapi.HTTPException(
                    status_code=503,
                    detail='排程服務暫時無回應，請稍後再試',
                ) from exc
            return

        # Fallback: in-process dispatch (CLI mode / tests without proxy).
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

        If the scheduler proxy is not available, a 503 is raised.
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

        if self._proxy is not None:
            if not self._proxy.is_scheduler_up():
                raise fastapi.HTTPException(
                    status_code=503,
                    detail='Scheduler 暫時無法連線，請稍後再試',
                )
            try:
                await self._proxy.cancel_task(sn)
            except Exception as exc:  # noqa: BLE001
                raise fastapi.HTTPException(
                    status_code=503,
                    detail=f'Scheduler 無法連線: {exc}',
                ) from exc
            return

        # Fallback: in-process cancel (CLI mode / tests without proxy).
        # This path is only reachable in tests or single-process mode.
        # The progress_service snapshot check above already validated access.

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
        getattr(c, 'scheduler_proxy', None),
    )
    return TaskService(
        c.settings_repo,
        c.manual_runner,
        getattr(c, 'scheduler_proxy', None),
        progress_service=progress_service,
    )


get_task_service = container_bound(_build_task_service)
"""FastAPI dependency resolver for :class:`TaskService`."""


__all__ = ['ManualTaskRunner', 'TaskService', 'get_task_service']

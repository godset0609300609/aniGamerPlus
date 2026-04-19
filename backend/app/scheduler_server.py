"""Internal FastAPI server for the scheduler process.

Listens on 127.0.0.1:5001 and exposes routes prefixed with /internal/*.
All routes are protected by a shared-secret header check.

Entry point: ``anigamerplus-scheduler``
"""

from __future__ import annotations

import asyncio
import collections.abc
import contextlib
import dataclasses
import datetime
import os
import secrets
import threading
import time
import typing as T

import fastapi
import pydantic
import uvicorn

from .core import Container, build_container
from .log_config import get_ring_buffer_handler

# ---------------------------------------------------------------------------
# Shared-secret resolution
# ---------------------------------------------------------------------------

_SECRET_ENV_VAR = 'ANIGAMERPLUS_INTERNAL_SECRET'
_RESOLVED_SECRET: str | None = None


def _get_internal_secret() -> str:
    """Return the shared secret.

    Reads ``ANIGAMERPLUS_INTERNAL_SECRET`` from the environment.  If the
    variable is not set, generates a random token once per process and
    logs it so operators can copy it into the API process's environment.
    """
    global _RESOLVED_SECRET  # noqa: PLW0603
    if _RESOLVED_SECRET is not None:
        return _RESOLVED_SECRET
    env_val = os.environ.get(_SECRET_ENV_VAR, '')
    _RESOLVED_SECRET = env_val if env_val else secrets.token_urlsafe(32)
    return _RESOLVED_SECRET


# ---------------------------------------------------------------------------
# FastAPI auth dependency
# ---------------------------------------------------------------------------


async def _verify_secret(
    x_internal_secret: T.Annotated[str | None, fastapi.Header(alias='X-Internal-Secret')] = None,
) -> None:
    """Reject the request if the shared-secret header is missing or wrong."""
    if x_internal_secret is None or x_internal_secret != _get_internal_secret():
        raise fastapi.HTTPException(
            status_code=401,
            detail='Missing or invalid X-Internal-Secret header',
        )


_SecretDep = T.Annotated[None, fastapi.Depends(_verify_secret)]


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class _InternalManualTaskRequest(pydantic.BaseModel):
    """Body for POST /internal/tasks/manual."""

    sn: str | int
    resolution: str = '1080'
    mode: str = 'single'
    thread: int = pydantic.Field(default=1, ge=1, le=50)
    classify: bool = True
    danmu: bool = False
    owner_id: str = ''


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

_SERVER_START_TIME: float = time.monotonic()


def build_scheduler_app(container: Container) -> fastapi.FastAPI:
    """Build the internal FastAPI app listening on 127.0.0.1:5001."""

    @contextlib.asynccontextmanager
    async def _lifespan(app: fastapi.FastAPI) -> collections.abc.AsyncIterator[None]:
        from .scheduler.watchdog import SchedulerWatchdog

        # Bootstrap the ring buffer from the on-disk log file.  The class-level
        # flag in RingBufferHandler ensures only one bootstrap runs per process
        # even when both this lifespan and the API lifespan call it.
        _n = get_ring_buffer_handler().bootstrap_from_file(container.paths.logs_dir)
        if _n > 0:
            container.logger.info(
                None,
                'Bootstrap',
                f'從 log 檔載入最近 {_n} 筆歷史訊息',
                display=False,
            )

        secret = _get_internal_secret()
        env_set = bool(os.environ.get(_SECRET_ENV_VAR, ''))
        if not env_set:
            container.logger.info(
                None,
                'Scheduler',
                (f'ANIGAMERPLUS_INTERNAL_SECRET not set — using generated secret: {secret}'),
            )
        # Mark any tasks that were left in-progress by the previous process
        # (e.g. due to a kill signal) as interrupted so the history UI does
        # not show stale "下載中" entries.
        task_history_repo = getattr(container, 'task_history_repo', None)
        if task_history_repo is not None:
            interrupted = task_history_repo.mark_interrupted_on_boot()
            if interrupted > 0:
                container.logger.info(
                    None,
                    'Scheduler',
                    f'標記 {interrupted} 個上次未完成的任務為中斷',
                )
            # One-time idempotent cleanup: coerce any legacy bogus final_status
            # values (e.g. '正在解析', '正在下載') written by older versions of
            # the code to '中斷'.  Safe to call on every boot.
            normalized = task_history_repo.normalize_legacy_statuses()
            if normalized > 0:
                container.logger.info(
                    None,
                    'Scheduler',
                    f'修正 {normalized} 筆歷史紀錄的非終態 final_status → 中斷',
                )

        container.logger.info(None, 'Scheduler', 'Starting UpdateLoop…')
        # Build a standalone watchdog exposed on app.state so the health
        # endpoint can report heartbeat age.  The UpdateLoop calls beat() on
        # this watchdog through the optional watchdog parameter; we pass it
        # explicitly when the container supports the kwarg (real Container
        # always does via build_update_loop(watchdog=...)).
        watchdog = SchedulerWatchdog(container.logger)
        watchdog.start()
        app.state.watchdog = watchdog

        try:
            loop = container.build_update_loop(watchdog=watchdog)
        except TypeError:
            # Test stubs / legacy containers that don't accept watchdog kwarg.
            loop = container.build_update_loop()
        thread = threading.Thread(
            target=loop.run_forever,
            name='anigamerplus-update-loop',
            daemon=True,
        )
        thread.start()
        app.state.update_loop = loop
        app.state.update_loop_thread = thread
        try:
            yield
        finally:
            container.logger.info(None, 'Scheduler', 'Stopping UpdateLoop…')
            loop.stop()

    app = fastapi.FastAPI(
        title='aniGamerPlus Scheduler (internal)',
        version='2.0.0',
        lifespan=_lifespan,
    )

    router = fastapi.APIRouter(prefix='/internal')

    # ---- POST /internal/tasks/manual ---------------------------------------

    @router.post('/tasks/manual', status_code=202)
    async def enqueue_manual(
        payload: _InternalManualTaskRequest,
        _auth: _SecretDep,
    ) -> dict[str, str]:
        """Fire-and-forget: spawn a daemon thread and return 202 immediately.

        Running the download pipeline synchronously (even in a thread-pool
        executor) blocks the HTTP response until the entire download finishes
        — which can take minutes.  Any network error (TLS timeout, etc.) then
        bubbles up as an ASGI 500.  Instead we start a daemon thread and return
        immediately; errors are caught and logged inside the thread.
        """

        # Capture payload fields now so the closure doesn't hold a reference to
        # the Pydantic model after the request context is gone.
        _sn = int(payload.sn)
        _resolution = payload.resolution
        _mode = payload.mode
        _thread_limit = min(payload.thread, 5)
        _classify = payload.classify
        _danmu = payload.danmu
        _owner_id = payload.owner_id or None

        def _run() -> None:
            try:
                container.manual_runner.run(
                    _sn,
                    resolution=_resolution,
                    mode=_mode,
                    thread_limit=_thread_limit,
                    ep_range=[],
                    classify=_classify,
                    realtime_show=False,
                    cui_danmu=_danmu,
                    owner_id=_owner_id,
                )
            except Exception as exc:  # noqa: BLE001
                container.logger.error(_sn, 'ManualTask', f'失敗: {exc}')
            finally:
                # Last-resort safety net: ensure the progress entry is closed
                # even if ManualRunner.run() raises an unhandled exception or
                # an inner path forgets to call finish().  finish() is idempotent
                # so this is a no-op when ManualRunner already called it.
                container.progress_bus.finish(_sn)

        threading.Thread(
            target=_run,
            name=f'manual-task-{_sn}',
            daemon=True,
        ).start()
        return {'status': 'accepted'}

    # ---- DELETE /internal/tasks/{sn} ---------------------------------------

    @router.delete('/tasks/{sn}')
    async def cancel_task(
        sn: int,
        _auth: _SecretDep,
    ) -> fastapi.Response:
        """Cancel an in-flight task by sn.

        Returns 204 if the task was found and the cancel signal was sent.
        Returns 404 if no task with the given sn is being tracked.
        """
        cancelled = container.progress_bus.cancel(sn)
        if cancelled:
            return fastapi.Response(status_code=204)
        raise fastapi.HTTPException(status_code=404, detail=f'Task sn={sn} not found')

    # ---- WS /internal/progress ---------------------------------------------

    @router.websocket('/progress')
    async def progress_ws(
        ws: fastapi.WebSocket,
        x_internal_secret: T.Annotated[str | None, fastapi.Header(alias='X-Internal-Secret')] = None,
    ) -> None:
        """Push a full progress snapshot dict every 500 ms."""
        if x_internal_secret is None or x_internal_secret != _get_internal_secret():
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            while True:
                snap = container.progress_bus.snapshot()
                payload: dict[str, dict[str, object]] = {}
                for sn, entry in snap.items():
                    entry_dict: dict[str, object] = dataclasses.asdict(entry)
                    # datetime fields are not JSON-serialisable — convert to ISO string.
                    if isinstance(entry_dict.get('started_at'), datetime.datetime):
                        dt: datetime.datetime = entry_dict['started_at']  # type: ignore[assignment]
                        entry_dict['started_at'] = dt.isoformat()
                    if isinstance(entry_dict.get('finished_at'), datetime.datetime):
                        dt2: datetime.datetime = entry_dict['finished_at']  # type: ignore[assignment]
                        entry_dict['finished_at'] = dt2.isoformat()
                    if isinstance(entry_dict.get('cooldown_until'), datetime.datetime):
                        dt3: datetime.datetime = entry_dict['cooldown_until']  # type: ignore[assignment]
                        entry_dict['cooldown_until'] = dt3.isoformat()
                    # _cancel_event is an internal signal; never send over wire.
                    entry_dict.pop('_cancel_event', None)
                    payload[str(sn)] = entry_dict
                await ws.send_json(payload)
                await asyncio.sleep(0.5)
        except fastapi.WebSocketDisconnect:
            return

    # ---- GET /internal/health ----------------------------------------------

    @router.get('/health')
    async def health(
        request: fastapi.Request,
        _auth: _SecretDep,
    ) -> dict[str, object]:
        """Return scheduler health with watchdog heartbeat information.

        ``status`` is ``"ok"`` when the loop is running and the heartbeat
        is fresh; ``"degraded"`` when the heartbeat age exceeds 60 s.
        """
        uptime = time.monotonic() - _SERVER_START_TIME
        snap = container.progress_bus.snapshot()
        # Exclude terminal statuses from the active-download count so that
        # a cancelled (or just-completed) task doesn't inflate the counter
        # during the 1-second window before finish() fires.
        _TERMINAL_STATUSES = frozenset({'已取消', '下載完成', '任務失敗, 等待重啓'})
        active_downloads = sum(
            1 for entry in snap.values() if entry.status not in _TERMINAL_STATUSES and entry.finished_at is None
        )

        # Retrieve watchdog from app state (set by lifespan).
        watchdog = getattr(request.app.state, 'watchdog', None)
        update_loop_thread = getattr(request.app.state, 'update_loop_thread', None)

        last_heartbeat_age = watchdog.last_beat_age_seconds() if watchdog is not None else None
        update_loop_running = update_loop_thread is not None and update_loop_thread.is_alive()

        # Degraded if heartbeat is stale (> 60 s) or the loop thread has died.
        heartbeat_stale = last_heartbeat_age is not None and last_heartbeat_age > 60
        status: str = 'degraded' if (heartbeat_stale or not update_loop_running) else 'ok'

        result: dict[str, object] = {
            'status': status,
            'uptime_seconds': int(uptime),
            'active_downloads': active_downloads,
            'update_loop_running': update_loop_running,
        }
        if last_heartbeat_age is not None:
            result['last_heartbeat_age_seconds'] = round(last_heartbeat_age, 1)
        return result

    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def serve() -> None:
    """Entry point for ``uv run anigamerplus-scheduler``."""
    container = build_container()
    app = build_scheduler_app(container)
    port = int(os.environ.get('ANIGAMERPLUS_SCHEDULER_PORT', '5001'))
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='info')


if __name__ == '__main__':
    serve()

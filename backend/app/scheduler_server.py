"""Internal FastAPI server for the scheduler process.

Listens on the configured internal address (default 127.0.0.1:5001, override
via ANIGAMERPLUS_SCHEDULER_HOST/PORT) and exposes a single health route at
/internal/health protected by a shared-secret header.

The scheduler container runs two processes in parallel:
- The dramatiq worker (started externally via ``dramatiq app.tasks ...``)
- This uvicorn server that hosts ``/internal/health`` and drives APScheduler

Entry point: ``anigamerplus-scheduler``
"""

from __future__ import annotations

import collections.abc
import contextlib
import logging.config
import os
import secrets
import time
import typing as T

import fastapi
import uvicorn

from .core import Container, build_container
from .log_config import build_log_config, get_ring_buffer_handler
from .persistence.paths import WorkspacePaths

if T.TYPE_CHECKING:
    from .scheduler.aps_scheduler import ApsScheduler

# ---------------------------------------------------------------------------
# Shared-secret resolution
# ---------------------------------------------------------------------------

_SECRET_ENV_VAR = 'ANIGAMERPLUS_INTERNAL_SECRET'
_RESOLVED_SECRET: str | None = None

# ---------------------------------------------------------------------------
# WebSocket keepalive-ping tuning
# ---------------------------------------------------------------------------

#: Env-vars that override uvicorn's WebSocket keepalive ping cadence. Same
#: rationale as ``app.main`` — this process also serves an HTTP/WS surface
#: (``/internal/health``) behind the internal docker network, and matching
#: the API process's forgiving keepalive avoids the same 1011 ERROR-level
#: traceback spam under a slow/buffering hop. See ``app.main`` for details.
WS_PING_INTERVAL_ENV_VAR = 'ANIGAMERPLUS_WS_PING_INTERVAL'
WS_PING_TIMEOUT_ENV_VAR = 'ANIGAMERPLUS_WS_PING_TIMEOUT'
_DEFAULT_WS_PING_INTERVAL = 30.0
_DEFAULT_WS_PING_TIMEOUT = 60.0


def _ws_ping_interval() -> float:
    """Return the WS ping interval in seconds (env-overridable, default 30.0)."""
    raw = os.environ.get(WS_PING_INTERVAL_ENV_VAR, '')
    return float(raw) if raw else _DEFAULT_WS_PING_INTERVAL


def _ws_ping_timeout() -> float:
    """Return the WS ping timeout in seconds (env-overridable, default 60.0)."""
    raw = os.environ.get(WS_PING_TIMEOUT_ENV_VAR, '')
    return float(raw) if raw else _DEFAULT_WS_PING_TIMEOUT


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
# App factory
# ---------------------------------------------------------------------------

_SERVER_START_TIME: float = time.monotonic()


def build_scheduler_app(container: Container) -> fastapi.FastAPI:
    """Build the internal FastAPI app.

    Listens on the configured internal address (default 127.0.0.1:5001,
    override via ANIGAMERPLUS_SCHEDULER_HOST/PORT).
    """

    @contextlib.asynccontextmanager
    async def _lifespan(app: fastapi.FastAPI) -> collections.abc.AsyncIterator[None]:
        from .scheduler.aps_scheduler import ApsScheduler

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

        _get_internal_secret()
        env_set = bool(os.environ.get(_SECRET_ENV_VAR, ''))
        if not env_set:
            container.logger.error(
                None,
                'Scheduler',
                (
                    'ANIGAMERPLUS_INTERNAL_SECRET not set — using generated ephemeral secret. '
                    'Set the env var for stable inter-process auth.'
                ),
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

        # Ghost-task reconciliation: a scheduler killed mid-BT-landing or
        # mid-TG-download leaves its live ProgressBus/Redis-mirror entry
        # stuck non-terminal even though the DB row (bt_feed_entry /
        # tg_downloaded_media) already reflects the real, finished outcome.
        # See BtProgressReconciler's docstring for the full story. Never
        # allowed to block boot — any failure here (Redis hiccup, DB error)
        # is swallowed so the scheduler still starts.
        bt_progress_reconciler = getattr(container, 'bt_progress_reconciler', None)
        if bt_progress_reconciler is not None:
            with contextlib.suppress(Exception):
                await bt_progress_reconciler.reconcile_on_boot()

        container.logger.info(None, 'Scheduler', 'Starting APScheduler…')
        aps = ApsScheduler(container.settings_repo)
        aps.start()
        app.state.aps = aps

        try:
            yield
        finally:
            container.logger.info(None, 'Scheduler', 'Stopping APScheduler…')
            aps.stop()

    app = fastapi.FastAPI(
        title='aniGamerPlus Scheduler (internal)',
        version='2.0.0',
        lifespan=_lifespan,
    )

    router = fastapi.APIRouter(prefix='/internal')

    # ---- GET /internal/health ----------------------------------------------

    @router.get('/health')
    async def health(
        request: fastapi.Request,
        _auth: _SecretDep,
    ) -> dict[str, object]:
        """Return scheduler health.

        ``status`` is ``"ok"`` when the APScheduler is running; ``"degraded"``
        otherwise.
        """
        uptime = time.monotonic() - _SERVER_START_TIME

        aps: ApsScheduler | None = getattr(request.app.state, 'aps', None)
        aps_running = aps is not None and aps._scheduler.running

        status: str = 'ok' if aps_running else 'degraded'

        return {
            'status': status,
            'uptime_seconds': int(uptime),
            'aps_running': aps_running,
        }

    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def serve() -> None:
    """Entry point for ``uv run anigamerplus-scheduler``."""
    # Apply our log config BEFORE build_container so Alembic migration log
    # lines are captured by our handlers rather than the root logger default.
    _paths = WorkspacePaths.detect()
    logging.config.dictConfig(build_log_config(_paths, save_logs=True, quantity_of_logs=7))
    container = build_container()
    app = build_scheduler_app(container)
    host = os.environ.get('ANIGAMERPLUS_SCHEDULER_HOST', '127.0.0.1')
    port = int(os.environ.get('ANIGAMERPLUS_SCHEDULER_PORT', '5001'))
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level='info',
        log_config=build_log_config(_paths, save_logs=True, quantity_of_logs=7),
        ws_ping_interval=_ws_ping_interval(),
        ws_ping_timeout=_ws_ping_timeout(),
    )


if __name__ == '__main__':
    serve()

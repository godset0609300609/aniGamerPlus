"""FastAPI application entry point for the aniGamerPlus dashboard."""

from __future__ import annotations

import asyncio
import collections.abc
import contextlib
import logging.config
import os
import secrets
import threading

import fastapi
import fastapi.middleware.cors
import starlette.middleware.sessions
import uvicorn

from .api import router as api_router
from .api.auth_api import router as auth_router
from .core import Container, build_container
from .log_config import LogFileTailer, build_log_config, get_ring_buffer_handler
from .persistence.paths import WorkspacePaths

#: Env-var that, when set to anything truthy, disables the background
#: scheduler thread spawned by the FastAPI lifespan hook. Used by the
#: pytest ``client`` fixture and by tooling that only wants to poke at
#: the HTTP surface without starting the periodic downloader.
DISABLE_SCHEDULER_ENV_VAR = 'ANIGAMERPLUS_DISABLE_SCHEDULER'


def _scheduler_disabled() -> bool:
    """Return True when the env-var opt-out is set to anything non-empty."""
    value = os.environ.get(DISABLE_SCHEDULER_ENV_VAR, '')
    return bool(value) and value != '0'


class DashboardApp:
    """Factory + runtime wrapper around the FastAPI application."""

    DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = (
        'http://localhost:5173',
        'http://127.0.0.1:5173',
        'http://localhost:4173',
    )

    def __init__(
        self,
        container: Container,
        *,
        cors_origins: list[str] | None = None,
    ) -> None:
        self._container = container
        self._cors_origins = cors_origins or self._env_cors_origins()
        self._app = self._build_app()

    # -- public --------------------------------------------------------------

    @property
    def app(self) -> fastapi.FastAPI:
        return self._app

    @property
    def container(self) -> Container:
        return self._container

    def run(self) -> None:
        settings = self._container.settings_repo.load()
        dashboard = settings.dashboard
        host = dashboard.host or '0.0.0.0'
        port = int(dashboard.port or 5000)

        ssl_certfile: str | None = None
        ssl_keyfile: str | None = None
        if dashboard.SSL:
            cert = self._container.paths.ssl_cert_path
            key = self._container.paths.ssl_key_path
            if not cert.exists() or not key.exists():
                raise FileNotFoundError(
                    'dashboard.SSL is enabled but cert / key files are missing. '
                    f'Expected {cert} and {key}. Provide your own certificate '
                    '(self-signed cert / key pair, or put a real cert there) '
                    'or front the server with a reverse proxy that terminates TLS.'
                )
            ssl_certfile = str(cert)
            ssl_keyfile = str(key)

        uvicorn.run(
            'app.main:app',
            host=host,
            port=port,
            log_level='info',
            log_config=build_log_config(
                self._container.paths,
                save_logs=settings.save_logs,
                quantity_of_logs=settings.quantity_of_logs,
            ),
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
        )

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _env_cors_origins() -> list[str]:
        env = os.environ.get('ANIGAMERPLUS_CORS_ORIGINS')
        if env:
            return [o.strip() for o in env.split(',') if o.strip()]
        return list(DashboardApp.DEFAULT_ALLOWED_ORIGINS)

    def _build_app(self) -> fastapi.FastAPI:
        app = fastapi.FastAPI(
            title='aniGamerPlus Dashboard API',
            version='2.0.0',
            description='FastAPI backend for the aniGamerPlus 動畫瘋下載器 控制臺.',
            lifespan=self._lifespan,
        )

        # Session middleware must be added before CORS so the session cookie
        # is available on every request regardless of origin header.
        session_secret = self._resolve_session_secret()
        app.add_middleware(
            starlette.middleware.sessions.SessionMiddleware,
            secret_key=session_secret,
            same_site='lax',
            https_only=False,
        )

        app.add_middleware(
            fastapi.middleware.cors.CORSMiddleware,
            allow_origins=self._cors_origins,
            allow_credentials=True,
            allow_methods=['*'],
            allow_headers=['*'],
        )
        app.include_router(auth_router)
        app.include_router(api_router)
        return app

    def _resolve_session_secret(self) -> str:
        """Return a stable session secret, generating + persisting one if needed.

        If ``settings.auth.session_secret`` is empty we generate a random
        secret and persist it to ``config.json`` so it survives restarts.
        An ephemeral secret on every boot would invalidate all browser
        sessions on restart.
        """
        settings = self._container.settings_repo.load()
        if settings.auth.session_secret:
            return settings.auth.session_secret

        new_secret = secrets.token_urlsafe(32)
        updated_auth = settings.auth.model_copy(update={'session_secret': new_secret})
        updated = settings.model_copy(update={'auth': updated_auth})
        self._container.settings_repo.save(updated)
        return new_secret

    @contextlib.asynccontextmanager
    async def _lifespan(self, app: fastapi.FastAPI) -> collections.abc.AsyncIterator[None]:
        """Start the scheduler proxy WS subscription on boot; clean up on shutdown.

        In the multi-process deployment (docker-compose), the API process
        does not run the UpdateLoop — that is the scheduler process's job.
        Instead the lifespan launches a background asyncio task that
        maintains a WebSocket subscription to the scheduler and keeps a
        cached progress snapshot in memory.

        The legacy single-process path (``ANIGAMERPLUS_DISABLE_SCHEDULER``
        env-var unset, no scheduler proxy) still works: if the proxy's
        ``is_scheduler_up()`` returns False, ``TaskService.enqueue`` falls
        back to calling the ManualRunner in-process; and ProgressService
        reads from the local ProgressBus directly.

        For backwards compatibility during the transition, if
        ``ANIGAMERPLUS_DISABLE_SCHEDULER`` is *not* set and no scheduler
        process is configured, the old daemon thread is still spawned so
        the single-process CLI experience keeps working.
        """
        logger = self._container.logger

        # Bootstrap the ring buffer from the on-disk log file so the /logs
        # page shows history even after a restart.  Must run before anything
        # else emits log records so the file-sourced entries appear first.
        ring_handler = get_ring_buffer_handler()
        _n = ring_handler.bootstrap_from_file(self._container.paths.logs_dir)
        if _n > 0:
            logger.info(
                None,
                'Bootstrap',
                f'從 log 檔載入最近 {_n} 筆歷史訊息',
                display=False,
            )

        # Start the log-file tailer so that new lines written by the
        # Scheduler process (which shares the same log file) are pushed
        # into the ring buffer and fan-out to WebSocket subscribers.
        # The tailer starts positioned at EOF (bootstrap already loaded
        # historical content), so only genuinely new lines are injected.
        tailer = LogFileTailer(self._container.paths.logs_dir, ring_handler)
        tailer.start()

        proxy = getattr(self._container, 'scheduler_proxy', None)

        # Always start the proxy WS subscription if a proxy is wired.
        # The proxy subscription is independent of the scheduler env-var:
        # the env-var only controls whether to spawn the in-process UpdateLoop.
        proxy_task: asyncio.Task[None] | None = None
        if proxy is not None:
            proxy_task = asyncio.create_task(
                proxy.run_progress_subscription(),
                name='scheduler-proxy-ws',
            )
            # Expose the proxy on app.state so health.py can fetch scheduler health.
            app.state.scheduler_proxy = proxy
            logger.info(
                None,
                'API Process',
                'Started scheduler proxy WebSocket subscription',
                display=False,
            )

        if _scheduler_disabled():
            logger.info(
                None,
                'API Process',
                f'已略過背景排程 ({DISABLE_SCHEDULER_ENV_VAR} 已設定)',
                display=False,
            )
        elif proxy is None and getattr(app.state, 'scheduler_thread', None) is None:
            # No proxy wired and scheduler not disabled → fallback to in-process
            # UpdateLoop (single-process / legacy compatibility).
            thread = self._spawn_scheduler_thread()
            app.state.scheduler_thread = thread
            settings = self._container.settings_repo.load()
            logger.info(
                None,
                '自動下載排程',
                f'已啟動背景排程（每 {settings.check_frequency} 分鐘掃一次）',
            )

        try:
            yield
        finally:
            tailer.stop()
            if proxy_task is not None:
                proxy_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await proxy_task
                if proxy is not None:
                    await proxy.close()
            logger.info(
                None,
                'API Process',
                '伺服器關閉中',
                display=False,
            )

    def _spawn_scheduler_thread(self) -> threading.Thread:
        """Build the :class:`UpdateLoop` and hand it to a daemon thread."""
        loop = self._container.build_update_loop()
        thread = threading.Thread(
            target=loop.run_forever,
            name='anigamerplus-scheduler',
            daemon=True,
        )
        thread.start()
        return thread


def create_app(container: Container | None = None) -> fastapi.FastAPI:
    """Convenience factory kept for tests and scripting.

    Builds a container on first call unless one is supplied; tests pass a
    :class:`~app.core.Container` stand-in so side effects stay scoped to
    the test.
    """
    if container is None:
        container = build_container()
    return DashboardApp(container).app


app: fastapi.FastAPI = create_app()


def serve() -> None:
    """Entry point for ``uv run anigamerplus-api`` / ``anigamerplus-server``."""
    # Apply our log config BEFORE build_container so Alembic migration log
    # lines are captured by our handlers rather than the root logger default.
    _paths = WorkspacePaths.detect()
    logging.config.dictConfig(build_log_config(_paths, save_logs=True, quantity_of_logs=7))
    DashboardApp(build_container()).run()


if __name__ == '__main__':
    serve()

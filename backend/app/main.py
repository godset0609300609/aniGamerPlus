"""FastAPI application entry point for the aniGamerPlus dashboard."""

from __future__ import annotations

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

from . import rate_limit
from .api import router as api_router
from .api.auth_api import router as auth_router
from .api.profile_telegram_api import router as profile_telegram_router
from .api.telegram_admin import router as telegram_admin_router
from .api.telegram_webhook import router as telegram_webhook_router
from .core import Container, build_container
from .log_config import LogFileTailer, build_log_config, get_ring_buffer_handler
from .persistence.paths import WorkspacePaths
from .services.telegram_client_cache import close_telegram_client_cache

_log = logging.getLogger(__name__)

#: Env-var that, unset (or not "1"), makes :meth:`DashboardApp.run` refuse to
#: silently expose an unauthenticated dashboard on a non-loopback bind. Set to
#: "1" to acknowledge the risk and suppress the startup warning.
ALLOW_UNAUTH_PUBLIC_BIND_ENV_VAR = 'ANIGAMERPLUS_ALLOW_UNAUTH_PUBLIC_BIND'

#: Env-var that, when set to anything truthy, disables the background
#: scheduler thread spawned by the FastAPI lifespan hook. Used by the
#: pytest ``client`` fixture and by tooling that only wants to poke at
#: the HTTP surface without starting the periodic downloader.
DISABLE_SCHEDULER_ENV_VAR = 'ANIGAMERPLUS_DISABLE_SCHEDULER'

#: Env-var that controls the session cookie's ``Secure`` flag. Defaults to
#: "0" (cookie sent over plain HTTP too) for backward compat with the
#: current internal-HTTP deployment. Set to "1" once the dashboard is
#: served over HTTPS (direct TLS or a TLS-terminating reverse proxy) so the
#: session cookie is never sent in the clear.
HTTPS_ONLY_ENV_VAR = 'ANIGAMERPLUS_HTTPS_ONLY'

#: Env-vars that override uvicorn's WebSocket keepalive ping cadence.
#: uvicorn's defaults (ping every 20s, drop the connection if no pong
#: arrives within 20s) are too aggressive behind a public reverse proxy
#: (Cloudflare, nginx) — a briefly backgrounded browser tab or a proxy that
#: buffers a frame for a couple seconds is enough to blow past 20s and
#: trigger a 1011 "keepalive ping timeout" close, logged as an ERROR with a
#: full traceback. Widening both knobs reduces (but, being inherent to
#: WebSocket + browser tab-backgrounding behaviour, cannot eliminate) how
#: often that happens.
WS_PING_INTERVAL_ENV_VAR = 'ANIGAMERPLUS_WS_PING_INTERVAL'
WS_PING_TIMEOUT_ENV_VAR = 'ANIGAMERPLUS_WS_PING_TIMEOUT'
_DEFAULT_WS_PING_INTERVAL = 30.0
_DEFAULT_WS_PING_TIMEOUT = 60.0


def _scheduler_disabled() -> bool:
    """Return True when the env-var opt-out is set to anything non-empty."""
    value = os.environ.get(DISABLE_SCHEDULER_ENV_VAR, '')
    return bool(value) and value != '0'


def _https_only() -> bool:
    """Return True when the session cookie should be marked ``Secure``.

    Defaults to False (matches the previous hardcoded behaviour) so
    existing internal-HTTP deployments keep working without config
    changes; opt in by setting ``ANIGAMERPLUS_HTTPS_ONLY=1``.
    """
    return os.environ.get(HTTPS_ONLY_ENV_VAR, '0') != '0'


def _ws_ping_interval() -> float:
    """Return the WS ping interval in seconds (env-overridable, default 30.0)."""
    raw = os.environ.get(WS_PING_INTERVAL_ENV_VAR, '')
    return float(raw) if raw else _DEFAULT_WS_PING_INTERVAL


def _ws_ping_timeout() -> float:
    """Return the WS ping timeout in seconds (env-overridable, default 60.0)."""
    raw = os.environ.get(WS_PING_TIMEOUT_ENV_VAR, '')
    return float(raw) if raw else _DEFAULT_WS_PING_TIMEOUT


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

        self._warn_if_unauth_public_bind(settings.auth.enabled, host)

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
            ws_ping_interval=_ws_ping_interval(),
            ws_ping_timeout=_ws_ping_timeout(),
        )

    # -- internals -----------------------------------------------------------

    _LOOPBACK_HOSTS: frozenset[str] = frozenset({'127.0.0.1', 'localhost'})

    @staticmethod
    def _env_cors_origins() -> list[str]:
        env = os.environ.get('ANIGAMERPLUS_CORS_ORIGINS')
        if env:
            return [o.strip() for o in env.split(',') if o.strip()]
        return list(DashboardApp.DEFAULT_ALLOWED_ORIGINS)

    @classmethod
    def _warn_if_unauth_public_bind(cls, auth_enabled: bool, host: str) -> None:
        """Warn (loudly) when the dashboard is unauthenticated and bound off-loopback.

        ``auth.enabled=False`` is a reasonable default for a purely local
        install, but combined with a non-loopback ``host`` it means anyone
        who can reach the machine gets unauthenticated admin access. Set
        ``ANIGAMERPLUS_ALLOW_UNAUTH_PUBLIC_BIND=1`` to acknowledge the risk
        and silence this warning.
        """
        if auth_enabled or host in cls._LOOPBACK_HOSTS:
            return
        if os.environ.get(ALLOW_UNAUTH_PUBLIC_BIND_ENV_VAR, '') == '1':
            return
        _log.warning(
            'auth.enabled=False and dashboard.host=%r is not loopback — the '
            'dashboard is reachable WITHOUT authentication from anything that '
            'can route to this host. Enable auth, bind to 127.0.0.1, or set '
            '%s=1 to acknowledge this and suppress the warning.',
            host,
            ALLOW_UNAUTH_PUBLIC_BIND_ENV_VAR,
        )

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
            https_only=_https_only(),
        )

        # CORS credentials + a wildcard origin is a browser-enforced
        # contradiction (credentialed requests silently fail), but a proxy or
        # non-browser client would happily send credentials to any origin —
        # refuse to boot with that combination rather than fail confusingly
        # or silently over-trust every origin.
        allow_credentials = True
        if allow_credentials and '*' in self._cors_origins:
            raise RuntimeError(
                'ANIGAMERPLUS_CORS_ORIGINS cannot be "*" while credentials are allowed — refusing to start.'
            )

        app.add_middleware(
            fastapi.middleware.cors.CORSMiddleware,
            allow_origins=self._cors_origins,
            allow_credentials=allow_credentials,
            allow_methods=['*'],
            allow_headers=['*'],
        )
        rate_limit.install(app)
        app.include_router(auth_router)
        app.include_router(telegram_webhook_router)
        app.include_router(telegram_admin_router)
        app.include_router(profile_telegram_router)
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
        """Bootstrap logging and the background scheduler on boot; clean up on shutdown.

        In the multi-process deployment (docker-compose), the API process
        does not run the UpdateLoop — that is the scheduler process's job,
        driven independently via ``ANIGAMERPLUS_DISABLE_SCHEDULER``.

        In the single-process / CLI deployment, ``ANIGAMERPLUS_DISABLE_SCHEDULER``
        is unset, so the in-process ``UpdateLoop`` daemon thread is spawned here.
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

        if _scheduler_disabled():
            logger.info(
                None,
                'API Process',
                f'已略過背景排程 ({DISABLE_SCHEDULER_ENV_VAR} 已設定)',
                display=False,
            )
        elif getattr(app.state, 'scheduler_thread', None) is None:
            # Scheduler not disabled → run the in-process UpdateLoop
            # (single-process / CLI deployment).
            thread = self._spawn_scheduler_thread()
            app.state.scheduler_thread = thread
            settings = self._container.settings_repo.load()
            logger.info(
                None,
                '自動下載排程',
                f'已啟動背景排程（每 {settings.check_frequency} 分鐘掃一次）',
            )

        # Reconnect every active Telegram User API session and register its
        # download watcher. Only the API process runs live hydrogram clients
        # (mirrors the telegram_client / scheduler split above) — best-effort,
        # a startup failure here must not prevent the API from serving.
        tg_service = getattr(self._container, 'tg_service', None)
        if tg_service is not None:
            try:
                await tg_service.startup()
            except Exception as exc:  # noqa: BLE001
                logger.error(None, 'Bootstrap', f'Telegram User API 啟動失敗: {exc}', display=False)

        try:
            yield
        finally:
            tailer.stop()
            # Close the dynamic API-side TelegramClient cache (used by admin
            # endpoints and webhook route).  The scheduler-side client stored
            # in container.telegram_client is closed separately below.
            await close_telegram_client_cache()
            tg_client = getattr(self._container, 'telegram_client', None)
            if tg_client is not None:
                await tg_client.close()
            if tg_service is not None:
                with contextlib.suppress(Exception):
                    await tg_service.shutdown()
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

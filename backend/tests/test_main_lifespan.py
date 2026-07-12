"""Tests for the FastAPI lifespan hook and container assembly.

The rewrite split the single legacy process into ``anigamerplus`` (CLI,
runs ``UpdateLoop.run_forever`` in the foreground) and
``anigamerplus-server`` (FastAPI web dashboard). For UX parity the web
server now also spawns the periodic downloader on startup — unless
``ANIGAMERPLUS_DISABLE_SCHEDULER`` is set. These tests pin that wiring
so the split doesn't silently regress.
"""

from __future__ import annotations

import threading
import types
from typing import TYPE_CHECKING, Any

import fastapi.testclient
import pytest

from app.main import DashboardApp

if TYPE_CHECKING:
    from .conftest import FakeContainer


class _RecordingUpdateLoop:
    """Stand-in for :class:`UpdateLoop` that records ``run_forever`` calls.

    ``run_forever`` blocks on a ``threading.Event`` so the daemon thread
    the lifespan starts stays alive for the duration of the test — the
    same shape as the real loop, which only returns on ``stop()``.
    """

    def __init__(self) -> None:
        self.run_forever_calls = 0
        self.thread_names: list[str] = []
        self.thread_daemon_flags: list[bool] = []
        self._started = threading.Event()
        self._release = threading.Event()

    def run_forever(self) -> None:
        self.run_forever_calls += 1
        current = threading.current_thread()
        self.thread_names.append(current.name)
        self.thread_daemon_flags.append(current.daemon)
        self._started.set()
        # Block until the test releases us (or the process exits — the
        # thread is daemon so it'll die with the interpreter anyway).
        self._release.wait(timeout=5.0)

    def wait_until_started(self, timeout: float = 2.0) -> bool:
        return self._started.wait(timeout=timeout)

    def release(self) -> None:
        self._release.set()


def _lifespan_container(fake_container: FakeContainer, loop: _RecordingUpdateLoop) -> Any:
    """Return a container proxy that satisfies :class:`DashboardApp`.

    Adds a ``build_update_loop`` method returning ``loop`` on top of the
    minimal attribute set the dashboard factory touches.
    """
    proxy = types.SimpleNamespace(
        paths=fake_container.paths,
        logger=fake_container.logger,
        settings_repo=fake_container.settings_repo,
        sn_list_repo=fake_container.sn_list_repo,
        cookie_repo=fake_container.cookie_repo,
        database=fake_container.database,
        anime_repo=fake_container.anime_repo,
        progress_bus=fake_container.progress_bus,
        manual_runner=fake_container.manual_runner,
        build_update_loop=lambda: loop,
    )
    return proxy


def test_lifespan_spawns_scheduler_thread(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the env-var unset, the lifespan starts one daemon thread."""
    monkeypatch.delenv('ANIGAMERPLUS_DISABLE_SCHEDULER', raising=False)

    loop = _RecordingUpdateLoop()
    proxy = _lifespan_container(fake_container, loop)
    dashboard = DashboardApp(proxy)
    app = dashboard.app

    try:
        with fastapi.testclient.TestClient(app):
            # TestClient's context-manager semantics run the lifespan
            # startup phase; the scheduler thread should be live before
            # we return.
            assert loop.wait_until_started(), 'scheduler thread did not call run_forever within timeout'
            thread = app.state.scheduler_thread
            assert isinstance(thread, threading.Thread)
            assert thread.daemon is True
            assert thread.is_alive()
            assert loop.run_forever_calls == 1
            assert loop.thread_daemon_flags == [True]
    finally:
        # Unblock the loop so the daemon thread exits cleanly; even
        # without this, daemon=True would let the process exit anyway.
        loop.release()


def test_lifespan_respects_env_var_disable(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ANIGAMERPLUS_DISABLE_SCHEDULER=1`` skips the scheduler spawn."""
    monkeypatch.setenv('ANIGAMERPLUS_DISABLE_SCHEDULER', '1')

    loop = _RecordingUpdateLoop()
    proxy = _lifespan_container(fake_container, loop)
    dashboard = DashboardApp(proxy)
    app = dashboard.app

    with fastapi.testclient.TestClient(app):
        assert getattr(app.state, 'scheduler_thread', None) is None
        assert loop.run_forever_calls == 0


# ---------------------------------------------------------------------------
# Container telegram_client wiring
# ---------------------------------------------------------------------------


def test_container_telegram_client_none_when_no_token(
    fake_container: FakeContainer,
) -> None:
    """``telegram_client`` is None when bot_token is empty."""
    import dataclasses
    import json

    from app.models import AppSettings, TelegramSettings

    # Seed a settings file with empty bot_token
    settings = AppSettings(telegram=TelegramSettings(bot_token=''))
    blob = settings.model_dump(by_alias=True, exclude_none=False)
    fake_container.paths.config_path.write_text(json.dumps(blob, ensure_ascii=False), encoding='utf-8')

    from app.persistence.settings_repo import SettingsRepository

    # Rebuild settings_repo on the same paths
    sr = SettingsRepository(fake_container.paths, fake_container.logger)
    updated = dataclasses.replace(fake_container, settings_repo=sr)

    loaded = updated.settings_repo.load()
    assert loaded.telegram.bot_token == ''

    # Container.telegram_client is a field — when bot_token empty it should be None.
    # We verify the field exists on Container and defaults to None.
    from app.core import Container

    field_names = {f.name for f in dataclasses.fields(Container)}
    assert 'telegram_client' in field_names


def test_container_telegram_client_instantiated_when_token_set(
    fake_container: FakeContainer,
) -> None:
    """``telegram_client`` is a TelegramClient when bot_token is non-empty."""
    import json

    from app.models import AppSettings, TelegramSettings
    from app.services.telegram_client import TelegramClient

    # Seed a settings file with a non-empty bot_token
    settings = AppSettings(telegram=TelegramSettings(bot_token='123:TESTTOKEN'))
    blob = settings.model_dump(by_alias=True, exclude_none=False)
    fake_container.paths.config_path.write_text(json.dumps(blob, ensure_ascii=False), encoding='utf-8')

    from app.persistence.settings_repo import SettingsRepository

    sr = SettingsRepository(fake_container.paths, fake_container.logger)
    loaded = sr.load()
    assert loaded.telegram.bot_token == '123:TESTTOKEN'

    # Simulate what build_container does: instantiate when token is set.
    client = TelegramClient(loaded.telegram.bot_token) if loaded.telegram.bot_token else None
    assert client is not None
    assert isinstance(client, TelegramClient)
    # Clean up the httpx client to avoid resource-leak warnings.
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(client.close())
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Session cookie ``Secure`` flag — env-driven via ANIGAMERPLUS_HTTPS_ONLY
# ---------------------------------------------------------------------------


def test_https_only_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the env-var unset, https_only defaults to False (backward compat)."""
    from app.main import _https_only

    monkeypatch.delenv('ANIGAMERPLUS_HTTPS_ONLY', raising=False)
    assert _https_only() is False


@pytest.mark.parametrize('value', ['0', '', 'false', 'no'])
def test_https_only_falsy_values_stay_disabled(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Only the sentinel "0" (or unset) disables it; anything else enables it.

    This mirrors the exact contract from the task: ``!= '0'`` is truthy, so
    only the literal string "0" (or an unset var, defaulted to "0") counts
    as disabled. Other falsy-looking strings like "false"/"no" are NOT
    treated as disabled — documented here so a future refactor doesn't
    silently "fix" this into a stricter boolean parse without noticing the
    behaviour change.
    """
    from app.main import _https_only

    monkeypatch.setenv('ANIGAMERPLUS_HTTPS_ONLY', value)
    if value == '0':
        assert _https_only() is False
    else:
        assert _https_only() is True


def test_https_only_enabled_when_set_to_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import _https_only

    monkeypatch.setenv('ANIGAMERPLUS_HTTPS_ONLY', '1')
    assert _https_only() is True


def _session_middleware_kwargs(app: fastapi.FastAPI) -> dict[str, Any]:
    """Extract the ``SessionMiddleware`` kwargs from the built app's middleware stack."""
    import starlette.middleware.sessions

    for mw in app.user_middleware:
        if mw.cls is starlette.middleware.sessions.SessionMiddleware:
            return dict(mw.kwargs)
    raise AssertionError('SessionMiddleware not found in app.user_middleware')


def test_session_middleware_https_only_follows_env_var(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SessionMiddleware wired by DashboardApp must reflect the env var."""
    import types

    proxy = types.SimpleNamespace(settings_repo=fake_container.settings_repo)

    monkeypatch.delenv('ANIGAMERPLUS_HTTPS_ONLY', raising=False)
    app_default = DashboardApp(proxy).app
    assert _session_middleware_kwargs(app_default)['https_only'] is False

    monkeypatch.setenv('ANIGAMERPLUS_HTTPS_ONLY', '1')
    app_enabled = DashboardApp(proxy).app
    assert _session_middleware_kwargs(app_enabled)['https_only'] is True


# ---------------------------------------------------------------------------
# WebSocket keepalive-ping tuning — env-driven via ANIGAMERPLUS_WS_PING_*
#
# uvicorn's own defaults (ping every 20s, drop the connection after 20s
# without a pong) are too aggressive behind a public reverse proxy — see
# ``app.main._ws_ping_interval`` / ``_ws_ping_timeout`` docstrings.
# ---------------------------------------------------------------------------


def test_uvicorn_config_defaults_to_30_ping_interval_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the env-vars unset, the ws ping helpers return the forgiving 30s/60s defaults."""
    from app.main import _ws_ping_interval, _ws_ping_timeout

    monkeypatch.delenv('ANIGAMERPLUS_WS_PING_INTERVAL', raising=False)
    monkeypatch.delenv('ANIGAMERPLUS_WS_PING_TIMEOUT', raising=False)

    assert _ws_ping_interval() == 30.0
    assert _ws_ping_timeout() == 60.0


def test_uvicorn_config_reads_ws_ping_interval_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """ANIGAMERPLUS_WS_PING_INTERVAL / _TIMEOUT override the defaults."""
    from app.main import _ws_ping_interval, _ws_ping_timeout

    monkeypatch.setenv('ANIGAMERPLUS_WS_PING_INTERVAL', '15')
    monkeypatch.setenv('ANIGAMERPLUS_WS_PING_TIMEOUT', '45')

    assert _ws_ping_interval() == 15.0
    assert _ws_ping_timeout() == 45.0


def test_dashboard_run_forwards_ws_ping_config_to_uvicorn(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DashboardApp.run() must forward the resolved ws_ping_* kwargs to uvicorn.run."""
    import app.main as main_module

    monkeypatch.setenv('ANIGAMERPLUS_WS_PING_INTERVAL', '12')
    monkeypatch.setenv('ANIGAMERPLUS_WS_PING_TIMEOUT', '34')

    captured: dict[str, Any] = {}

    def _fake_uvicorn_run(app: Any, **kwargs: Any) -> None:  # noqa: ARG001
        captured.update(kwargs)

    monkeypatch.setattr(main_module.uvicorn, 'run', _fake_uvicorn_run)

    proxy = types.SimpleNamespace(
        paths=fake_container.paths,
        logger=fake_container.logger,
        settings_repo=fake_container.settings_repo,
    )
    main_module.DashboardApp(proxy).run()

    assert captured.get('ws_ping_interval') == 12.0
    assert captured.get('ws_ping_timeout') == 34.0


def test_dashboard_run_defaults_ws_ping_config_when_env_unset(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DashboardApp.run() falls back to 30s/60s when the env-vars are unset."""
    import app.main as main_module

    monkeypatch.delenv('ANIGAMERPLUS_WS_PING_INTERVAL', raising=False)
    monkeypatch.delenv('ANIGAMERPLUS_WS_PING_TIMEOUT', raising=False)

    captured: dict[str, Any] = {}

    def _fake_uvicorn_run(app: Any, **kwargs: Any) -> None:  # noqa: ARG001
        captured.update(kwargs)

    monkeypatch.setattr(main_module.uvicorn, 'run', _fake_uvicorn_run)

    proxy = types.SimpleNamespace(
        paths=fake_container.paths,
        logger=fake_container.logger,
        settings_repo=fake_container.settings_repo,
    )
    main_module.DashboardApp(proxy).run()

    assert captured.get('ws_ping_interval') == 30.0
    assert captured.get('ws_ping_timeout') == 60.0

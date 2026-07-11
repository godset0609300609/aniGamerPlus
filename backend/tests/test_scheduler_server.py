"""Tests for the internal scheduler server (app/scheduler_server.py)."""

from __future__ import annotations

import collections.abc
import pathlib
import tempfile
import types
from typing import Any
from unittest import mock

import fastapi.testclient
import pytest

import app.scheduler_server as scheduler_server_module
from app.scheduler_server import build_scheduler_app

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeSettingsRepo:
    """Minimal SettingsRepository stub: load() returns an object with check_frequency."""

    def load(self) -> Any:
        return types.SimpleNamespace(check_frequency=5)


class _FakeApsScheduler:
    """Minimal ApsScheduler stub that avoids touching a real BackgroundScheduler."""

    def __init__(self) -> None:
        self._running = False

    @property
    def _scheduler(self) -> Any:
        return types.SimpleNamespace(running=self._running)

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False


def _fake_container() -> Any:
    """Build a SimpleNamespace container for scheduler_server tests."""
    from app.logging_ import Logger

    # Use a temporary directory for the logger (save_logs=False means no
    # file handler is attached, so the directory only needs to be a valid path).
    tmpdir = pathlib.Path(tempfile.gettempdir())
    logger = Logger(tmpdir, save_logs=False, quantity_of_logs=1)

    container = types.SimpleNamespace(
        logger=logger,
        paths=types.SimpleNamespace(logs_dir=tmpdir),
        settings_repo=_FakeSettingsRepo(),
        task_history_repo=None,  # getattr-protected in lifespan
    )
    return container


# ---------------------------------------------------------------------------
# Fixture: TestClient with a fixed secret
# ---------------------------------------------------------------------------

_TEST_SECRET = 'test-secret-12345'


@pytest.fixture()
def scheduler_client(monkeypatch: pytest.MonkeyPatch) -> collections.abc.Iterator[fastapi.testclient.TestClient]:
    """TestClient for the scheduler internal app with a fixed secret.

    Patches ApsScheduler so no real BackgroundScheduler is started.
    """
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    # Reset cached secret so monkeypatched env is picked up.
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    fake_aps = _FakeApsScheduler()
    monkeypatch.setattr('app.scheduler.aps_scheduler.ApsScheduler', lambda settings_repo: fake_aps)

    container = _fake_container()
    app = build_scheduler_app(container)  # type: ignore[arg-type]
    with fastapi.testclient.TestClient(app, raise_server_exceptions=True) as client:
        yield client


# ---------------------------------------------------------------------------
# /internal/health
# ---------------------------------------------------------------------------


def test_health_returns_ok(scheduler_client: fastapi.testclient.TestClient) -> None:
    r = scheduler_client.get(
        '/internal/health',
        headers={'X-Internal-Secret': _TEST_SECRET},
    )
    assert r.status_code == 200
    data = r.json()
    assert data['status'] == 'ok'
    assert 'uptime_seconds' in data
    assert 'aps_running' in data
    assert isinstance(data['uptime_seconds'], int)
    assert isinstance(data['aps_running'], bool)


def test_health_no_secret_returns_401(
    scheduler_client: fastapi.testclient.TestClient,
) -> None:
    r = scheduler_client.get('/internal/health')
    assert r.status_code == 401


def test_health_wrong_secret_returns_401(
    scheduler_client: fastapi.testclient.TestClient,
) -> None:
    r = scheduler_client.get(
        '/internal/health',
        headers={'X-Internal-Secret': 'wrong-secret'},
    )
    assert r.status_code == 401


def test_health_degraded_when_aps_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health is 'degraded' when the ApsScheduler is not running."""
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    # Provide an ApsScheduler stub that reports not running.
    class _StoppedAps:
        _scheduler = types.SimpleNamespace(running=False)

        def start(self) -> None:
            pass  # do not flip running

        def stop(self) -> None:
            pass

    monkeypatch.setattr('app.scheduler.aps_scheduler.ApsScheduler', lambda settings_repo: _StoppedAps())

    container = _fake_container()
    _app = build_scheduler_app(container)  # type: ignore[arg-type]
    with fastapi.testclient.TestClient(_app) as client:
        r = client.get(
            '/internal/health',
            headers={'X-Internal-Secret': _TEST_SECRET},
        )
        assert r.status_code == 200
        data = r.json()
        assert data['status'] == 'degraded'
        assert data['aps_running'] is False


def test_health_has_required_fields(scheduler_client: fastapi.testclient.TestClient) -> None:
    """Response must contain all expected top-level fields."""
    r = scheduler_client.get(
        '/internal/health',
        headers={'X-Internal-Secret': _TEST_SECRET},
    )
    assert r.status_code == 200
    data = r.json()
    required = {'status', 'uptime_seconds', 'aps_running'}
    assert required <= data.keys(), f'Missing fields: {required - data.keys()}'


# ---------------------------------------------------------------------------
# _lifespan() ghost-task reconciliation
# ---------------------------------------------------------------------------


def test_lifespan_awaits_bt_progress_reconciler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: reconcile_on_boot is async; the lifespan must await it.

    A previous version of ``_lifespan`` called
    ``bt_progress_reconciler.reconcile_on_boot()`` without ``await``. That
    creates the coroutine object, immediately discards it, and the
    reconciliation body never actually runs. Python only surfaces this as a
    ``RuntimeWarning`` ("coroutine ... was never awaited") emitted later
    (source ``<sys>:0``), which slips past the ``contextlib.suppress
    (Exception)`` wrapping around the call (a RuntimeWarning is not an
    Exception) and is easy to miss in test output.

    This drives the app through its real ASGI lifespan (the
    ``TestClient`` context manager) with an ``AsyncMock`` reconciler and
    asserts the coroutine was actually awaited — not just constructed —
    so a future regression to a bare (non-awaited) call fails loudly.
    """
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    fake_aps = _FakeApsScheduler()
    monkeypatch.setattr('app.scheduler.aps_scheduler.ApsScheduler', lambda settings_repo: fake_aps)

    container = _fake_container()
    mock_reconciler = mock.Mock()
    mock_reconciler.reconcile_on_boot = mock.AsyncMock(return_value=(0, 0))
    container.bt_progress_reconciler = mock_reconciler

    app = build_scheduler_app(container)  # type: ignore[arg-type]
    with fastapi.testclient.TestClient(app, raise_server_exceptions=True):
        pass

    mock_reconciler.reconcile_on_boot.assert_awaited_once()


# ---------------------------------------------------------------------------
# serve() host/port env var resolution
# ---------------------------------------------------------------------------


def test_serve_passes_custom_host_to_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    """serve() must forward ANIGAMERPLUS_SCHEDULER_HOST to uvicorn.run as host=."""
    import types as _types

    monkeypatch.setenv('ANIGAMERPLUS_SCHEDULER_HOST', '0.0.0.0')
    monkeypatch.setenv('ANIGAMERPLUS_SCHEDULER_PORT', '5001')
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    captured: dict[str, Any] = {}

    def _fake_uvicorn_run(app: Any, **kwargs: Any) -> None:  # noqa: ARG001
        captured.update(kwargs)

    fake_container = _fake_container()

    monkeypatch.setattr(scheduler_server_module, 'build_container', lambda: fake_container)
    monkeypatch.setattr(scheduler_server_module.uvicorn, 'run', _fake_uvicorn_run)

    # WorkspacePaths.detect() is called inside serve(); provide a minimal stub.
    import app.persistence.paths as _paths_mod

    fake_paths = _types.SimpleNamespace(logs_dir=pathlib.Path(tempfile.gettempdir()))
    monkeypatch.setattr(_paths_mod.WorkspacePaths, 'detect', staticmethod(lambda: fake_paths))

    scheduler_server_module.serve()

    assert captured.get('host') == '0.0.0.0'


def test_serve_defaults_host_to_loopback_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """serve() must default host to '127.0.0.1' when env var is not set."""
    import types as _types

    monkeypatch.delenv('ANIGAMERPLUS_SCHEDULER_HOST', raising=False)
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    captured: dict[str, Any] = {}

    def _fake_uvicorn_run(app: Any, **kwargs: Any) -> None:  # noqa: ARG001
        captured.update(kwargs)

    fake_container = _fake_container()

    monkeypatch.setattr(scheduler_server_module, 'build_container', lambda: fake_container)
    monkeypatch.setattr(scheduler_server_module.uvicorn, 'run', _fake_uvicorn_run)

    import app.persistence.paths as _paths_mod

    fake_paths = _types.SimpleNamespace(logs_dir=pathlib.Path(tempfile.gettempdir()))
    monkeypatch.setattr(_paths_mod.WorkspacePaths, 'detect', staticmethod(lambda: fake_paths))

    scheduler_server_module.serve()

    assert captured.get('host') == '127.0.0.1'


def test_serve_passes_custom_port_to_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    """serve() must forward ANIGAMERPLUS_SCHEDULER_PORT to uvicorn.run as port= (int)."""
    import types as _types

    monkeypatch.delenv('ANIGAMERPLUS_SCHEDULER_HOST', raising=False)
    monkeypatch.setenv('ANIGAMERPLUS_SCHEDULER_PORT', '9999')
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    captured: dict[str, Any] = {}

    def _fake_uvicorn_run(app: Any, **kwargs: Any) -> None:  # noqa: ARG001
        captured.update(kwargs)

    fake_container = _fake_container()

    monkeypatch.setattr(scheduler_server_module, 'build_container', lambda: fake_container)
    monkeypatch.setattr(scheduler_server_module.uvicorn, 'run', _fake_uvicorn_run)

    import app.persistence.paths as _paths_mod

    fake_paths = _types.SimpleNamespace(logs_dir=pathlib.Path(tempfile.gettempdir()))
    monkeypatch.setattr(_paths_mod.WorkspacePaths, 'detect', staticmethod(lambda: fake_paths))

    scheduler_server_module.serve()

    assert captured.get('port') == 9999


# ---------------------------------------------------------------------------
# serve() WebSocket keepalive-ping env var resolution
# ---------------------------------------------------------------------------


def _serve_with_mocked_uvicorn(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run ``serve()`` with uvicorn.run mocked out; return the captured kwargs."""
    import types as _types

    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    captured: dict[str, Any] = {}

    def _fake_uvicorn_run(app: Any, **kwargs: Any) -> None:  # noqa: ARG001
        captured.update(kwargs)

    fake_container = _fake_container()

    monkeypatch.setattr(scheduler_server_module, 'build_container', lambda: fake_container)
    monkeypatch.setattr(scheduler_server_module.uvicorn, 'run', _fake_uvicorn_run)

    import app.persistence.paths as _paths_mod

    fake_paths = _types.SimpleNamespace(logs_dir=pathlib.Path(tempfile.gettempdir()))
    monkeypatch.setattr(_paths_mod.WorkspacePaths, 'detect', staticmethod(lambda: fake_paths))

    scheduler_server_module.serve()
    return captured


def test_uvicorn_config_reads_ws_ping_interval_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """ANIGAMERPLUS_WS_PING_INTERVAL/TIMEOUT must be forwarded to uvicorn.run."""
    monkeypatch.setenv('ANIGAMERPLUS_WS_PING_INTERVAL', '45')
    monkeypatch.setenv('ANIGAMERPLUS_WS_PING_TIMEOUT', '90')

    captured = _serve_with_mocked_uvicorn(monkeypatch)

    assert captured.get('ws_ping_interval') == 45.0
    assert captured.get('ws_ping_timeout') == 90.0


def test_uvicorn_config_defaults_to_30_ping_interval_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the env-vars unset, uvicorn.run gets the 30s/60s forgiving defaults."""
    monkeypatch.delenv('ANIGAMERPLUS_WS_PING_INTERVAL', raising=False)
    monkeypatch.delenv('ANIGAMERPLUS_WS_PING_TIMEOUT', raising=False)

    captured = _serve_with_mocked_uvicorn(monkeypatch)

    assert captured.get('ws_ping_interval') == 30.0
    assert captured.get('ws_ping_timeout') == 60.0

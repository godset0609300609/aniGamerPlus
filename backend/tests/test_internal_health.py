"""Tests for GET /internal/health — APScheduler-aware scheduler health endpoint."""

from __future__ import annotations

import pathlib
import tempfile
import types
from typing import Any

import fastapi.testclient
import pytest

import app.scheduler_server as scheduler_server_module
from app.scheduler_server import build_scheduler_app

_TEST_SECRET = 'internal-health-secret-99'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSettingsRepo:
    def load(self) -> Any:
        return types.SimpleNamespace(check_frequency=5)


class _RunningAps:
    """Stub ApsScheduler that reports running=True."""

    _scheduler = types.SimpleNamespace(running=True)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class _StoppedAps:
    """Stub ApsScheduler that reports running=False."""

    _scheduler = types.SimpleNamespace(running=False)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def _fake_container() -> Any:
    from app.logging_ import Logger

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
# Tests: APS running → ok
# ---------------------------------------------------------------------------


def test_internal_health_ok_when_aps_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status is 'ok' when the ApsScheduler is running."""
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)
    monkeypatch.setattr('app.scheduler.aps_scheduler.ApsScheduler', lambda settings_repo: _RunningAps())

    container = _fake_container()
    _app = build_scheduler_app(container)  # type: ignore[arg-type]

    with fastapi.testclient.TestClient(_app) as client:
        r = client.get(
            '/internal/health',
            headers={'X-Internal-Secret': _TEST_SECRET},
        )
        assert r.status_code == 200
        data = r.json()
        assert data['status'] == 'ok'
        assert data['aps_running'] is True


# ---------------------------------------------------------------------------
# Tests: APS not running → degraded
# ---------------------------------------------------------------------------


def test_internal_health_degraded_when_aps_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status is 'degraded' when ApsScheduler is not running."""
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)
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


# ---------------------------------------------------------------------------
# Tests: field presence
# ---------------------------------------------------------------------------


def test_internal_health_has_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response must contain all expected top-level fields."""
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)
    monkeypatch.setattr('app.scheduler.aps_scheduler.ApsScheduler', lambda settings_repo: _RunningAps())

    container = _fake_container()
    _app = build_scheduler_app(container)  # type: ignore[arg-type]

    with fastapi.testclient.TestClient(_app) as client:
        r = client.get(
            '/internal/health',
            headers={'X-Internal-Secret': _TEST_SECRET},
        )
        assert r.status_code == 200
        data = r.json()

        required = {'status', 'uptime_seconds', 'aps_running'}
        assert required <= data.keys(), f'Missing fields: {required - data.keys()}'


def test_internal_health_unauthorized_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /internal/health returns 401 when secret header is absent."""
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)
    monkeypatch.setattr('app.scheduler.aps_scheduler.ApsScheduler', lambda settings_repo: _RunningAps())

    container = _fake_container()
    _app = build_scheduler_app(container)  # type: ignore[arg-type]

    with fastapi.testclient.TestClient(_app) as client:
        r = client.get('/internal/health')
        assert r.status_code == 401

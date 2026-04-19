"""Tests for GET /internal/health — watchdog-aware scheduler health endpoint."""

from __future__ import annotations

import pathlib
import tempfile
import threading
import types
from typing import Any

import fastapi.testclient
import pytest

import app.scheduler_server as scheduler_server_module
from app.scheduler.watchdog import SchedulerWatchdog
from app.scheduler_server import build_scheduler_app

_TEST_SECRET = 'internal-health-secret-99'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeProgressBus:
    def snapshot(self) -> dict[int, Any]:
        return {}

    def cancel(self, sn: int) -> bool:
        return False


class _NullUpdateLoop:
    """UpdateLoop stand-in that blocks indefinitely."""

    def __init__(self) -> None:
        self._stop = threading.Event()

    def run_forever(self) -> None:
        self._stop.wait(timeout=30.0)

    def stop(self) -> None:
        self._stop.set()


def _fake_container() -> Any:
    from app.logging_ import Logger

    tmpdir = pathlib.Path(tempfile.gettempdir())
    logger = Logger(tmpdir, save_logs=False, quantity_of_logs=1)
    bus = _FakeProgressBus()

    container = types.SimpleNamespace(
        logger=logger,
        paths=types.SimpleNamespace(logs_dir=tmpdir),
        progress_bus=bus,
        manual_runner=None,
        task_history_repo=None,  # getattr-protected in lifespan
        build_update_loop=lambda **kw: _NullUpdateLoop(),
    )
    return container


# ---------------------------------------------------------------------------
# Tests: fresh heartbeat → ok
# ---------------------------------------------------------------------------


def test_internal_health_ok_when_beat_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status is 'ok' when the watchdog heartbeat is fresh."""
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container = _fake_container()
    _app = build_scheduler_app(container)  # type: ignore[arg-type]

    with fastapi.testclient.TestClient(_app) as client:
        watchdog: SchedulerWatchdog = _app.state.watchdog
        # Beat right before the check so age is minimal.
        watchdog.beat()

        r = client.get(
            '/internal/health',
            headers={'X-Internal-Secret': _TEST_SECRET},
        )
        assert r.status_code == 200
        data = r.json()
        assert data['status'] == 'ok'
        assert 'last_heartbeat_age_seconds' in data
        assert data['last_heartbeat_age_seconds'] < 60
        assert data['update_loop_running'] is True


# ---------------------------------------------------------------------------
# Tests: stale heartbeat → degraded
# ---------------------------------------------------------------------------


def test_internal_health_degraded_when_beat_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status is 'degraded' when last_heartbeat_age_seconds > 60."""
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container = _fake_container()
    _app = build_scheduler_app(container)  # type: ignore[arg-type]

    with fastapi.testclient.TestClient(_app) as client:
        watchdog: SchedulerWatchdog = _app.state.watchdog
        # Simulate a stale heartbeat by backdating the timestamp.
        watchdog._last_beat_ts -= 120.0  # type: ignore[attr-defined]

        r = client.get(
            '/internal/health',
            headers={'X-Internal-Secret': _TEST_SECRET},
        )
        assert r.status_code == 200
        data = r.json()
        assert data['status'] == 'degraded'
        assert data['last_heartbeat_age_seconds'] > 60


# ---------------------------------------------------------------------------
# Tests: field presence
# ---------------------------------------------------------------------------


def test_internal_health_has_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response must contain all expected top-level fields."""
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container = _fake_container()
    _app = build_scheduler_app(container)  # type: ignore[arg-type]

    with fastapi.testclient.TestClient(_app) as client:
        r = client.get(
            '/internal/health',
            headers={'X-Internal-Secret': _TEST_SECRET},
        )
        assert r.status_code == 200
        data = r.json()

        required = {'status', 'uptime_seconds', 'active_downloads', 'update_loop_running'}
        assert required <= data.keys(), f'Missing fields: {required - data.keys()}'


# ---------------------------------------------------------------------------
# Tests: active_downloads excludes terminal statuses
# ---------------------------------------------------------------------------


def test_health_active_downloads_excludes_cancelled_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """active_downloads must not count tasks whose status is '已取消'.

    A cancelled task transitions to status='已取消' immediately when
    ProgressBus.cancel() is called, but finish() is delayed 1 s.  During that
    window the entry is still in the snapshot — the health endpoint must
    exclude it from the count.
    """
    from app.downloader.progress import ProgressBus

    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container = _fake_container()
    real_bus = ProgressBus()
    # One active download.
    real_bus.start(10, 'ep10.mp4', status='正在下載')
    # One cancelled task (still in snapshot, finished_at not yet set).
    real_bus.start(20, 'ep20.mp4', status='正在下載')
    real_bus.cancel(20)  # sets status='已取消', schedules finish() after 1 s

    container.progress_bus = real_bus  # type: ignore[attr-defined]
    _app = build_scheduler_app(container)  # type: ignore[arg-type]

    with fastapi.testclient.TestClient(_app) as client:
        r = client.get(
            '/internal/health',
            headers={'X-Internal-Secret': _TEST_SECRET},
        )
        assert r.status_code == 200
        data = r.json()
        # Only sn=10 is genuinely active; sn=20 is cancelled.
        assert data['active_downloads'] == 1


def test_health_active_downloads_excludes_finished_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """active_downloads must not count tasks whose finished_at is set."""
    from app.downloader.progress import ProgressBus

    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container = _fake_container()
    real_bus = ProgressBus()
    real_bus.start(30, 'ep30.mp4', status='正在下載')
    real_bus.start(40, 'ep40.mp4', status='正在下載')
    # Mark sn=40 as finished (simulates a completed task within TTL window).
    real_bus.finish(40)

    container.progress_bus = real_bus  # type: ignore[attr-defined]
    _app = build_scheduler_app(container)  # type: ignore[arg-type]

    with fastapi.testclient.TestClient(_app) as client:
        r = client.get(
            '/internal/health',
            headers={'X-Internal-Secret': _TEST_SECRET},
        )
        assert r.status_code == 200
        data = r.json()
        assert data['active_downloads'] == 1

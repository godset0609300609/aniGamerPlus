"""Tests for GET /api/health — aggregate health endpoint."""

from __future__ import annotations

from typing import Any

import fastapi.testclient
import pytest

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _SchedulerProxyUp:
    """Fake proxy that reports the scheduler as healthy."""

    def is_scheduler_up(self) -> bool:
        return True

    async def fetch_health(self) -> dict[str, Any]:
        return {
            'status': 'ok',
            'uptime_seconds': 300,
            'active_downloads': 2,
            'update_loop_running': True,
            'last_heartbeat_age_seconds': 5.0,
        }

    def latest_snapshot(self) -> dict[int, Any]:
        return {}

    async def run_progress_subscription(self) -> None:
        import asyncio

        await asyncio.sleep(9999)

    async def close(self) -> None:
        pass

    async def enqueue_manual(self, request: Any, owner_id: str) -> None:
        pass

    async def cancel_task(self, sn: int) -> None:
        pass


class _SchedulerProxyDegraded:
    """Fake proxy reporting the scheduler as degraded."""

    def is_scheduler_up(self) -> bool:
        return True

    async def fetch_health(self) -> dict[str, Any]:
        return {
            'status': 'degraded',
            'uptime_seconds': 300,
            'active_downloads': 0,
            'update_loop_running': True,
            'last_heartbeat_age_seconds': 90.0,
        }

    def latest_snapshot(self) -> dict[int, Any]:
        return {}

    async def run_progress_subscription(self) -> None:
        import asyncio

        await asyncio.sleep(9999)

    async def close(self) -> None:
        pass

    async def enqueue_manual(self, request: Any, owner_id: str) -> None:
        pass

    async def cancel_task(self, sn: int) -> None:
        pass


class _SchedulerProxyDown:
    """Fake proxy that says the scheduler is unreachable."""

    def is_scheduler_up(self) -> bool:
        return False

    def latest_snapshot(self) -> dict[int, Any]:
        return {}

    async def run_progress_subscription(self) -> None:
        import asyncio

        await asyncio.sleep(9999)

    async def close(self) -> None:
        pass

    async def enqueue_manual(self, request: Any, owner_id: str) -> None:
        pass

    async def cancel_task(self, sn: int) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_ok_when_scheduler_healthy(
    client: fastapi.testclient.TestClient,
    fake_container: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overall status is 'ok' when api and scheduler are both healthy."""

    # Wire a healthy proxy into app state.
    proxy = _SchedulerProxyUp()
    # Inject proxy via app.state on the underlying FastAPI app.
    starlette_app = client.app
    starlette_app.state.scheduler_proxy = proxy  # type: ignore[attr-defined]

    r = client.get('/api/health')
    assert r.status_code == 200
    data = r.json()
    assert data['status'] == 'ok'
    assert data['scheduler']['status'] == 'ok'
    assert data['api']['status'] == 'ok'
    assert 'checked_at' in data


def test_health_degraded_when_scheduler_degraded(
    client: fastapi.testclient.TestClient,
) -> None:
    """Overall status is 'degraded' when scheduler reports degraded."""
    proxy = _SchedulerProxyDegraded()
    client.app.state.scheduler_proxy = proxy  # type: ignore[attr-defined]

    r = client.get('/api/health')
    assert r.status_code == 200
    data = r.json()
    assert data['status'] == 'degraded'
    assert data['scheduler']['status'] == 'degraded'


def test_health_degraded_when_scheduler_offline(
    client: fastapi.testclient.TestClient,
) -> None:
    """Overall status is 'degraded' when scheduler is offline."""
    proxy = _SchedulerProxyDown()
    client.app.state.scheduler_proxy = proxy  # type: ignore[attr-defined]

    r = client.get('/api/health')
    assert r.status_code == 200
    data = r.json()
    assert data['status'] == 'degraded'
    assert data['scheduler']['status'] == 'offline'


def test_health_degraded_when_no_proxy(
    client: fastapi.testclient.TestClient,
) -> None:
    """When no proxy is configured, scheduler status is 'offline'."""
    # Remove proxy from state if it exists.
    if hasattr(client.app.state, 'scheduler_proxy'):
        del client.app.state.scheduler_proxy

    r = client.get('/api/health')
    assert r.status_code == 200
    data = r.json()
    assert data['scheduler']['status'] == 'offline'
    assert data['status'] == 'degraded'


def test_health_response_structure(
    client: fastapi.testclient.TestClient,
) -> None:
    """Response contains all expected top-level fields."""
    r = client.get('/api/health')
    assert r.status_code == 200
    data = r.json()

    assert 'status' in data
    assert 'api' in data
    assert 'scheduler' in data
    assert 'checked_at' in data
    assert 'version' in data['api']


def test_health_fetch_timeout_is_3_seconds(
    client: fastapi.testclient.TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scheduler health fetch uses a 3 s timeout to avoid false-positive
    degraded reports when the scheduler is momentarily busy.

    The timeout value is verified by patching asyncio.wait_for inside the
    health module and recording what timeout value the handler passes.
    """
    import asyncio

    import app.api.health as health_module

    captured: list[float] = []
    _original_wait_for = asyncio.wait_for

    async def spy_wait_for(coro: Any, timeout: float) -> Any:
        captured.append(timeout)
        return await _original_wait_for(coro, timeout=timeout)

    monkeypatch.setattr(health_module.asyncio, 'wait_for', spy_wait_for)

    proxy = _SchedulerProxyUp()
    client.app.state.scheduler_proxy = proxy  # type: ignore[attr-defined]

    r = client.get('/api/health')
    assert r.status_code == 200

    assert captured, 'asyncio.wait_for was never called — proxy may be unreachable'
    assert captured[0] == 3.0, f'Expected scheduler health timeout=3.0 s, got {captured[0]}'

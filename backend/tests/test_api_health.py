"""Tests for GET /api/health — aggregate health endpoint."""

from __future__ import annotations

import asyncio
from typing import Any

import fastapi.testclient
import pytest

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeRedisOk:
    """Async Redis stub whose ping() returns True immediately."""

    async def ping(self) -> bool:
        return True


class _FakeRedisDown:
    """Async Redis stub whose ping() raises ConnectionError."""

    async def ping(self) -> bool:
        raise ConnectionError('redis unreachable')


class _FakeRedisSlow:
    """Async Redis stub whose ping() sleeps forever (tests the timeout path)."""

    async def ping(self) -> bool:
        await asyncio.sleep(9999)
        return True  # never reached


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_ok_when_redis_pingable(
    client: fastapi.testclient.TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overall status is 'ok' when Redis responds to ping."""
    monkeypatch.setattr('app.api.health._get_redis_client', lambda: _FakeRedisOk())

    r = client.get('/api/health')
    assert r.status_code == 200
    data = r.json()
    assert data['status'] == 'ok'
    assert data['redis']['status'] == 'ok'
    assert data['scheduler']['status'] == 'ok'  # back-compat alias
    assert data['api']['status'] == 'ok'
    assert 'checked_at' in data


def test_health_degraded_when_redis_down(
    client: fastapi.testclient.TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overall status is 'degraded' when Redis ping raises."""
    monkeypatch.setattr('app.api.health._get_redis_client', lambda: _FakeRedisDown())

    r = client.get('/api/health')
    assert r.status_code == 200
    data = r.json()
    assert data['status'] == 'degraded'
    assert data['redis']['status'] == 'offline'


def test_health_degraded_when_redis_unreachable_timeout(
    client: fastapi.testclient.TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overall status is 'degraded' when Redis ping times out."""
    import app.api.health as health_module

    monkeypatch.setattr('app.api.health._get_redis_client', lambda: _FakeRedisSlow())

    # Override wait_for in the health module to use a very short timeout so
    # the test completes quickly without waiting the full 2 s.
    _real_wait_for = asyncio.wait_for

    async def _fast_wait_for(coro: Any, timeout: float) -> Any:
        return await _real_wait_for(coro, timeout=0.05)

    monkeypatch.setattr(health_module.asyncio, 'wait_for', _fast_wait_for)

    r = client.get('/api/health')
    assert r.status_code == 200
    data = r.json()
    assert data['status'] == 'degraded'
    assert data['redis']['status'] == 'offline'


def test_health_degraded_when_no_redis_client(
    client: fastapi.testclient.TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overall status is 'degraded' when no Redis client is configured."""
    monkeypatch.setattr('app.api.health._get_redis_client', lambda: None)

    r = client.get('/api/health')
    assert r.status_code == 200
    data = r.json()
    assert data['status'] == 'degraded'
    assert data['redis']['status'] == 'offline'


def test_health_response_structure(
    client: fastapi.testclient.TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response contains all expected top-level fields."""
    monkeypatch.setattr('app.api.health._get_redis_client', lambda: _FakeRedisOk())

    r = client.get('/api/health')
    assert r.status_code == 200
    data = r.json()

    assert 'status' in data
    assert 'api' in data
    assert 'redis' in data
    assert 'scheduler' in data
    assert 'checked_at' in data
    assert 'version' in data['api']


def test_health_fetch_timeout_is_2_seconds(
    client: fastapi.testclient.TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Redis ping uses a 2 s timeout.

    Verified by patching asyncio.wait_for inside the health module and
    recording the timeout= argument the handler passes.
    """
    import app.api.health as health_module

    monkeypatch.setattr('app.api.health._get_redis_client', lambda: _FakeRedisOk())

    captured: list[float] = []
    _original_wait_for = asyncio.wait_for

    async def spy_wait_for(coro: Any, timeout: float) -> Any:
        captured.append(timeout)
        return await _original_wait_for(coro, timeout=timeout)

    monkeypatch.setattr(health_module.asyncio, 'wait_for', spy_wait_for)

    r = client.get('/api/health')
    assert r.status_code == 200

    assert captured, 'asyncio.wait_for was never called'
    assert captured[0] == 2.0, f'Expected Redis ping timeout=2.0 s, got {captured[0]}'

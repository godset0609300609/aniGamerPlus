"""Tests for :mod:`app.api.logs_ws` — the log streaming WebSocket + REST endpoint.

Key test scenarios
------------------
* Admin → receives historical snapshot and live records.
* Non-admin (downloader) via WS → closed with code 1008 (Policy Violation).
* No session (auth enabled) → close code 4401.
* GET /api/logs as admin → 200 with snapshot and level filter.
* GET /api/logs as non-admin → 403 Forbidden.
* _next_from_either → no items silently dropped when both queues ready.
"""

from __future__ import annotations

import asyncio
import json
import logging

import fastapi
import fastapi.testclient
import pytest

from app.api.deps import current_user_opt
from app.api.logs_ws import _entry_visible, _next_from_either
from app.log_config import RingBufferHandler, get_ring_buffer_handler
from app.persistence.user_repo import UserRow

from .conftest import FakeContainer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_ring_buffer() -> None:
    """Clear the ring buffer singleton between tests so snapshots don't bleed."""
    import app.log_config as _lc

    # Discard the old singleton so each test starts fresh.
    _lc._ring_buffer_handler = None


def _admin_user() -> UserRow:
    import datetime

    return UserRow(
        id='admin-1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        last_login_at=None,
    )


def _downloader_user() -> UserRow:
    import datetime

    return UserRow(
        id='dl-1',
        username='downloader',
        avatar_url=None,
        role='downloader',
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        last_login_at=None,
    )


def _emit_to_handler(handler: RingBufferHandler, msg: str, level: int = logging.INFO, sn: int | None = None) -> None:
    record = logging.LogRecord(
        name='test',
        level=level,
        pathname='',
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if sn is not None:
        record.sn = sn  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter('%(message)s'))
    handler.emit(record)


# ---------------------------------------------------------------------------
# _entry_visible unit tests
# ---------------------------------------------------------------------------


def test_entry_visible_admin_sees_all() -> None:
    admin = _admin_user()
    assert _entry_visible({'sn': None}, admin) is True
    assert _entry_visible({'sn': 123}, admin) is True
    assert _entry_visible({}, admin) is True


# ---------------------------------------------------------------------------
# WS endpoint tests
# ---------------------------------------------------------------------------


def _make_client_for_user(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
    user: UserRow | None,
) -> fastapi.testclient.TestClient:
    """Build a TestClient with current_user_opt overridden to return *user*."""
    monkeypatch.setenv('ANIGAMERPLUS_DISABLE_SCHEDULER', '1')

    import types

    from app.api.health import HealthService, get_health_service
    from app.main import DashboardApp
    from app.services import (
        AnimeListService,
        ConfigService,
        ProgressService,
        SnListService,
        TaskService,
        get_animelist_service,
        get_config_service,
        get_progress_service,
        get_snlist_service,
        get_task_service,
    )

    proxy = types.SimpleNamespace(
        paths=fake_container.paths,
        logger=fake_container.logger,
        settings_repo=fake_container.settings_repo,
        sn_list_repo=fake_container.sn_list_repo,
        cookie_repo=fake_container.cookie_repo,
        database=fake_container.database,
        anime_repo=fake_container.anime_repo,
        user_repo=fake_container.user_repo,
        anime_list_entry_repo=fake_container.anime_list_entry_repo,
        progress_bus=fake_container.progress_bus,
        manual_runner=fake_container.manual_runner,
    )
    app = DashboardApp(proxy).app

    app.dependency_overrides[get_config_service] = lambda: ConfigService(fake_container.settings_repo)
    app.dependency_overrides[get_snlist_service] = lambda: SnListService(fake_container.sn_list_repo)
    app.dependency_overrides[get_animelist_service] = lambda: AnimeListService(
        fake_container.sn_list_repo,
        fake_container.anime_repo,
        fake_container.anime_list_entry_repo,
        fake_container.user_repo,
    )
    app.dependency_overrides[get_task_service] = lambda: TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
    )
    app.dependency_overrides[get_progress_service] = lambda: ProgressService(
        fake_container.progress_bus,
        fake_container.user_repo,
    )
    app.dependency_overrides[get_health_service] = lambda: HealthService(fake_container.paths)

    # Override auth to return the supplied user (or None to simulate no session).
    _captured_user = user

    async def _user_dep() -> UserRow | None:
        return _captured_user

    app.dependency_overrides[current_user_opt] = _user_dep
    return fastapi.testclient.TestClient(app)


def test_no_session_closes_with_4401(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unauthenticated connection must be rejected with close code 4401."""
    import starlette.testclient

    tc = _make_client_for_user(fake_container, monkeypatch, user=None)
    # The server calls ws.close(code=4401) before accepting, so the
    # TestClient WebSocketTestSession raises WebSocketDisconnect on
    # any send/receive after the close.  We verify the close code.
    try:
        with tc.websocket_connect('/api/ws/logs') as ws:
            ws.receive_text()  # triggers the close response
    except starlette.testclient.WebSocketDisconnect as exc:
        assert exc.code == 4401
    else:
        pytest.fail('Expected WebSocketDisconnect with code 4401')


def test_non_admin_ws_closes_with_1008(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-admin authenticated connection must be closed with code 1008 (Policy Violation)."""
    import starlette.testclient

    tc = _make_client_for_user(fake_container, monkeypatch, user=_downloader_user())
    try:
        with tc.websocket_connect('/api/ws/logs') as ws:
            ws.receive_text()  # triggers the close response
    except starlette.testclient.WebSocketDisconnect as exc:
        assert exc.code == 1008
    else:
        pytest.fail('Expected WebSocketDisconnect with code 1008')


def test_admin_receives_historical_snapshot(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin should receive pre-existing buffered records on connect."""
    handler = get_ring_buffer_handler()
    _emit_to_handler(handler, 'system boot')
    _emit_to_handler(handler, 'download started', sn=100)

    tc = _make_client_for_user(fake_container, monkeypatch, user=_admin_user())
    with tc.websocket_connect('/api/ws/logs') as ws:
        msg1 = json.loads(ws.receive_text())
        msg2 = json.loads(ws.receive_text())

    messages = {msg1['message'], msg2['message']}
    assert 'system boot' in messages
    assert 'download started' in messages


# ---------------------------------------------------------------------------
# Per-user connection cap (fix #21) — shared registry with progress_ws
# ---------------------------------------------------------------------------


def test_ws_connection_cap_rejects_sixth_connection(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contextlib

    import starlette.testclient

    tc = _make_client_for_user(fake_container, monkeypatch, user=_admin_user())

    with contextlib.ExitStack() as stack:
        for _ in range(5):
            stack.enter_context(tc.websocket_connect('/api/ws/logs'))

        try:
            with tc.websocket_connect('/api/ws/logs') as ws:
                ws.receive_text()
        except starlette.testclient.WebSocketDisconnect as exc:
            assert exc.code == 4429
        else:
            pytest.fail('Expected WebSocketDisconnect with code 4429')


# ---------------------------------------------------------------------------
# Origin allowlist (fix #37)
# ---------------------------------------------------------------------------


def test_ws_rejects_disallowed_origin(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import starlette.testclient

    tc = _make_client_for_user(fake_container, monkeypatch, user=_admin_user())
    try:
        with tc.websocket_connect('/api/ws/logs', headers={'origin': 'http://evil.example'}) as ws:
            ws.receive_text()
    except starlette.testclient.WebSocketDisconnect as exc:
        assert exc.code == 1008
    else:
        pytest.fail('Expected WebSocketDisconnect with code 1008')


def test_ws_accepts_allowed_origin(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tc = _make_client_for_user(fake_container, monkeypatch, user=_admin_user())
    with tc.websocket_connect('/api/ws/logs', headers={'origin': 'http://web'}):
        pass  # accepted — no exception


# ---------------------------------------------------------------------------
# GET /api/logs snapshot endpoint
# ---------------------------------------------------------------------------


def test_get_logs_returns_snapshot(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
) -> None:
    """GET /api/logs as admin should return the current ring-buffer snapshot."""
    handler = get_ring_buffer_handler()
    _emit_to_handler(handler, 'info message', level=logging.INFO)
    _emit_to_handler(handler, 'warn message', level=logging.WARNING)

    resp = client.get('/api/logs')
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    messages = [e['message'] for e in data]
    assert 'info message' in messages
    assert 'warn message' in messages


def test_get_logs_level_filter(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
) -> None:
    """GET /api/logs?level=WARNING should exclude INFO records."""
    handler = get_ring_buffer_handler()
    _emit_to_handler(handler, 'just info', level=logging.INFO)
    _emit_to_handler(handler, 'important', level=logging.WARNING)

    resp = client.get('/api/logs?level=WARNING')
    assert resp.status_code == 200
    data = resp.json()
    messages = [e['message'] for e in data]
    assert 'important' in messages
    assert 'just info' not in messages


def test_get_logs_limit(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
) -> None:
    """GET /api/logs?limit=1 should return at most 1 record."""
    handler = get_ring_buffer_handler()
    for i in range(5):
        _emit_to_handler(handler, f'msg-{i}')

    resp = client.get('/api/logs?limit=1')
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1


def test_get_logs_non_admin_returns_403(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/logs as a non-admin user must return 403 Forbidden."""
    tc = _make_client_for_user(fake_container, monkeypatch, user=_downloader_user())
    resp = tc.get('/api/logs')
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# _next_from_either — no silent item drops
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_next_from_either_returns_q_item(anyio_backend: str) -> None:
    """Returns from q when q has an item ready first."""
    q: asyncio.Queue[str] = asyncio.Queue()
    bridge: asyncio.Queue[str] = asyncio.Queue()
    await q.put('from-q')
    result = await _next_from_either(q, bridge)
    assert result == 'from-q'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_next_from_either_returns_bridge_item(anyio_backend: str) -> None:
    """Returns from bridge when bridge has an item ready first."""
    q: asyncio.Queue[str] = asyncio.Queue()
    bridge: asyncio.Queue[str] = asyncio.Queue()
    await bridge.put('from-bridge')
    result = await _next_from_either(q, bridge)
    assert result == 'from-bridge'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_next_from_either_no_item_dropped_when_both_ready(
    anyio_backend: str,
) -> None:
    """When both queues have items simultaneously, neither item is silently discarded.

    This test verifies the fix for the cancel-race bug in the original
    asyncio.wait implementation: if both done tasks complete at the same time
    and we only returned one result while discarding the other task's consumed
    item, the second item would be lost.
    """
    q: asyncio.Queue[str] = asyncio.Queue()
    bridge: asyncio.Queue[str] = asyncio.Queue()

    # Pre-fill both queues.
    await q.put('q-item')
    await bridge.put('bridge-item')

    first = await _next_from_either(q, bridge)
    # The item NOT returned by the first call must still be in its queue.
    assert (q.qsize() + bridge.qsize()) == 1, (
        'One item must remain in a queue; the other was returned — neither must be silently dropped'
    )
    # Retrieve the remaining item via a second call.
    second = await _next_from_either(q, bridge)
    items = {first, second}
    assert items == {'q-item', 'bridge-item'}


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_next_from_either_propagates_cancellation(anyio_backend: str) -> None:
    """CancelledError from outer task is not swallowed."""
    q: asyncio.Queue[str] = asyncio.Queue()
    bridge: asyncio.Queue[str] = asyncio.Queue()

    async def _call() -> object:
        return await _next_from_either(q, bridge)

    task = asyncio.create_task(_call())
    await asyncio.sleep(0)  # let the task start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

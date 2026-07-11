"""Tests for the progress WebSocket endpoint."""

from __future__ import annotations

import contextlib
import json

import fastapi.testclient
import pytest
import starlette.testclient

from .conftest import FakeContainer


def test_ws_pushes_snapshot(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    fake_container.progress_bus.start(12345, 'EP01.mp4', status='正在下載')
    fake_container.progress_bus.update_rate(12345, 42.5)

    with client.websocket_connect('/api/ws/tasks_progress') as ws:
        message = ws.receive_text()
        parsed = json.loads(message)
        assert '12345' in parsed
        entry = parsed['12345']
        assert entry['rate'] == 42.5
        assert entry['status'] == '正在下載'
        assert entry['filename'] == 'EP01.mp4'
        # New optional fields should be present with defaults.
        assert entry['retries'] == 0
        assert entry['bangumi_name'] is None


def test_ws_emits_empty_snapshot_when_idle(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """Starting a subscriber with no active downloads yields ``{}``.

    This exercises the "no entries" branch of the progress service — the
    legacy ``tasks_progress_rate`` dict could surface malformed entries
    that the service had to filter out; :class:`ProgressBus` can't hold
    malformed state, so the empty case is the remaining branch to cover.
    """
    # No calls to progress_bus.start() — the bus is empty by default.
    with client.websocket_connect('/api/ws/tasks_progress') as ws:
        message = ws.receive_text()
        parsed = json.loads(message)
        assert parsed == {}


# ---------------------------------------------------------------------------
# Per-user connection cap (fix #21)
# ---------------------------------------------------------------------------


def test_ws_connection_cap_rejects_sixth_connection(client: fastapi.testclient.TestClient) -> None:
    """The client fixture's every connection uses the same sentinel-admin
    user id, so five concurrent connections exhaust the cap and a sixth
    must be rejected with 4429."""
    with contextlib.ExitStack() as stack:
        for _ in range(5):
            stack.enter_context(client.websocket_connect('/api/ws/tasks_progress'))

        try:
            with client.websocket_connect('/api/ws/tasks_progress') as ws:
                ws.receive_text()
        except starlette.testclient.WebSocketDisconnect as exc:
            assert exc.code == 4429
        else:
            pytest.fail('Expected WebSocketDisconnect with code 4429')


def test_ws_connection_cap_frees_slot_on_disconnect(client: fastapi.testclient.TestClient) -> None:
    """Closing a connection frees its slot so a new one can connect."""
    with contextlib.ExitStack() as stack:
        for _ in range(5):
            stack.enter_context(client.websocket_connect('/api/ws/tasks_progress'))

    # All 5 connections above are now closed (ExitStack unwound) — a fresh
    # connection must be accepted again.
    with client.websocket_connect('/api/ws/tasks_progress') as ws:
        message = ws.receive_text()
        assert json.loads(message) == {}


# ---------------------------------------------------------------------------
# Origin allowlist (fix #37)
# ---------------------------------------------------------------------------


def test_ws_rejects_disallowed_origin(client: fastapi.testclient.TestClient) -> None:
    try:
        with client.websocket_connect(
            '/api/ws/tasks_progress', headers={'origin': 'http://evil.example'}
        ) as ws:
            ws.receive_text()
    except starlette.testclient.WebSocketDisconnect as exc:
        assert exc.code == 1008
    else:
        pytest.fail('Expected WebSocketDisconnect with code 1008')


def test_ws_accepts_allowed_origin(client: fastapi.testclient.TestClient) -> None:
    with client.websocket_connect(
        '/api/ws/tasks_progress', headers={'origin': 'http://localhost:5173'}
    ) as ws:
        message = ws.receive_text()
        assert json.loads(message) == {}

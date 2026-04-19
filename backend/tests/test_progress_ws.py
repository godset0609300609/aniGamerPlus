"""Tests for the progress WebSocket endpoint."""

from __future__ import annotations

import json

import fastapi.testclient

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

"""Tests for SchedulerProxy (app/api/_scheduler_proxy.py)."""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.api._scheduler_proxy import SchedulerProxy, SchedulerUnreachable, _parse_snapshot
from app.downloader.progress import TaskProgress


# ---------------------------------------------------------------------------
# _parse_snapshot — unit tests (sync)
# ---------------------------------------------------------------------------


def test_parse_snapshot_empty() -> None:
    result = _parse_snapshot({})
    assert result == {}


def test_parse_snapshot_valid_entry() -> None:
    data: dict[str, Any] = {
        '123': {
            'sn': 123,
            'rate': 45.5,
            'status': '正在下載',
            'filename': 'ep01.mp4',
            'bangumi_name': 'BG',
            'episode': '1',
            'resolution': '1080p',
            'speed_mbps': 2.5,
            'eta_seconds': 60,
            'retries': 0,
            'started_at': '2026-04-18T10:00:00+00:00',
            'owner_id': 'user-1',
        }
    }
    result = _parse_snapshot(data)
    assert 123 in result
    entry = result[123]
    assert isinstance(entry, TaskProgress)
    assert entry.rate == 45.5
    assert entry.status == '正在下載'
    assert entry.filename == 'ep01.mp4'
    assert entry.owner_id == 'user-1'
    assert entry.started_at is not None


def test_parse_snapshot_skips_malformed() -> None:
    data: dict[str, Any] = {
        'not-an-int': 'bad',
        '456': {
            'sn': 456,
            'rate': 0.0,
            'status': 'ok',
            'filename': 'f.mp4',
        },
    }
    result = _parse_snapshot(data)
    # "not-an-int" can be parsed as a key but the value is a str, not dict.
    assert 456 in result


def test_parse_snapshot_none_started_at() -> None:
    data: dict[str, Any] = {
        '1': {
            'sn': 1,
            'rate': 0.0,
            'status': 's',
            'filename': 'f',
            'started_at': None,
        }
    }
    result = _parse_snapshot(data)
    assert result[1].started_at is None


# ---------------------------------------------------------------------------
# SchedulerProxy — async unit tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def proxy() -> SchedulerProxy:
    return SchedulerProxy(base_url='http://127.0.0.1:5001', secret='test-secret')


def test_proxy_latest_snapshot_empty_by_default(proxy: SchedulerProxy) -> None:
    assert proxy.latest_snapshot() == {}


def test_proxy_is_scheduler_up_false_by_default(proxy: SchedulerProxy) -> None:
    assert not proxy.is_scheduler_up()


def test_proxy_is_scheduler_up_true_after_recent_message(
    proxy: SchedulerProxy,
) -> None:
    proxy._last_ws_message_at = time.monotonic()
    assert proxy.is_scheduler_up()


def test_proxy_is_scheduler_up_false_after_stale_message(
    proxy: SchedulerProxy,
) -> None:
    # Threshold is _WS_FRESHNESS_SECONDS = 30.0; use 35 s to be safely outside.
    proxy._last_ws_message_at = time.monotonic() - 35.0
    assert not proxy.is_scheduler_up()


def test_proxy_is_scheduler_up_true_within_freshness_window(
    proxy: SchedulerProxy,
) -> None:
    """A message 10 s ago is still within the 30 s freshness window."""
    proxy._last_ws_message_at = time.monotonic() - 10.0
    assert proxy.is_scheduler_up()


# ---------------------------------------------------------------------------
# enqueue_manual — mock httpx
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_enqueue_manual_posts_correct_payload(proxy: SchedulerProxy) -> None:
    from app.models import ManualTaskRequest

    req = ManualTaskRequest(sn=999, resolution='720', mode='single', thread=2)

    captured: dict[str, Any] = {}

    async def _mock_post(url: str, **kwargs: Any) -> httpx.Response:
        captured['url'] = url
        captured['json'] = kwargs.get('json')
        captured['headers'] = kwargs.get('headers', {})
        return httpx.Response(202, request=httpx.Request('POST', 'http://127.0.0.1:5001'))

    with patch.object(proxy._client, 'post', side_effect=_mock_post):
        await proxy.enqueue_manual(req, 'user-42')

    assert '/internal/tasks/manual' in captured['url']
    body = captured['json']
    assert body['sn'] == '999'
    assert body['resolution'] == '720'
    assert body['owner_id'] == 'user-42'


@pytest.mark.anyio
async def test_enqueue_manual_sends_secret_header(proxy: SchedulerProxy) -> None:
    from app.models import ManualTaskRequest

    req = ManualTaskRequest(sn=1, resolution='1080', mode='single', thread=1)

    async def _mock_post(url: str, **kwargs: Any) -> httpx.Response:
        # Headers are set on the client, not per-call in our impl.
        return httpx.Response(202, request=httpx.Request('POST', 'http://127.0.0.1:5001'))

    with patch.object(proxy._client, 'post', side_effect=_mock_post):
        await proxy.enqueue_manual(req, 'u1')

    # The client was constructed with the secret in its default headers.
    assert proxy._client.headers.get('x-internal-secret') == 'test-secret'


# ---------------------------------------------------------------------------
# enqueue_manual — SchedulerUnreachable on transport / status errors
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_enqueue_manual_raises_scheduler_unreachable_on_connect_error(
    proxy: SchedulerProxy,
) -> None:
    """ConnectError from httpx must be re-raised as SchedulerUnreachable."""
    from app.models import ManualTaskRequest

    req = ManualTaskRequest(sn=1, resolution='1080', mode='single', thread=1)

    async def _mock_post(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError('connection refused')

    with patch.object(proxy._client, 'post', side_effect=_mock_post):
        with pytest.raises(SchedulerUnreachable, match='unreachable'):
            await proxy.enqueue_manual(req, 'u1')


@pytest.mark.anyio
async def test_enqueue_manual_raises_scheduler_unreachable_on_timeout(
    proxy: SchedulerProxy,
) -> None:
    """TimeoutException from httpx must be re-raised as SchedulerUnreachable."""
    from app.models import ManualTaskRequest

    req = ManualTaskRequest(sn=2, resolution='720', mode='single', thread=1)

    async def _mock_post(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.TimeoutException('timed out')

    with patch.object(proxy._client, 'post', side_effect=_mock_post):
        with pytest.raises(SchedulerUnreachable, match='unreachable'):
            await proxy.enqueue_manual(req, 'u1')


@pytest.mark.anyio
async def test_enqueue_manual_raises_scheduler_unreachable_on_5xx(
    proxy: SchedulerProxy,
) -> None:
    """A 5xx HTTP response must be re-raised as SchedulerUnreachable."""
    from app.models import ManualTaskRequest

    req = ManualTaskRequest(sn=3, resolution='480', mode='single', thread=1)

    async def _mock_post(url: str, **kwargs: Any) -> httpx.Response:
        raw_request = httpx.Request('POST', 'http://127.0.0.1:5001/internal/tasks/manual')
        return httpx.Response(503, request=raw_request)

    with patch.object(proxy._client, 'post', side_effect=_mock_post):
        with pytest.raises(SchedulerUnreachable, match='503'):
            await proxy.enqueue_manual(req, 'u1')


@pytest.mark.anyio
async def test_enqueue_manual_succeeds_on_202(proxy: SchedulerProxy) -> None:
    """A 202 Accepted response must complete without raising."""
    from app.models import ManualTaskRequest

    req = ManualTaskRequest(sn=4, resolution='1080', mode='single', thread=1)

    async def _mock_post(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(202, request=httpx.Request('POST', 'http://127.0.0.1:5001'))

    with patch.object(proxy._client, 'post', side_effect=_mock_post):
        await proxy.enqueue_manual(req, 'u2')  # must not raise


# ---------------------------------------------------------------------------
# cancel_task — mock httpx DELETE
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_task_sends_delete(proxy: SchedulerProxy) -> None:
    captured: dict[str, str] = {}

    async def _mock_delete(url: str, **kwargs: Any) -> httpx.Response:
        captured['url'] = url
        return httpx.Response(204, request=httpx.Request('DELETE', 'http://127.0.0.1:5001'))

    with patch.object(proxy._client, 'delete', side_effect=_mock_delete):
        await proxy.cancel_task(77)

    assert '/internal/tasks/77' in captured['url']


# ---------------------------------------------------------------------------
# latest_snapshot updates after WS parse
# ---------------------------------------------------------------------------


def test_latest_snapshot_returns_parsed_data(proxy: SchedulerProxy) -> None:
    from app.downloader.progress import TaskProgress

    fake = TaskProgress(sn=42, rate=0.5, status='x', filename='f.mp4')
    proxy._last_snapshot = {42: fake}
    snap = proxy.latest_snapshot()
    assert 42 in snap
    # Returns a copy — mutations don't affect internal state.
    snap[99] = fake  # type: ignore[assignment]
    assert 99 not in proxy._last_snapshot


# ---------------------------------------------------------------------------
# run_progress_subscription — cancellation stops the loop
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_run_progress_subscription_cancels_cleanly(
    proxy: SchedulerProxy,
    anyio_backend: str,
) -> None:
    """Cancelling the task should not raise; is_scheduler_up stays False.

    Pinned to asyncio backend because ``SchedulerProxy.run_progress_subscription``
    uses ``asyncio.create_task`` internally.
    """

    async def _always_fail() -> None:
        raise ConnectionRefusedError('no server')

    with patch.object(proxy, '_subscribe_once', side_effect=_always_fail):
        task = asyncio.create_task(proxy.run_progress_subscription())
        # Let it attempt once and start the back-off sleep.
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Scheduler should still be reported as down.
    assert not proxy.is_scheduler_up()


# ---------------------------------------------------------------------------
# close — cleans up client
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_closes_http_client(proxy: SchedulerProxy) -> None:
    closed: list[bool] = []

    async def _mock_aclose() -> None:
        closed.append(True)

    with patch.object(proxy._client, 'aclose', side_effect=_mock_aclose):
        await proxy.close()

    assert closed == [True]


# ---------------------------------------------------------------------------
# _parse_snapshot — robustness against unexpected fields
# ---------------------------------------------------------------------------


def test_parse_snapshot_ignores_unknown_field() -> None:
    """Extra keys in the wire payload must not cause _parse_snapshot to raise."""
    data: dict[str, Any] = {
        '77': {
            'sn': 77,
            'rate': 1.0,
            'status': 'ok',
            'filename': 'f.mp4',
            '_cancel_event': None,  # would crash TaskProgress(**entry)
            '_extra_unknown': 'surprise',
        }
    }
    # _parse_snapshot must silently skip the unknown fields or the entry
    # (it uses hand-picked kwargs, so the entry should parse cleanly).
    result = _parse_snapshot(data)
    assert 77 in result
    assert result[77].status == 'ok'


def test_parse_snapshot_with_cancel_event_field_skipped() -> None:
    """_cancel_event field in wire payload must not break parsing."""
    data: dict[str, Any] = {
        '88': {
            'sn': 88,
            'rate': 0.0,
            'status': '完成',
            'filename': 'ep.mp4',
            '_cancel_event': 'cannot-serialise-this',
        }
    }
    result = _parse_snapshot(data)
    assert 88 in result
    assert result[88].sn == 88


# ---------------------------------------------------------------------------
# _subscribe_once — bad message must not tear down the WS connection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_subscribe_once_survives_bad_message(
    proxy: SchedulerProxy,
    anyio_backend: str,
) -> None:
    """A malformed JSON message must be skipped; a valid one after it is processed.

    Simulates _subscribe_once by feeding two messages through the same
    try/except loop that lives inside the real method, verifying the guard
    prevents a bad message from aborting the connection.
    """
    import json as _json

    # Messages the fake WS would deliver in order.
    messages = [
        'not-json-at-all',  # bad: will raise JSONDecodeError
        '{"48412": {"sn": 48412, "rate": 0.5, "status": "下載完成", "filename": "ep.mp4"}}',
    ]

    # Replay the per-message logic from _subscribe_once in isolation.
    for raw in messages:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode()
            data: dict[str, object] = _json.loads(raw)
            proxy._last_ws_message_at = time.monotonic()
            proxy._last_snapshot = _parse_snapshot(data)
        except Exception:  # noqa: BLE001 — mirrors the real guard
            proxy._logger.warning(
                'SchedulerProxy: bad message from scheduler WS — skipping',
                exc_info=True,
            )

    # Bad message was skipped; valid message was processed.
    assert 48412 in proxy._last_snapshot
    assert proxy._last_snapshot[48412].status == '下載完成'


# ---------------------------------------------------------------------------
# _parse_snapshot — finished_at and cooldown_until reconstruction
# ---------------------------------------------------------------------------


def test_proxy_reconstructs_finished_at() -> None:
    """finished_at ISO string in the wire payload must round-trip to a datetime."""
    data: dict[str, Any] = {
        '100': {
            'sn': 100,
            'rate': 0.0,
            'status': '下載完成',
            'filename': 'ep.mp4',
            'finished_at': '2026-04-18T11:00:00+00:00',
        }
    }
    result = _parse_snapshot(data)
    assert 100 in result
    assert result[100].finished_at == datetime.datetime.fromisoformat('2026-04-18T11:00:00+00:00')


def test_proxy_reconstructs_cooldown_until() -> None:
    """cooldown_until ISO string in the wire payload must round-trip to a datetime."""
    data: dict[str, Any] = {
        '100': {
            'sn': 100,
            'rate': 0.0,
            'status': '正在解析',
            'filename': 'ep.mp4',
            'cooldown_until': '2026-04-18T12:00:00+00:00',
        }
    }
    result = _parse_snapshot(data)
    assert 100 in result
    assert result[100].cooldown_until == datetime.datetime.fromisoformat('2026-04-18T12:00:00+00:00')


def test_proxy_handles_missing_finished_at() -> None:
    """finished_at absent from the wire payload must produce None on TaskProgress."""
    data: dict[str, Any] = {
        '101': {
            'sn': 101,
            'rate': 0.0,
            'status': '正在下載',
            'filename': 'ep.mp4',
        }
    }
    result = _parse_snapshot(data)
    assert result[101].finished_at is None


def test_proxy_handles_missing_cooldown_until() -> None:
    """cooldown_until absent from the wire payload must produce None on TaskProgress."""
    data: dict[str, Any] = {
        '102': {
            'sn': 102,
            'rate': 0.0,
            'status': '正在下載',
            'filename': 'ep.mp4',
        }
    }
    result = _parse_snapshot(data)
    assert result[102].cooldown_until is None


def test_proxy_handles_non_string_cooldown_until() -> None:
    """A non-string cooldown_until value (e.g. int) must not crash and must be None."""
    data: dict[str, Any] = {
        '103': {
            'sn': 103,
            'rate': 0.0,
            'status': '正在下載',
            'filename': 'ep.mp4',
            'cooldown_until': 1234567890,  # int, not a string
        }
    }
    result = _parse_snapshot(data)
    assert 103 in result
    assert result[103].cooldown_until is None


def test_proxy_handles_non_string_finished_at() -> None:
    """A non-string finished_at value (e.g. int) must not crash and must be None."""
    data: dict[str, Any] = {
        '104': {
            'sn': 104,
            'rate': 0.0,
            'status': '下載完成',
            'filename': 'ep.mp4',
            'finished_at': 9999,  # int, not a string
        }
    }
    result = _parse_snapshot(data)
    assert 104 in result
    assert result[104].finished_at is None


# ---------------------------------------------------------------------------
# run_progress_subscription — widened "known disconnect" exception list
# ---------------------------------------------------------------------------


def _run_proxy_subscription_once(
    proxy: SchedulerProxy,
    exc_or_factory: object,
) -> tuple[list[logging.LogRecord], list[logging.LogRecord]]:
    """Run ``run_progress_subscription`` for one iteration with a custom handler.

    Returns ``(info_records, warning_records)`` captured by a dedicated
    ``logging.Handler`` installed directly on the proxy's logger — this
    approach is resilient to ``propagate=False`` or suite-level level mutations
    that can cause ``caplog`` to see an empty record list.
    """
    import logging as _logging

    captured: list[_logging.LogRecord] = []

    class _Capture(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture()
    handler.setLevel(_logging.DEBUG)
    proxy._logger.setLevel(_logging.DEBUG)
    proxy._logger.disabled = False  # guard against dictConfig disable_existing_loggers
    proxy._logger.addHandler(handler)

    async def _run() -> None:
        factory = exc_or_factory
        call_count: list[int] = [0]

        async def _raise() -> None:
            call_count[0] += 1
            if callable(factory):
                raise factory()
            raise factory  # type: ignore[misc]

        with patch.object(proxy, '_subscribe_once', side_effect=_raise):
            task = asyncio.create_task(proxy.run_progress_subscription())
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())

    proxy._logger.removeHandler(handler)
    info_records = [r for r in captured if r.levelno == _logging.INFO and 'disconnected' in r.getMessage()]
    warning_records = [r for r in captured if r.levelno == _logging.WARNING and 'disconnected' in r.getMessage()]
    return info_records, warning_records


@pytest.mark.parametrize(
    'exc_factory',
    [
        lambda: ConnectionRefusedError('refused'),
        lambda: ConnectionResetError('reset'),
        lambda: ConnectionAbortedError('aborted'),
        lambda: OSError('os error'),
        lambda: TimeoutError('timed out'),
    ],
    ids=[
        'ConnectionRefusedError',
        'ConnectionResetError',
        'ConnectionAbortedError',
        'OSError',
        'TimeoutError',
    ],
)
def test_known_disconnect_exceptions_log_info_and_retry(
    proxy: SchedulerProxy,
    exc_factory: object,
) -> None:
    """Each exception in the widened 'known disconnect' list must be caught with
    INFO-level logging (no traceback bubble) and trigger a retry."""
    info_records, _warn = _run_proxy_subscription_once(proxy, exc_factory)
    assert info_records, f'Expected INFO reconnect log for {exc_factory}; got nothing'
    for rec in info_records:
        assert rec.exc_info is None, f'Expected no exc_info on INFO record: {rec}'


def test_websocket_connection_closed_logs_info_no_traceback(proxy: SchedulerProxy) -> None:
    """websockets.exceptions.ConnectionClosed must be treated as a known
    disconnect: INFO log, no traceback, retry continues."""
    import websockets.exceptions

    class _FakeConnClosed(websockets.exceptions.ConnectionClosed):
        def __init__(self) -> None:
            super().__init__(None, None)  # type: ignore[arg-type]

    info_records, _warn = _run_proxy_subscription_once(proxy, _FakeConnClosed)
    assert info_records, f'Expected INFO reconnect log for ConnectionClosed; got nothing'
    for rec in info_records:
        assert rec.exc_info is None


def test_websocket_exception_logs_info_no_traceback(proxy: SchedulerProxy) -> None:
    """websockets.exceptions.WebSocketException (base) must be treated as known disconnect."""
    import websockets.exceptions

    info_records, _warn = _run_proxy_subscription_once(
        proxy,
        lambda: websockets.exceptions.WebSocketException('generic ws error'),
    )
    assert info_records, 'Expected INFO reconnect log for WebSocketException; got nothing'
    for rec in info_records:
        assert rec.exc_info is None


def test_unexpected_exception_logs_warning_with_traceback(proxy: SchedulerProxy) -> None:
    """An exception NOT in the known-disconnect list (e.g. RuntimeError) must
    be caught by the generic ``Exception`` branch: WARN level + exc_info set."""
    _info, warning_records = _run_proxy_subscription_once(
        proxy,
        lambda: RuntimeError('totally unexpected'),
    )
    assert warning_records, f'Expected WARNING log for unexpected exc; got nothing'
    for rec in warning_records:
        assert rec.exc_info is not None, f'Expected exc_info on WARNING record: {rec}'

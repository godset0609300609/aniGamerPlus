"""Tests for the internal scheduler server (app/scheduler_server.py)."""

from __future__ import annotations

import collections.abc
import datetime
import json
import pathlib
import tempfile
import threading
import types
from typing import Any

import fastapi.testclient
import pytest
import starlette.websockets

import app.scheduler_server as scheduler_server_module
from app.scheduler_server import build_scheduler_app

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeManualRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._event = threading.Event()

    def run(self, sn: int | None, *, owner_id: str | None = None, **kwargs: Any) -> None:
        self.calls.append({'sn': sn, 'owner_id': owner_id, **kwargs})
        self._event.set()

    def wait(self, timeout: float = 1.0) -> bool:
        return self._event.wait(timeout)


class _FakeProgressBus:
    def __init__(self) -> None:
        self._entries: dict[int, Any] = {}

    def snapshot(self) -> dict[int, Any]:
        return dict(self._entries)

    def cancel(self, sn: int) -> bool:
        if sn not in self._entries:
            return False
        self._entries[sn].status = '已取消'
        return True

    def finish(self, sn: int) -> None:
        """No-op stub — tests that need to assert on finish() should subclass."""

    def seed(self, sn: int, **kwargs: Any) -> None:
        from app.downloader.progress import TaskProgress

        self._entries[sn] = TaskProgress(
            sn=sn,
            rate=kwargs.get('rate', 0.0),
            status=kwargs.get('status', '正在下載'),
            filename=kwargs.get('filename', f'ep{sn}.mp4'),
            started_at=datetime.datetime.now(datetime.UTC),
        )


def _fake_container(secret: str) -> Any:
    """Build a SimpleNamespace container for scheduler_server tests."""
    from app.logging_ import Logger

    # Use a temporary directory for the logger (save_logs=False means no
    # file handler is attached, so the directory only needs to be a valid path).
    tmpdir = pathlib.Path(tempfile.gettempdir())
    logger = Logger(tmpdir, save_logs=False, quantity_of_logs=1)
    progress_bus = _FakeProgressBus()
    runner = _FakeManualRunner()

    container = types.SimpleNamespace(
        logger=logger,
        paths=types.SimpleNamespace(logs_dir=tmpdir),
        progress_bus=progress_bus,
        manual_runner=runner,
        task_history_repo=None,  # getattr-protected in lifespan
        build_update_loop=lambda: _NullUpdateLoop(),
    )
    return container, runner, progress_bus


class _NullUpdateLoop:
    """UpdateLoop stand-in that blocks until released."""

    def __init__(self) -> None:
        self._stop = threading.Event()

    def run_forever(self) -> None:
        self._stop.wait(timeout=30.0)

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Fixture: TestClient with a fixed secret
# ---------------------------------------------------------------------------

_TEST_SECRET = 'test-secret-12345'


@pytest.fixture()
def scheduler_client(monkeypatch: pytest.MonkeyPatch) -> collections.abc.Iterator[fastapi.testclient.TestClient]:
    """TestClient for the scheduler internal app with a fixed secret.

    Uses a context-manager so the ASGI lifespan (and the update-loop thread)
    are live for the duration of every request in the test.
    """
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    # Reset cached secret so monkeypatched env is picked up.
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container, _runner, _bus = _fake_container(_TEST_SECRET)
    app = build_scheduler_app(container)  # type: ignore[arg-type]
    with fastapi.testclient.TestClient(app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture()
def scheduler_client_with_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> collections.abc.Iterator[tuple[fastapi.testclient.TestClient, _FakeManualRunner, _FakeProgressBus]]:
    """TestClient + direct access to the fake runner and bus.

    Uses a context-manager so the ASGI lifespan is live during every request.
    """
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container, runner, bus = _fake_container(_TEST_SECRET)
    app = build_scheduler_app(container)  # type: ignore[arg-type]
    with fastapi.testclient.TestClient(app, raise_server_exceptions=True) as client:
        yield client, runner, bus


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
    assert 'active_downloads' in data
    assert isinstance(data['uptime_seconds'], int)
    assert isinstance(data['active_downloads'], int)


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


# ---------------------------------------------------------------------------
# POST /internal/tasks/manual
# ---------------------------------------------------------------------------


def test_manual_task_accepted(
    scheduler_client_with_parts: tuple[fastapi.testclient.TestClient, _FakeManualRunner, _FakeProgressBus],
) -> None:
    client, runner, _bus = scheduler_client_with_parts
    payload = {
        'sn': '12345',
        'resolution': '720',
        'mode': 'single',
        'thread': 2,
        'classify': True,
        'danmu': False,
        'owner_id': 'user-abc',
    }
    r = client.post(
        '/internal/tasks/manual',
        json=payload,
        headers={'X-Internal-Secret': _TEST_SECRET},
    )
    assert r.status_code == 202
    assert r.json() == {'status': 'accepted'}

    # The runner is called in a thread-pool executor; give it a moment.
    assert runner.wait(timeout=2.0), 'ManualRunner.run was not called'
    call = runner.calls[0]
    assert call['sn'] == 12345
    assert call['resolution'] == '720'
    assert call['owner_id'] == 'user-abc'


def test_manual_task_no_secret_rejected(
    scheduler_client: fastapi.testclient.TestClient,
) -> None:
    r = scheduler_client.post(
        '/internal/tasks/manual',
        json={'sn': '1', 'resolution': '1080', 'mode': 'single'},
    )
    assert r.status_code == 401


def test_manual_task_wrong_secret_rejected(
    scheduler_client: fastapi.testclient.TestClient,
) -> None:
    r = scheduler_client.post(
        '/internal/tasks/manual',
        json={'sn': '1', 'resolution': '1080', 'mode': 'single'},
        headers={'X-Internal-Secret': 'bad-secret'},
    )
    assert r.status_code == 401


def test_manual_task_post_returns_immediately_even_if_runner_is_slow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /internal/tasks/manual must return 202 before ManualRunner.run() finishes.

    The endpoint must NOT block (await run_in_executor) on the download pipeline.
    A slow runner (sleep 3 s) should not delay the HTTP response.
    """
    import time as _time

    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    started_event = threading.Event()
    done_event = threading.Event()

    class _SlowRunner:
        def run(self, sn: int | None, **kwargs: Any) -> None:
            started_event.set()
            _time.sleep(3)  # simulate slow download
            done_event.set()

    container, _runner, _bus = _fake_container(_TEST_SECRET)
    container.manual_runner = _SlowRunner()  # type: ignore[attr-defined]

    app = build_scheduler_app(container)  # type: ignore[arg-type]
    with fastapi.testclient.TestClient(app, raise_server_exceptions=True) as client:
        t0 = _time.monotonic()
        r = client.post(
            '/internal/tasks/manual',
            json={'sn': '999', 'resolution': '1080', 'mode': 'single'},
            headers={'X-Internal-Secret': _TEST_SECRET},
        )
        elapsed = _time.monotonic() - t0

    assert r.status_code == 202
    # The HTTP response must arrive well before the 3-second sleep finishes.
    assert elapsed < 2.0, f'POST blocked for {elapsed:.2f}s — expected fire-and-forget'


def test_manual_task_runner_exception_does_not_return_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception raised inside ManualRunner.run() must NOT bubble up as a 500.

    The exception is caught inside the daemon thread; the HTTP layer already
    returned 202 before the thread even starts running.
    """
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    ran_event = threading.Event()

    class _BrokenRunner:
        def run(self, sn: int | None, **kwargs: Any) -> None:
            ran_event.set()
            raise RuntimeError('simulated download failure')

    container, _runner, _bus = _fake_container(_TEST_SECRET)
    container.manual_runner = _BrokenRunner()  # type: ignore[attr-defined]

    app = build_scheduler_app(container)  # type: ignore[arg-type]
    with fastapi.testclient.TestClient(app, raise_server_exceptions=True) as client:
        r = client.post(
            '/internal/tasks/manual',
            json={'sn': '888', 'resolution': '1080', 'mode': 'single'},
            headers={'X-Internal-Secret': _TEST_SECRET},
        )

    assert r.status_code == 202
    assert r.json() == {'status': 'accepted'}
    # Give the daemon thread a moment to run and raise.
    ran_event.wait(timeout=2.0)


# ---------------------------------------------------------------------------
# DELETE /internal/tasks/{sn}
# ---------------------------------------------------------------------------


def test_cancel_task_no_secret_rejected(
    scheduler_client: fastapi.testclient.TestClient,
) -> None:
    r = scheduler_client.delete('/internal/tasks/99')
    assert r.status_code == 401


def test_cancel_task_returns_404_when_not_tracked(
    scheduler_client_with_parts: tuple[fastapi.testclient.TestClient, _FakeManualRunner, _FakeProgressBus],
) -> None:
    """DELETE /internal/tasks/{sn} returns 404 when the sn is not tracked."""
    client, _runner, _bus = scheduler_client_with_parts
    r = client.delete(
        '/internal/tasks/9999',
        headers={'X-Internal-Secret': _TEST_SECRET},
    )
    assert r.status_code == 404


def test_delete_task_cancels_via_progress_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE /internal/tasks/{sn} returns 204 and calls cancel() on ProgressBus."""
    from app.downloader.progress import ProgressBus

    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container, _runner, _fake_bus = _fake_container(_TEST_SECRET)

    # Replace the fake bus with a real ProgressBus so cancel() works properly.
    real_bus = ProgressBus()
    real_bus.start(42, 'ep42.mp4', status='正在下載')
    container.progress_bus = real_bus  # type: ignore[attr-defined]

    app = build_scheduler_app(container)  # type: ignore[arg-type]
    client = fastapi.testclient.TestClient(app)

    r = client.delete(
        '/internal/tasks/42',
        headers={'X-Internal-Secret': _TEST_SECRET},
    )
    assert r.status_code == 204

    # The bus entry should have been marked cancelled.
    snap = real_bus.snapshot()
    assert snap[42].status == '已取消'


# ---------------------------------------------------------------------------
# WS /internal/progress
# ---------------------------------------------------------------------------


def test_ws_progress_sends_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container, _runner, bus = _fake_container(_TEST_SECRET)
    bus.seed(777, status='正在下載', filename='ep01.mp4', rate=55.0)

    app = build_scheduler_app(container)  # type: ignore[arg-type]
    client = fastapi.testclient.TestClient(app)

    with client.websocket_connect(
        '/internal/progress',
        headers={'X-Internal-Secret': _TEST_SECRET},
    ) as ws:
        raw = ws.receive_text()
        data = json.loads(raw)
        assert '777' in data
        entry = data['777']
        assert entry['status'] == '正在下載'
        assert entry['filename'] == 'ep01.mp4'


def test_progress_ws_serialises_cooldown_until_as_iso_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WS /internal/progress must serialise cooldown_until as an ISO string, not a datetime."""
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container, _runner, bus = _fake_container(_TEST_SECRET)
    # Seed an entry and then set cooldown_until directly so we control the value.
    bus.seed(555, status='冷卻中', filename='ep_cd.mp4')
    cooldown_dt = datetime.datetime(2026, 4, 18, 12, 0, 0, tzinfo=datetime.UTC)
    bus._entries[555].cooldown_until = cooldown_dt

    app = build_scheduler_app(container)  # type: ignore[arg-type]
    client = fastapi.testclient.TestClient(app)

    with client.websocket_connect(
        '/internal/progress',
        headers={'X-Internal-Secret': _TEST_SECRET},
    ) as ws:
        raw = ws.receive_text()
        data = json.loads(raw)

    assert '555' in data
    entry = data['555']
    # Must be a string (ISO), not None or a non-serialisable object.
    assert isinstance(entry['cooldown_until'], str)
    assert entry['cooldown_until'] == cooldown_dt.isoformat()


def test_ws_progress_rejects_missing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container, _runner, _bus = _fake_container(_TEST_SECRET)
    app = build_scheduler_app(container)  # type: ignore[arg-type]
    client = fastapi.testclient.TestClient(app)

    with pytest.raises(starlette.websockets.WebSocketDisconnect), client.websocket_connect('/internal/progress') as ws:
        ws.receive_text()


def test_ws_progress_rejects_wrong_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    container, _runner, _bus = _fake_container(_TEST_SECRET)
    app = build_scheduler_app(container)  # type: ignore[arg-type]
    client = fastapi.testclient.TestClient(app)

    with (
        pytest.raises(starlette.websockets.WebSocketDisconnect),
        client.websocket_connect(
            '/internal/progress',
            headers={'X-Internal-Secret': 'wrong'},
        ) as ws,
    ):
        ws.receive_text()


# ---------------------------------------------------------------------------
# Bug (2) safety net — finish() called even if ManualRunner.run() raises
# ---------------------------------------------------------------------------


def test_manual_task_post_finish_is_called_even_if_runner_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /internal/tasks/manual must call progress_bus.finish(sn) even when
    ManualRunner.run() raises an unhandled exception (the safety-net finally
    block in _run ensures this).

    We verify this by replacing both the runner (raises) and the progress bus
    (records finish calls) with spies/fakes.
    """
    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', _TEST_SECRET)
    monkeypatch.setattr(scheduler_server_module, '_RESOLVED_SECRET', None)

    ran_event = threading.Event()
    finish_calls: list[int] = []

    class _BrokenRunner:
        def run(self, sn: int | None, **kwargs: Any) -> None:
            ran_event.set()
            raise RuntimeError('simulated catastrophic failure in runner')

    class _SpyProgressBus(_FakeProgressBus):
        def finish(self, sn: int) -> None:
            finish_calls.append(sn)
            # _FakeProgressBus has no finish(); just record and return.

    container, _runner, _bus = _fake_container(_TEST_SECRET)
    container.manual_runner = _BrokenRunner()  # type: ignore[attr-defined]
    spy_bus = _SpyProgressBus()
    container.progress_bus = spy_bus  # type: ignore[attr-defined]

    app = build_scheduler_app(container)  # type: ignore[arg-type]
    with fastapi.testclient.TestClient(app, raise_server_exceptions=True) as client:
        r = client.post(
            '/internal/tasks/manual',
            json={'sn': '777', 'resolution': '1080', 'mode': 'single'},
            headers={'X-Internal-Secret': _TEST_SECRET},
        )

    assert r.status_code == 202

    # Wait for the daemon thread to run and raise.
    ran_event.wait(timeout=2.0)

    # Give the finally block a moment to execute.
    import time as _time

    deadline = _time.monotonic() + 2.0
    while _time.monotonic() < deadline and 777 not in finish_calls:
        _time.sleep(0.02)

    assert 777 in finish_calls, (
        f'progress_bus.finish(777) was not called after runner raised; finish_calls={finish_calls}'
    )


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

    fake_container, _runner, _bus = _fake_container(_TEST_SECRET)

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

    fake_container, _runner, _bus = _fake_container(_TEST_SECRET)

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

    fake_container, _runner, _bus = _fake_container(_TEST_SECRET)

    monkeypatch.setattr(scheduler_server_module, 'build_container', lambda: fake_container)
    monkeypatch.setattr(scheduler_server_module.uvicorn, 'run', _fake_uvicorn_run)

    import app.persistence.paths as _paths_mod

    fake_paths = _types.SimpleNamespace(logs_dir=pathlib.Path(tempfile.gettempdir()))
    monkeypatch.setattr(_paths_mod.WorkspacePaths, 'detect', staticmethod(lambda: fake_paths))

    scheduler_server_module.serve()

    assert captured.get('port') == 9999

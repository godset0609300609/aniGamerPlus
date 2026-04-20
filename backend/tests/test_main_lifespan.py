"""Tests for the FastAPI lifespan hook that owns the background scheduler.

The rewrite split the single legacy process into ``anigamerplus`` (CLI,
runs ``UpdateLoop.run_forever`` in the foreground) and
``anigamerplus-server`` (FastAPI web dashboard). For UX parity the web
server now also spawns the periodic downloader on startup — unless
``ANIGAMERPLUS_DISABLE_SCHEDULER`` is set. These tests pin that wiring
so the split doesn't silently regress.
"""

from __future__ import annotations

import threading
import types
from typing import TYPE_CHECKING, Any

import fastapi.testclient
import pytest

from app.main import DashboardApp

if TYPE_CHECKING:
    from .conftest import FakeContainer


class _RecordingUpdateLoop:
    """Stand-in for :class:`UpdateLoop` that records ``run_forever`` calls.

    ``run_forever`` blocks on a ``threading.Event`` so the daemon thread
    the lifespan starts stays alive for the duration of the test — the
    same shape as the real loop, which only returns on ``stop()``.
    """

    def __init__(self) -> None:
        self.run_forever_calls = 0
        self.thread_names: list[str] = []
        self.thread_daemon_flags: list[bool] = []
        self._started = threading.Event()
        self._release = threading.Event()

    def run_forever(self) -> None:
        self.run_forever_calls += 1
        current = threading.current_thread()
        self.thread_names.append(current.name)
        self.thread_daemon_flags.append(current.daemon)
        self._started.set()
        # Block until the test releases us (or the process exits — the
        # thread is daemon so it'll die with the interpreter anyway).
        self._release.wait(timeout=5.0)

    def wait_until_started(self, timeout: float = 2.0) -> bool:
        return self._started.wait(timeout=timeout)

    def release(self) -> None:
        self._release.set()


def _lifespan_container(fake_container: FakeContainer, loop: _RecordingUpdateLoop) -> Any:
    """Return a container proxy that satisfies :class:`DashboardApp`.

    Adds a ``build_update_loop`` method returning ``loop`` on top of the
    minimal attribute set the dashboard factory touches.
    """
    proxy = types.SimpleNamespace(
        paths=fake_container.paths,
        logger=fake_container.logger,
        settings_repo=fake_container.settings_repo,
        sn_list_repo=fake_container.sn_list_repo,
        cookie_repo=fake_container.cookie_repo,
        database=fake_container.database,
        anime_repo=fake_container.anime_repo,
        progress_bus=fake_container.progress_bus,
        manual_runner=fake_container.manual_runner,
        build_update_loop=lambda: loop,
    )
    return proxy


def test_lifespan_spawns_scheduler_thread(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the env-var unset, the lifespan starts one daemon thread."""
    monkeypatch.delenv('ANIGAMERPLUS_DISABLE_SCHEDULER', raising=False)

    loop = _RecordingUpdateLoop()
    proxy = _lifespan_container(fake_container, loop)
    dashboard = DashboardApp(proxy)
    app = dashboard.app

    try:
        with fastapi.testclient.TestClient(app):
            # TestClient's context-manager semantics run the lifespan
            # startup phase; the scheduler thread should be live before
            # we return.
            assert loop.wait_until_started(), 'scheduler thread did not call run_forever within timeout'
            thread = app.state.scheduler_thread
            assert isinstance(thread, threading.Thread)
            assert thread.daemon is True
            assert thread.is_alive()
            assert loop.run_forever_calls == 1
            assert loop.thread_daemon_flags == [True]
    finally:
        # Unblock the loop so the daemon thread exits cleanly; even
        # without this, daemon=True would let the process exit anyway.
        loop.release()


def test_lifespan_respects_env_var_disable(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ANIGAMERPLUS_DISABLE_SCHEDULER=1`` skips the scheduler spawn."""
    monkeypatch.setenv('ANIGAMERPLUS_DISABLE_SCHEDULER', '1')

    loop = _RecordingUpdateLoop()
    proxy = _lifespan_container(fake_container, loop)
    dashboard = DashboardApp(proxy)
    app = dashboard.app

    with fastapi.testclient.TestClient(app):
        assert getattr(app.state, 'scheduler_thread', None) is None
        assert loop.run_forever_calls == 0

"""Tests for :class:`SignalHandler`."""

from __future__ import annotations

import atexit
import pathlib
import signal
import threading
from collections.abc import Iterator

import pytest

from app.logging_ import Logger
from app.scheduler.signals import SignalHandler


def _logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture(autouse=True)
def _restore_handlers() -> Iterator[None]:
    """Snapshot and restore the original SIGINT/SIGTERM handlers for every test."""
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGINT, original_sigint)
    signal.signal(signal.SIGTERM, original_sigterm)


def test_install_registers_sigint_and_sigterm(tmp_path: pathlib.Path) -> None:
    handler = SignalHandler(_logger(tmp_path))
    handler.install()
    sigint_handler = signal.getsignal(signal.SIGINT)
    sigterm_handler = signal.getsignal(signal.SIGTERM)
    assert callable(sigint_handler)
    assert callable(sigterm_handler)
    # Bound-method identity: compare the underlying function + __self__.
    assert getattr(sigint_handler, '__func__', None) is SignalHandler._handler
    assert getattr(sigint_handler, '__self__', None) is handler
    assert getattr(sigterm_handler, '__func__', None) is SignalHandler._handler
    assert getattr(sigterm_handler, '__self__', None) is handler


def test_callbacks_run_in_registration_order(tmp_path: pathlib.Path) -> None:
    handler = SignalHandler(_logger(tmp_path))
    order: list[int] = []
    handler.on_exit(lambda: order.append(1))
    handler.on_exit(lambda: order.append(2))
    handler.on_exit(lambda: order.append(3))

    # Unregister from atexit — we don't want the test process itself
    # to invoke these on teardown.
    for cb in list(handler._callbacks):
        atexit.unregister(cb)

    with pytest.raises(SystemExit):
        handler._handler(signal.SIGINT, None)
    assert order == [1, 2, 3]


def test_failing_callback_does_not_skip_subsequent(tmp_path: pathlib.Path) -> None:
    handler = SignalHandler(_logger(tmp_path))
    fires: list[str] = []

    def good_a() -> None:
        fires.append('a')

    def bad() -> None:
        fires.append('bad')
        raise RuntimeError('boom')

    def good_b() -> None:
        fires.append('b')

    handler.on_exit(good_a)
    handler.on_exit(bad)
    handler.on_exit(good_b)

    for cb in list(handler._callbacks):
        atexit.unregister(cb)

    with pytest.raises(SystemExit):
        handler._handler(signal.SIGINT, None)
    assert fires == ['a', 'bad', 'b']


def test_install_on_non_main_thread_is_noop(tmp_path: pathlib.Path) -> None:
    handler = SignalHandler(_logger(tmp_path))

    errors: list[BaseException] = []

    def run() -> None:
        try:
            handler.install()
        except BaseException as exc:  # noqa: BLE001 — we want to capture ANYTHING
            errors.append(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=5)

    assert errors == []
    # Installed flag must stay False — nothing was registered.
    assert handler._installed is False

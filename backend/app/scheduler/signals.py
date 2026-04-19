"""Signal handler installer.

Installs SIGINT / SIGTERM handlers in the main thread that run a chain
of user-registered cleanup callbacks, then ``sys.exit(0)``. Mirrors the
legacy ``user_exit`` + ``kill_gost`` setup at the bottom of
``aniGamerPlus.py`` but decouples cleanup registration from the
signal-wiring code.
"""

from __future__ import annotations

import atexit
import collections.abc
import signal
import sys
import threading
import typing as T

if T.TYPE_CHECKING:
    from ..logging_ import Logger


class SignalHandler:
    """Installs SIGINT/SIGTERM handlers that run cleanup callbacks then exit."""

    def __init__(self, logger: Logger) -> None:
        self._logger = logger
        self._callbacks: list[collections.abc.Callable[[], None]] = []
        self._installed = False

    # ------------------------------------------------------------------ public

    def on_exit(self, callback: collections.abc.Callable[[], None]) -> None:
        """Register a cleanup callback. Multiple callbacks run in registration order."""
        self._callbacks.append(callback)
        atexit.register(callback)

    def install(self) -> None:
        """Install the handlers. No-op when not on the main thread.

        Python's ``signal.signal`` raises ``ValueError: signal only works
        in main thread`` when called off-thread; FastAPI startup runs in a
        worker thread so we short-circuit instead of propagating.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        if self._installed:
            return
        signal.signal(signal.SIGINT, self._handler)
        signal.signal(signal.SIGTERM, self._handler)
        self._installed = True

    # ------------------------------------------------------------------ internals

    def _handler(self, signum: int, frame: object | None) -> None:
        """Run every registered callback, log any failures, then exit."""
        self._logger.info(
            None,
            '程序終止',
            f'收到信號 {signum}; 執行清理回調',
            display=False,
        )
        for callback in list(self._callbacks):
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                self._logger.error(
                    None,
                    '程序終止',
                    f'cleanup callback failed: {exc}',
                    display=False,
                )
        sys.exit(0)

"""In-process watchdog for the scheduler UpdateLoop.

The watchdog detects a stalled update loop and hard-exits the process so
that the Docker restart policy (``restart: unless-stopped``) can pull it
back up automatically.

Usage::

    watchdog = SchedulerWatchdog(logger)
    watchdog.start()          # launches daemon thread
    # … inside UpdateLoop.run_forever …
    watchdog.beat()           # call at the top of each iteration
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
import typing as T

if T.TYPE_CHECKING:
    from ..logging_ import Logger

_log = logging.getLogger(__name__)


class SchedulerWatchdog:
    """Periodic liveness watchdog for the scheduler update loop.

    Attributes
    ----------
    HEARTBEAT_INTERVAL_S:
        How often the daemon thread checks the last beat timestamp.
    STALE_THRESHOLD_S:
        Age (in seconds) above which a missing heartbeat is declared
        *stale* and the process is killed.  Defaults to 3× the heartbeat
        interval — a brief pause (GC, I/O) won't trigger a panic, but a
        genuinely locked loop will be caught within one check cycle after
        the threshold expires.
    """

    HEARTBEAT_INTERVAL_S: float = 30
    STALE_THRESHOLD_S: float = 180  # 3× heartbeat → 3 missed beats = stale

    def __init__(self, logger: Logger | None = None) -> None:
        self._logger = logger
        self._last_beat_ts: float = time.monotonic()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event = threading.Event()

    # ------------------------------------------------------------------ public

    def beat(self) -> None:
        """Record a heartbeat. Call at the top of each UpdateLoop iteration."""
        self._last_beat_ts = time.monotonic()

    def start(self) -> None:
        """Start the daemon checker thread.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._last_beat_ts = time.monotonic()  # reset on (re)start
        self._stop_event.clear()
        t = threading.Thread(
            target=self._check_loop,
            name='scheduler-watchdog',
            daemon=True,
        )
        t.start()
        self._thread = t

    def stop(self) -> None:
        """Signal the checker thread to exit on its next wake-up."""
        self._stop_event.set()

    def last_beat_age_seconds(self) -> float:
        """Return how many seconds ago the last beat was recorded."""
        return time.monotonic() - self._last_beat_ts

    # ------------------------------------------------------------------ internals

    def _check_loop(self) -> None:
        """Daemon thread body — checks for stale heartbeat periodically.

        Sleeps in ``HEARTBEAT_INTERVAL_S`` increments; exits cleanly when
        ``_stop_event`` is set (e.g. by :meth:`stop` in tests).
        """
        while not self._stop_event.wait(timeout=self.HEARTBEAT_INTERVAL_S):
            age = self.last_beat_age_seconds()
            if age > self.STALE_THRESHOLD_S:
                self._panic(age)

    def _panic(self, age: float) -> None:
        """Log a CRITICAL message and hard-exit the process.

        ``os._exit(1)`` is intentional — it bypasses Python shutdown hooks
        so Docker's restart policy fires immediately without waiting for
        cleanup that may itself be stalled.
        """
        msg = (
            f'UpdateLoop heartbeat stale for {age:.0f}s (threshold={self.STALE_THRESHOLD_S}s) — triggering hard restart'
        )
        if self._logger is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort log before hard exit
                self._logger.error(None, 'Watchdog', msg, display=False)
        _log.critical(msg)
        os._exit(1)  # noqa: SLF001 — intentional hard exit

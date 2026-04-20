"""Tests for the SchedulerWatchdog (app/scheduler/watchdog.py)."""

from __future__ import annotations

import threading
import time

import pytest

from app.scheduler.watchdog import SchedulerWatchdog

# ---------------------------------------------------------------------------
# beat() updates last_beat
# ---------------------------------------------------------------------------


def test_beat_updates_last_beat_age() -> None:
    """beat() resets the heartbeat timestamp so age stays near zero."""
    wd = SchedulerWatchdog()
    # Age should be very small right after construction.
    initial_age = wd.last_beat_age_seconds()
    assert initial_age < 1.0

    # Manually back-date the last beat.
    wd._last_beat_ts -= 10.0  # type: ignore[attr-defined]
    assert wd.last_beat_age_seconds() > 9.0

    # After beat(), age resets.
    wd.beat()
    assert wd.last_beat_age_seconds() < 1.0


# ---------------------------------------------------------------------------
# Consecutive beats → no panic
# ---------------------------------------------------------------------------


def test_consecutive_beats_do_not_panic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated beat() calls must never trigger _panic."""
    panic_called = False

    def fake_exit(code: int) -> None:
        nonlocal panic_called
        panic_called = True

    monkeypatch.setattr('os._exit', fake_exit)

    wd = SchedulerWatchdog()
    for _ in range(5):
        wd.beat()
        time.sleep(0.01)

    assert not panic_called, '_panic must not fire while beats are fresh'


# ---------------------------------------------------------------------------
# Stale heartbeat → _panic() is called
# ---------------------------------------------------------------------------


def test_stale_heartbeat_triggers_panic(monkeypatch: pytest.MonkeyPatch) -> None:
    """When last_beat_age > STALE_THRESHOLD_S, _panic must call os._exit(1)."""
    exit_codes: list[int] = []

    def fake_exit(code: int) -> None:
        exit_codes.append(code)

    monkeypatch.setattr('os._exit', fake_exit)

    wd = SchedulerWatchdog()
    # Force the last beat far into the past (beyond threshold).
    wd._last_beat_ts -= wd.STALE_THRESHOLD_S + 1  # type: ignore[attr-defined]

    # Directly invoke _panic as the daemon thread would.
    wd._panic(wd.last_beat_age_seconds())

    assert exit_codes == [1], f'Expected os._exit(1), got: {exit_codes}'


# ---------------------------------------------------------------------------
# _check_loop triggers panic when threshold exceeded
# ---------------------------------------------------------------------------


def test_check_loop_panics_on_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Daemon thread's _check_loop fires _panic when heartbeat is stale."""
    panicked = threading.Event()

    def fake_panic(self: SchedulerWatchdog, age: float) -> None:
        panicked.set()
        # Stop the loop cleanly after signalling panic — avoids SystemExit in
        # a daemon thread which pytest would surface as an unhandled exception.
        self.stop()

    monkeypatch.setattr(SchedulerWatchdog, '_panic', fake_panic)

    wd = SchedulerWatchdog()
    # Use a very short interval so the test doesn't hang.
    wd.HEARTBEAT_INTERVAL_S = 0.05  # type: ignore[assignment]
    wd.STALE_THRESHOLD_S = 0.0  # everything is immediately stale

    # Run _check_loop in a thread and let it fire.
    t = threading.Thread(target=wd._check_loop, daemon=True)
    t.start()

    assert panicked.wait(timeout=2.0), 'Watchdog did not panic within 2 s'
    t.join(timeout=1.0)  # wait for clean exit


# ---------------------------------------------------------------------------
# start() is idempotent
# ---------------------------------------------------------------------------


def test_start_is_idempotent() -> None:
    """Calling start() twice must not create a second daemon thread."""
    wd = SchedulerWatchdog()
    wd.start()
    thread1 = wd._thread  # type: ignore[attr-defined]
    wd.start()
    thread2 = wd._thread  # type: ignore[attr-defined]
    assert thread1 is thread2, 'start() should not replace a living thread'
    wd.stop()  # clean up daemon thread so it doesn't bleed into other tests


# ---------------------------------------------------------------------------
# last_beat_age_seconds returns a monotonic increasing value over time
# ---------------------------------------------------------------------------


def test_last_beat_age_monotonically_increases() -> None:
    """last_beat_age_seconds grows over (wall-clock) time between beats."""
    wd = SchedulerWatchdog()
    age1 = wd.last_beat_age_seconds()
    time.sleep(0.05)
    age2 = wd.last_beat_age_seconds()
    assert age2 > age1, 'Age should increase as time passes'

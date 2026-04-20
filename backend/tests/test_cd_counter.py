"""Tests for :class:`DownloadCooldown`."""

from __future__ import annotations

import pathlib
import threading
from collections.abc import Callable

from app.logging_ import Logger
from app.scheduler.cd_counter import DownloadCooldown


def _logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


def _fake_cooldown(
    tmp_path: pathlib.Path,
    seconds: int,
) -> tuple[DownloadCooldown, list[float]]:
    """Return a cooldown with injectable sleep for deterministic tests.

    Returns ``(cooldown, slept_for)`` where ``slept_for`` collects every
    value passed to the fake sleep.
    """
    slept_for: list[float] = []
    cooldown = DownloadCooldown(seconds, _logger(tmp_path))
    cooldown._set_sleep(lambda s: slept_for.append(s))
    return cooldown, slept_for


def test_schedule_release_invokes_callback_after_sleep(
    tmp_path: pathlib.Path,
) -> None:
    cooldown = DownloadCooldown(3, _logger(tmp_path))

    slept_for: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept_for.append(seconds)

    cooldown._set_sleep(fake_sleep)

    done = threading.Event()

    def cb() -> None:
        done.set()

    cooldown.schedule_release(cb)
    assert done.wait(timeout=2)
    assert slept_for == [3]


def test_schedule_release_uses_daemon_thread(tmp_path: pathlib.Path) -> None:
    cooldown = DownloadCooldown(0, _logger(tmp_path))
    # Capture the spawned thread by monkeypatching threading.Thread.
    spawned: list[threading.Thread] = []
    real_thread = threading.Thread

    def capture(*args: object, **kwargs: object) -> threading.Thread:
        t = real_thread(*args, **kwargs)  # type: ignore[arg-type]
        spawned.append(t)
        return t

    threading.Thread = capture  # type: ignore[assignment]
    try:
        cooldown.schedule_release(lambda: None)
    finally:
        threading.Thread = real_thread  # type: ignore[assignment]

    assert spawned
    assert spawned[-1].daemon


def test_multiple_scheduled_releases_are_independent(
    tmp_path: pathlib.Path,
) -> None:
    cooldown = DownloadCooldown(0, _logger(tmp_path))
    fires: list[int] = []
    lock = threading.Lock()
    events = [threading.Event() for _ in range(3)]

    for idx in range(3):

        def make_cb(i: int) -> Callable[[], None]:
            def _cb() -> None:
                with lock:
                    fires.append(i)
                events[i].set()

            return _cb

        cooldown.schedule_release(make_cb(idx))

    for evt in events:
        assert evt.wait(timeout=2)
    assert sorted(fires) == [0, 1, 2]


# ---------------------------------------------------------------------------
# wait() — full-duration sleep every call
# ---------------------------------------------------------------------------


def test_wait_zero_seconds_is_noop(tmp_path: pathlib.Path) -> None:
    cooldown, slept_for = _fake_cooldown(tmp_path, 0)
    cooldown.wait()
    cooldown.wait()
    assert slept_for == []


def test_wait_sleeps_full_duration(tmp_path: pathlib.Path) -> None:
    """wait() always sleeps the configured duration."""
    cooldown, slept_for = _fake_cooldown(tmp_path, 10)
    cooldown.wait()
    assert slept_for == [10.0]


def test_wait_waits_full_duration_every_time(tmp_path: pathlib.Path) -> None:
    """Each call to wait() sleeps the full configured duration, not a remainder."""
    cooldown, slept_for = _fake_cooldown(tmp_path, 5)

    cooldown.wait()
    cooldown.wait()

    # Both calls must sleep the full 5 seconds — no time-aware subtraction.
    assert slept_for == [5.0, 5.0]


def test_label_in_log_message(tmp_path: pathlib.Path) -> None:
    """Custom ``label`` must appear in both the log tag and the log message."""
    log_calls: list[tuple[str, str]] = []

    class _SpyLogger:
        def info(self, sn: object, tag: str, msg: str, **kwargs: object) -> None:
            log_calls.append((tag, msg))

    logger = _SpyLogger()
    cooldown = DownloadCooldown(10, logger, label='解析冷卻')  # type: ignore[arg-type]
    cooldown._set_sleep(lambda _s: None)

    cooldown.wait()

    assert log_calls, 'expected at least one log emit'
    tag, msg = log_calls[0]
    assert tag == '解析冷卻', f"expected tag '解析冷卻', got {tag!r}"
    assert '解析冷卻' in msg, f"expected '解析冷卻' in message, got {msg!r}"
    assert '下載冷卻' not in tag, 'default label must not bleed into custom label'


def test_wait_thread_safety_serialises_concurrent_callers(
    tmp_path: pathlib.Path,
) -> None:
    """Two threads calling ``wait`` concurrently must serialise through the
    internal lock; both callers each sleep the full configured duration."""
    slept_values: list[float] = []
    lock_for_append = threading.Lock()

    cooldown = DownloadCooldown(1, _logger(tmp_path))

    def fake_sleep(s: float) -> None:
        with lock_for_append:
            slept_values.append(s)

    cooldown._set_sleep(fake_sleep)

    threads = [threading.Thread(target=cooldown.wait, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    # Both threads must have slept the full 1-second duration.
    assert slept_values == [1.0, 1.0]


# ---------------------------------------------------------------------------
# wait() — progress_bus integration
# ---------------------------------------------------------------------------


def test_wait_calls_set_cooldown_before_sleep_and_clear_after(
    tmp_path: pathlib.Path,
) -> None:
    """When both progress_bus and sn are provided, wait() must call
    set_cooldown just before the sleep and clear_cooldown in the finally."""
    from app.downloader.progress import ProgressBus

    bus = ProgressBus()
    bus.start(1, 'ep01.mp4', status='正在解析')

    set_calls: list[tuple[int, float]] = []
    clear_calls: list[int] = []

    # Patch set_cooldown / clear_cooldown to record calls.
    _orig_set = bus.set_cooldown
    _orig_clear = bus.clear_cooldown

    def _spy_set(sn: int, seconds: float) -> None:
        set_calls.append((sn, seconds))
        _orig_set(sn, seconds)

    def _spy_clear(sn: int) -> None:
        clear_calls.append(sn)
        _orig_clear(sn)

    bus.set_cooldown = _spy_set  # type: ignore[method-assign]
    bus.clear_cooldown = _spy_clear  # type: ignore[method-assign]

    cooldown, slept_for = _fake_cooldown(tmp_path, 5)
    cooldown.wait(progress_bus=bus, sn=1)

    # set_cooldown must have been called with sn=1 and approximately 5.0 s.
    assert len(set_calls) == 1
    assert set_calls[0][0] == 1
    assert abs(set_calls[0][1] - 5.0) < 1e-9

    # clear_cooldown must have been called once (in the finally).
    assert clear_calls == [1]

    # The sleep still happened.
    assert slept_for == [5.0]


def test_wait_clears_cooldown_even_if_sleep_raises(
    tmp_path: pathlib.Path,
) -> None:
    """clear_cooldown must be called in a finally — even when sleep raises."""
    from app.downloader.progress import ProgressBus

    bus = ProgressBus()
    bus.start(3, 'ep03.mp4')

    clear_calls: list[int] = []
    _orig_clear = bus.clear_cooldown

    def _spy_clear(sn: int) -> None:
        clear_calls.append(sn)
        _orig_clear(sn)

    bus.clear_cooldown = _spy_clear  # type: ignore[method-assign]

    cooldown, _ = _fake_cooldown(tmp_path, 5)

    def _raising_sleep(_s: float) -> None:
        raise RuntimeError('boom')

    cooldown._set_sleep(_raising_sleep)

    try:
        cooldown.wait(progress_bus=bus, sn=3)
    except RuntimeError:
        pass

    # clear_cooldown must still have been called despite the exception.
    assert clear_calls == [3]


def test_wait_without_progress_bus_works_as_before(
    tmp_path: pathlib.Path,
) -> None:
    """Calling wait() without progress_bus / sn must still sleep normally."""
    cooldown, slept_for = _fake_cooldown(tmp_path, 3)
    cooldown.wait()
    assert slept_for == [3.0]


# ---------------------------------------------------------------------------
# wait() — status_during parameter
# ---------------------------------------------------------------------------


def test_wait_sets_status_during_inside_lock(tmp_path: pathlib.Path) -> None:
    """update_status must be called (with status_during) AND set_cooldown must
    both be called before sleep, in that order, and all inside the lock."""
    from app.downloader.progress import ProgressBus

    bus = ProgressBus()
    bus.start(10, 'ep10.mp4', status='正在解析')

    call_order: list[str] = []

    _orig_update = bus.update_status
    _orig_set = bus.set_cooldown

    def _spy_update(sn: int, status: str) -> None:
        call_order.append(f'update_status:{status}')
        _orig_update(sn, status)

    def _spy_set(sn: int, seconds: float) -> None:
        call_order.append('set_cooldown')
        _orig_set(sn, seconds)

    bus.update_status = _spy_update  # type: ignore[method-assign]
    bus.set_cooldown = _spy_set  # type: ignore[method-assign]

    cooldown, slept_for = _fake_cooldown(tmp_path, 5)
    cooldown.wait(progress_bus=bus, sn=10, status_during='下載冷卻')

    # update_status with "下載冷卻" must have been called.
    assert 'update_status:下載冷卻' in call_order
    # set_cooldown must follow immediately after.
    assert 'set_cooldown' in call_order
    update_idx = call_order.index('update_status:下載冷卻')
    set_idx = call_order.index('set_cooldown')
    assert update_idx < set_idx, 'update_status must be called before set_cooldown'
    # Sleep still happened.
    assert slept_for == [5.0]


def test_wait_without_status_during_does_not_update_status(
    tmp_path: pathlib.Path,
) -> None:
    """When status_during is omitted, update_status must NOT be called."""
    from app.downloader.progress import ProgressBus

    bus = ProgressBus()
    bus.start(20, 'ep20.mp4', status='正在解析')

    update_calls: list[tuple[int, str]] = []
    _orig_update = bus.update_status

    def _spy_update(sn: int, status: str) -> None:
        update_calls.append((sn, status))
        _orig_update(sn, status)

    bus.update_status = _spy_update  # type: ignore[method-assign]

    cooldown, _ = _fake_cooldown(tmp_path, 5)
    # No status_during — update_status must not fire.
    cooldown.wait(progress_bus=bus, sn=20)

    assert update_calls == [], f'update_status must not be called without status_during; got {update_calls!r}'


def test_wait_second_caller_does_not_flip_status_until_lock_acquired(
    tmp_path: pathlib.Path,
) -> None:
    """Two threads calling wait(status_during=...) must each flip their own
    status only after acquiring the lock — not while blocked on it.

    Thread A acquires the lock first and holds it during its fake sleep.
    We snapshot thread B's status while A is sleeping and assert B is still
    at its pre-wait status, proving the status flip is inside the lock.
    """
    from app.downloader.progress import ProgressBus

    bus = ProgressBus()
    bus.start(30, 'epA.mp4', status='正在解析')
    bus.start(31, 'epB.mp4', status='正在解析')

    cooldown = DownloadCooldown(1, _logger(tmp_path))

    # Thread A's sleep window: while A sleeps, capture B's status.
    b_status_while_a_sleeps: list[str] = []
    a_sleeping = threading.Event()
    a_may_wake = threading.Event()

    def _sleep_a(s: float) -> None:
        a_sleeping.set()  # signal that A is now sleeping (holds lock)
        a_may_wake.wait(timeout=5)  # wait for test to take snapshot of B

    def _sleep_b(s: float) -> None:
        pass  # B's sleep is instantaneous; we only care about lock ordering

    # Inject the same fake sleep; A goes first so the lock is held during its sleep.
    # We alternate by using a counter.
    call_count = 0
    lock_for_count = threading.Lock()

    def _sleep_dispatch(s: float) -> None:
        nonlocal call_count
        with lock_for_count:
            call_count += 1
            is_first = call_count == 1
        if is_first:
            _sleep_a(s)
        else:
            _sleep_b(s)

    cooldown._set_sleep(_sleep_dispatch)

    # Start thread A — it will acquire the internal lock, flip sn=30 to
    # "下載冷卻", then block in _sleep_a.
    thread_a = threading.Thread(
        target=cooldown.wait,
        kwargs={'progress_bus': bus, 'sn': 30, 'status_during': '下載冷卻'},
        daemon=True,
    )
    thread_a.start()

    # Wait until A is holding the lock and sleeping.
    assert a_sleeping.wait(timeout=5), 'Thread A never started sleeping'

    # Start thread B — it should be blocked on acquiring the lock.
    # B's sn=31 must still show "正在解析" because B hasn't entered the lock yet.
    thread_b = threading.Thread(
        target=cooldown.wait,
        kwargs={'progress_bus': bus, 'sn': 31, 'status_during': '下載冷卻'},
        daemon=True,
    )
    thread_b.start()

    # Give B a moment to reach the lock (but not hold it).
    import time

    time.sleep(0.05)

    snap = bus.snapshot()
    entry_b = snap.get(31)
    if entry_b is not None:
        b_status_while_a_sleeps.append(entry_b.status)

    # Allow A to finish; B will then acquire the lock and run.
    a_may_wake.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    # While A held the lock, B's status must still be "正在解析" (pre-wait).
    assert b_status_while_a_sleeps == ['正在解析'], (
        f"Expected B status to be '正在解析' while A holds the lock, got {b_status_while_a_sleeps!r}"
    )


# ---------------------------------------------------------------------------
# Mutation-kill tests — added to achieve full-kill on cd_counter.py
# ---------------------------------------------------------------------------


def test_default_label_is_exact_string(tmp_path: pathlib.Path) -> None:
    """Default label must be exactly '下載冷卻', not any other string.

    Kills: __init__ mutmut_1 (label = 'XX下載冷卻XX').
    """
    cooldown = DownloadCooldown(5, _logger(tmp_path))
    assert cooldown._label == '下載冷卻'


def test_sleep_callable_is_set_on_init(tmp_path: pathlib.Path) -> None:
    """_sleep must be set to time.sleep (callable) after __init__, not None.

    Kills: __init__ mutmut_12 (_sleep = None).
    """
    import time as _time

    cooldown = DownloadCooldown(5, _logger(tmp_path))
    # Must be a callable — calling it must not raise AttributeError
    assert callable(cooldown._sleep)
    # Must be the real time.sleep (or injected via _set_sleep)
    assert cooldown._sleep is _time.sleep


def test_wait_display_false_on_log(tmp_path: pathlib.Path) -> None:
    """wait() must log with display=False (not True or None).

    Kills: wait mutmut_7 (display=None), mutmut_11 (removed), mutmut_12 (display=True).
    """
    log_calls: list[dict] = []

    class _SpyLogger:
        def info(self, sn: object, tag: str, msg: str, *, display: bool = True, **kw: object) -> None:
            log_calls.append({'tag': tag, 'display': display})

    cooldown = DownloadCooldown(3, _SpyLogger(), label='测试冷卻')  # type: ignore[arg-type]
    cooldown._set_sleep(lambda _: None)
    cooldown.wait()

    assert log_calls, 'wait() must emit at least one log'
    # All log emissions from wait() must use display=False (background log, not UI toast)
    for call in log_calls:
        assert call['display'] is False, (
            f"wait() logged with display={call['display']!r}, expected False"
        )


def test_wait_progress_bus_requires_both_bus_and_sn(tmp_path: pathlib.Path) -> None:
    """progress_bus + sn guard must use AND, not OR.

    Kills: wait mutmut_13 (and → or in 'if progress_bus is not None and sn is not None').

    When only sn is provided (no progress_bus), calling set_cooldown would AttributeError;
    the guard must short-circuit on progress_bus=None.
    """
    from app.downloader.progress import ProgressBus

    bus = ProgressBus()
    bus.start(42, 'ep42.mp4')

    cooldown, slept_for = _fake_cooldown(tmp_path, 2)

    # With progress_bus=None and a valid sn — must NOT try to call set_cooldown.
    # If the guard were `or`, it would try bus.set_cooldown with bus=None → AttributeError.
    cooldown.wait(progress_bus=None, sn=42)  # should not raise
    assert slept_for == [2.0]

    # With a valid bus and sn=None — must NOT try to call set_cooldown.
    slept_for.clear()
    cooldown.wait(progress_bus=bus, sn=None)  # should not raise
    assert slept_for == [2.0]


def test_wait_update_status_passes_sn_not_none(tmp_path: pathlib.Path) -> None:
    """update_status must be called with the real sn, not None.

    Kills: wait mutmut_17 (update_status(None, status_during)).
    """
    from app.downloader.progress import ProgressBus

    bus = ProgressBus()
    bus.start(50, 'ep50.mp4', status='正在解析')

    update_calls: list[tuple[int | None, str]] = []
    _orig_update = bus.update_status

    def _spy(sn: int | None, status: str) -> None:
        update_calls.append((sn, status))
        if sn is not None:
            _orig_update(sn, status)

    bus.update_status = _spy  # type: ignore[method-assign]

    cooldown, _ = _fake_cooldown(tmp_path, 1)
    cooldown.wait(progress_bus=bus, sn=50, status_during='下載冷卻')

    assert len(update_calls) == 1
    called_sn, called_status = update_calls[0]
    assert called_sn == 50, f'update_status sn must be 50, got {called_sn!r}'
    assert called_status == '下載冷卻'


def test_wait_finally_clear_requires_both_bus_and_sn(tmp_path: pathlib.Path) -> None:
    """clear_cooldown guard in the finally block must use AND, not OR.

    Kills: wait mutmut_26 (and → or in 'if progress_bus is not None and sn is not None').

    When progress_bus=None + valid sn, using OR would cause clear_cooldown call on None.
    """
    # progress_bus=None, valid sn — if guard is OR, clear_cooldown(42) on None raises
    cooldown, _ = _fake_cooldown(tmp_path, 1)
    cooldown.wait(progress_bus=None, sn=42)  # must not raise

    # valid bus, sn=None — if guard is OR, clear_cooldown(None) would be wrong
    from app.downloader.progress import ProgressBus

    bus = ProgressBus()
    bus.start(43, 'ep43.mp4')
    clear_calls: list[int | None] = []
    _orig_clear = bus.clear_cooldown

    def _spy_clear(sn: int) -> None:
        clear_calls.append(sn)
        _orig_clear(sn)

    bus.clear_cooldown = _spy_clear  # type: ignore[method-assign]
    cooldown.wait(progress_bus=bus, sn=None)
    # clear_cooldown must NOT have been called when sn=None
    assert clear_calls == [], f'clear_cooldown must not fire when sn=None, got {clear_calls!r}'


def test_schedule_release_skips_sleep_when_zero_seconds(tmp_path: pathlib.Path) -> None:
    """When seconds == 0, schedule_release must NOT call sleep at all.

    Kills: schedule_release mutmut_1 (> 0 → >= 0) and mutmut_2 (> 0 → > 1).
    Both mutants would cause sleep(0) to be called when seconds == 0.
    """
    slept_for: list[float] = []
    done = threading.Event()

    cooldown = DownloadCooldown(0, _logger(tmp_path))
    cooldown._set_sleep(lambda s: slept_for.append(s))
    cooldown.schedule_release(done.set)

    assert done.wait(timeout=2)
    assert slept_for == [], (
        f'schedule_release must not sleep when seconds == 0, but slept_for = {slept_for!r}'
    )


def test_schedule_release_sleeps_exactly_configured_seconds(tmp_path: pathlib.Path) -> None:
    """schedule_release must sleep exactly the configured seconds (not 0 when seconds == 2).

    Kills: schedule_release mutmut_2 (> 0 → > 1): with seconds=2 this mutant still sleeps,
    so this test is a belt-and-suspenders kill confirming the boundary at 1.
    """
    slept_for: list[float] = []
    done = threading.Event()

    cooldown = DownloadCooldown(1, _logger(tmp_path))
    cooldown._set_sleep(lambda s: slept_for.append(s))
    cooldown.schedule_release(done.set)

    assert done.wait(timeout=2)
    assert slept_for == [1.0], (
        f'schedule_release must sleep 1.0 s when seconds == 1, got {slept_for!r}'
    )


def test_wait_reads_seconds_from_provider_each_call(tmp_path: pathlib.Path) -> None:
    """When a callable is passed as ``seconds``, wait() must call it fresh on
    every invocation so live config changes take effect without restarting.

    The provider returns 3 on the first call and 7 on the second; both sleep
    durations must match the value the provider returned at that moment.
    """
    call_count = 0

    def _provider() -> int:
        nonlocal call_count
        call_count += 1
        return 3 if call_count == 1 else 7

    slept_for: list[float] = []
    cooldown = DownloadCooldown(_provider, _logger(tmp_path))
    cooldown._set_sleep(lambda s: slept_for.append(s))

    cooldown.wait()
    cooldown.wait()

    assert slept_for == [3.0, 7.0], (
        f'Expected [3.0, 7.0] to reflect provider values, got {slept_for!r}'
    )


def test_schedule_release_error_log_uses_label_and_message_and_display_false(
    tmp_path: pathlib.Path,
) -> None:
    """When the release callback raises, schedule_release must log the error with:
    - sn = None
    - tag = self._label
    - detail containing 'release callback failed'
    - display = False

    Kills: schedule_release mutmut_4 (label → None), mutmut_5 (message → None),
    mutmut_6 (display=False → None), mutmut_7 (removed sn arg), mutmut_8 (removed label),
    mutmut_9 (removed message), mutmut_10 (removed display=), mutmut_11 (display=True).
    """
    error_calls: list[dict] = []

    class _SpyLogger:
        def error(
            self,
            sn: object,
            tag: str,
            detail: str = '',
            *,
            display: bool = True,
            **kw: object,
        ) -> None:
            error_calls.append({'sn': sn, 'tag': tag, 'detail': detail, 'display': display})

    cooldown = DownloadCooldown(0, _SpyLogger(), label='error_label')  # type: ignore[arg-type]

    bomb = RuntimeError('test-bomb')
    done = threading.Event()

    def _raising_cb() -> None:
        done.set()
        raise bomb

    cooldown.schedule_release(_raising_cb)
    assert done.wait(timeout=2)
    # Give the thread time to reach the except block
    import time
    time.sleep(0.05)

    assert len(error_calls) == 1, f'Expected 1 error log, got {error_calls!r}'
    call = error_calls[0]
    assert call['sn'] is None, f"sn must be None, got {call['sn']!r}"
    assert call['tag'] == 'error_label', f"tag must be 'error_label', got {call['tag']!r}"
    assert 'release callback failed' in call['detail'], (
        f"detail must mention 'release callback failed', got {call['detail']!r}"
    )
    assert call['display'] is False, (
        f"display must be False (background log), got {call['display']!r}"
    )

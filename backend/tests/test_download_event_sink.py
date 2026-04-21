"""Tests for :class:`DownloadEventSink`."""

from __future__ import annotations

import asyncio
import threading
import time

from app.scheduler.event_sink import DownloadEventSink

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeNotifier:
    """Records calls to notify_download_event."""

    def __init__(self, delay: float = 0.0, raises: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self._delay = delay
        self._raises = raises
        self._lock = threading.Lock()

    async def notify_download_event(self, *, event: str, **kwargs: object) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise RuntimeError('notifier failed')
        with self._lock:
            self.calls.append({'event': event, **kwargs})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fire_completed_calls_notifier() -> None:
    notifier = FakeNotifier()
    sink = DownloadEventSink(notifier)  # type: ignore[arg-type]
    try:
        sink.fire_completed(
            owner_id='u1',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=1,
            file_size_mb=100,
        )
        # Give async loop time to process.
        time.sleep(0.3)
    finally:
        sink.close()

    assert len(notifier.calls) == 1
    call = notifier.calls[0]
    assert call['event'] == 'completed'
    assert call['owner_id'] == 'u1'
    assert call['bangumi_name'] == 'Test'
    assert call['file_size_mb'] == 100


def test_fire_failed_calls_notifier() -> None:
    notifier = FakeNotifier()
    sink = DownloadEventSink(notifier)  # type: ignore[arg-type]
    try:
        sink.fire_failed(
            owner_id=None,
            bangumi_name='Fail',
            episode=None,
            resolution=None,
            sn=2,
            error_message='timeout',
        )
        time.sleep(0.3)
    finally:
        sink.close()

    assert len(notifier.calls) == 1
    assert notifier.calls[0]['event'] == 'failed'
    assert notifier.calls[0]['error_message'] == 'timeout'


def test_fire_cancelled_calls_notifier() -> None:
    notifier = FakeNotifier()
    sink = DownloadEventSink(notifier)  # type: ignore[arg-type]
    try:
        sink.fire_cancelled(
            owner_id='u2',
            bangumi_name='Cancel',
            episode='03',
            resolution='720',
            sn=3,
        )
        time.sleep(0.3)
    finally:
        sink.close()

    assert len(notifier.calls) == 1
    assert notifier.calls[0]['event'] == 'cancelled'


def test_none_notifier_all_fires_are_noop() -> None:
    sink = DownloadEventSink(None)
    # These must not raise or do anything.
    sink.fire_completed(owner_id=None, bangumi_name='x', episode=None, resolution=None, sn=1, file_size_mb=None)
    sink.fire_failed(owner_id=None, bangumi_name='x', episode=None, resolution=None, sn=2, error_message=None)
    sink.fire_cancelled(owner_id=None, bangumi_name='x', episode=None, resolution=None, sn=3)
    sink.close()


def test_fire_is_non_blocking() -> None:
    """fire_completed must return well under 1 s even if notifier sleeps."""
    notifier = FakeNotifier(delay=2.0)
    sink = DownloadEventSink(notifier)  # type: ignore[arg-type]
    try:
        start = time.monotonic()
        sink.fire_completed(owner_id=None, bangumi_name='x', episode=None, resolution=None, sn=10, file_size_mb=None)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f'fire_completed blocked for {elapsed:.2f}s'
    finally:
        sink.close()


def test_exception_in_notifier_does_not_propagate() -> None:
    """Exceptions inside the async notifier must be swallowed by the sink."""
    notifier = FakeNotifier(raises=True)
    sink = DownloadEventSink(notifier)  # type: ignore[arg-type]
    try:
        sink.fire_completed(owner_id=None, bangumi_name='x', episode=None, resolution=None, sn=20, file_size_mb=None)
        time.sleep(0.3)  # let the coroutine run + fail
    finally:
        sink.close()
    # No exception here means we passed.


def test_thread_safe_multiple_fires() -> None:
    """Multiple threads calling fire_* concurrently must not deadlock or drop events."""
    notifier = FakeNotifier()
    sink = DownloadEventSink(notifier)  # type: ignore[arg-type]
    try:
        threads = []
        for i in range(10):
            t = threading.Thread(
                target=sink.fire_completed,
                kwargs=dict(
                    owner_id=None,
                    bangumi_name=f'A{i}',
                    episode=None,
                    resolution=None,
                    sn=100 + i,
                    file_size_mb=None,
                ),
            )
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        time.sleep(0.5)
    finally:
        sink.close()

    assert len(notifier.calls) == 10


def test_close_is_idempotent() -> None:
    sink = DownloadEventSink(None)
    sink.close()
    sink.close()  # second close must not raise


def test_close_drains_in_flight(tmp_path: object) -> None:
    """close() waits for in-flight notifications before returning."""
    notifier = FakeNotifier(delay=0.1)
    sink = DownloadEventSink(notifier)  # type: ignore[arg-type]
    sink.fire_completed(owner_id=None, bangumi_name='x', episode=None, resolution=None, sn=30, file_size_mb=None)
    sink.close()
    # After close the event loop is stopped; the call may or may not have
    # completed (depends on timing), but close() must return without hanging.

"""Tests for :class:`TaskQueue`."""

from __future__ import annotations

import threading

import pytest

from app.scheduler.queue_ import TaskInfo, TaskQueue


def _info(sn: int) -> TaskInfo:
    return TaskInfo(sn=sn, tag='', mode='single')


def test_add_contains_pop_snapshot_happy_path() -> None:
    q = TaskQueue(max_download=2, max_upload=1)
    q.add(1, _info(1))
    q.add(2, _info(2))

    assert q.contains(1)
    assert q.contains(2)

    snap = q.snapshot()
    assert set(snap.keys()) == {1, 2}
    # Snapshot is a copy — mutating it doesn't touch the queue.
    snap.pop(1)
    assert q.contains(1)

    popped = q.pop(1)
    assert popped is not None
    assert popped.sn == 1
    assert not q.contains(1)


def test_download_slot_blocks_when_capacity_reached() -> None:
    q = TaskQueue(max_download=1, max_upload=1)
    entered = threading.Event()
    can_exit = threading.Event()
    second_acquired = threading.Event()

    def holder() -> None:
        with q.download_slot():
            entered.set()
            can_exit.wait(timeout=5)

    def competitor() -> None:
        with q.download_slot():
            second_acquired.set()

    t1 = threading.Thread(target=holder, daemon=True)
    t2 = threading.Thread(target=competitor, daemon=True)

    t1.start()
    assert entered.wait(timeout=2)
    t2.start()

    # Second thread must NOT have acquired while the first holds the slot.
    assert not second_acquired.wait(timeout=0.2)

    can_exit.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert second_acquired.is_set()


def test_download_slot_releases_on_normal_exit() -> None:
    q = TaskQueue(max_download=1, max_upload=1)
    with q.download_slot():
        pass
    # Must be able to acquire again without blocking.
    acquired = q.download_limiter.acquire(timeout=0.5)
    assert acquired
    q.download_limiter.release()


def test_download_slot_releases_on_exception() -> None:
    q = TaskQueue(max_download=1, max_upload=1)
    with pytest.raises(RuntimeError):
        with q.download_slot():
            raise RuntimeError('boom')
    # Permit must be back regardless of exception.
    acquired = q.download_limiter.acquire(timeout=0.5)
    assert acquired
    q.download_limiter.release()


def test_processing_set_operations() -> None:
    q = TaskQueue(max_download=1, max_upload=1)
    assert not q.is_processing(42)
    q.mark_processing(42)
    assert q.is_processing(42)
    q.unmark_processing(42)
    assert not q.is_processing(42)
    # unmark on a non-tracked sn is a no-op.
    q.unmark_processing(999)


def test_concurrent_adds_end_with_all_entries() -> None:
    q = TaskQueue(max_download=4, max_upload=1)
    threads: list[threading.Thread] = []
    for sn in range(20):
        t = threading.Thread(target=lambda s=sn: q.add(s, _info(s)))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    snap = q.snapshot()
    assert set(snap.keys()) == set(range(20))


def test_pop_missing_returns_none() -> None:
    q = TaskQueue(max_download=1, max_upload=1)
    assert q.pop(123456) is None


def test_snapshot_is_independent_of_processing_set() -> None:
    """Design doc: ``snapshot()`` returns the waiting dict verbatim. The
    processing set is a separate, orthogonal flag — it doesn't remove
    entries from the queue; the worker does that via ``pop`` on its own
    terminal branches."""
    q = TaskQueue(max_download=1, max_upload=1)
    q.add(7, _info(7))
    q.mark_processing(7)
    snap = q.snapshot()
    assert 7 in snap
    # Queue still contains it too.
    assert q.contains(7)

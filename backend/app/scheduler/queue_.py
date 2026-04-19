"""Thread-safe work queue for the scheduler.

Replaces the module-level ``queue`` dict and ``processing_queue`` list
in legacy ``aniGamerPlus.py``. All mutations are serialised through an
``RLock``; the download limiter is only acquirable through a context
manager so a mid-block exception can't leak a permit.
"""

from __future__ import annotations

import collections.abc
import contextlib
import dataclasses
import threading


@dataclasses.dataclass(slots=True)
class TaskInfo:
    """Data the worker needs for one sn. Mirrors legacy ``queue[sn]`` shape."""

    sn: int
    tag: str
    mode: str
    season: int = 1


class TaskQueue:
    """Thread-safe work queue with semaphores for download + upload concurrency."""

    def __init__(self, *, max_download: int, max_upload: int) -> None:
        self._download_limiter = threading.Semaphore(max(1, int(max_download)))
        self._upload_limiter = threading.Semaphore(max(1, int(max_upload)))
        self._lock = threading.RLock()
        self._entries: dict[int, TaskInfo] = {}
        self._processing: set[int] = set()

    # ------------------------------------------------------------------ limiters

    @property
    def download_limiter(self) -> threading.Semaphore:
        return self._download_limiter

    @property
    def upload_limiter(self) -> threading.Semaphore:
        return self._upload_limiter

    # ------------------------------------------------------------------ queue ops

    def add(self, sn: int, info: TaskInfo) -> None:
        with self._lock:
            self._entries[int(sn)] = info

    def pop(self, sn: int) -> TaskInfo | None:
        with self._lock:
            return self._entries.pop(int(sn), None)

    def contains(self, sn: int) -> bool:
        with self._lock:
            return int(sn) in self._entries

    def get(self, sn: int) -> TaskInfo | None:
        with self._lock:
            entry = self._entries.get(int(sn))
            if entry is None:
                return None
            return dataclasses.replace(entry)

    # ------------------------------------------------------------------ processing set

    def mark_processing(self, sn: int) -> None:
        with self._lock:
            self._processing.add(int(sn))

    def unmark_processing(self, sn: int) -> None:
        with self._lock:
            self._processing.discard(int(sn))

    def is_processing(self, sn: int) -> bool:
        with self._lock:
            return int(sn) in self._processing

    # ------------------------------------------------------------------ snapshot

    def snapshot(self) -> dict[int, TaskInfo]:
        """Copy of the current waiting queue.

        Design decision: the snapshot includes every sn currently in the
        queue — both waiting and being actively processed. The processing
        set is a separate flag that says "a worker has picked this sn up";
        it doesn't remove the sn from the queue until the worker decides
        to ``pop`` it (e.g. on success, or on unrecoverable failure). This
        matches the legacy ``queue`` dict semantics where ``queue.pop``
        only happened in the worker's terminal branches.
        """
        with self._lock:
            return {sn: dataclasses.replace(info) for sn, info in self._entries.items()}

    # ------------------------------------------------------------------ download_slot

    @contextlib.contextmanager
    def download_slot(self) -> collections.abc.Iterator[None]:
        """Acquire a download-limiter permit for the duration of the block.

        The permit is always released on exit, even if the block raises —
        this replaces the legacy bug where an exception between ``acquire``
        and the cooldown-thread start leaked a permit permanently.
        """
        self._download_limiter.acquire()
        try:
            yield
        finally:
            self._download_limiter.release()

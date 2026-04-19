"""Download cooldown helper.

Design choice: the legacy ``download_cd_counter`` spawned a thread that
slept for ``seconds`` then released the global ``thread_limiter``. That
pattern made it easy to leak permits when an exception fired before the
thread was spawned.

We expose both shapes of the primitive:

- :meth:`DownloadCooldown.schedule_release` — the legacy-equivalent
  daemon-thread variant. The caller passes a release callback, and the
  permit is held until the thread wakes up. This is useful when callers
  want to return control to the caller ASAP while keeping the concurrency
  cap in place.
- :meth:`DownloadCooldown.wait` — a simple synchronous sleep. Always sleeps
  the full configured ``seconds`` regardless of elapsed time. Thread-safe:
  concurrent callers serialise through a lock so each call sleeps the full
  duration in sequence. This is the shape used by :class:`DownloadWorker`
  and :class:`ManualRunner`.

The worker picks :meth:`wait` because the permit-leak class of bugs is
much easier to reason about when the limiter release is lexically scoped.
"""

from __future__ import annotations

import collections.abc
import threading
import time
import typing as T

if T.TYPE_CHECKING:
    from ..downloader.progress import ProgressBus
    from ..logging_ import Logger


class DownloadCooldown:
    """Enforces a fixed-duration pause between successive downloads.

    Thread-safe: multiple workers sharing one instance each sleep the full
    configured ``seconds``; concurrent callers serialise through the lock.
    """

    def __init__(self, seconds: int, logger: Logger, *, label: str = '下載冷卻') -> None:
        self._seconds = max(0, int(seconds))
        self._logger = logger
        self._label = label
        self._lock = threading.Lock()
        # Sleep is injectable so tests can replace it without touching the
        # global ``time.sleep`` and triggering warnings elsewhere.
        self._sleep: collections.abc.Callable[[float], None] = time.sleep

    @property
    def seconds(self) -> int:
        return self._seconds

    def wait(
        self,
        *,
        progress_bus: ProgressBus | None = None,
        sn: int | None = None,
        status_during: str | None = None,
    ) -> None:
        """Block the current thread for the full configured cooldown duration.

        Always sleeps ``self._seconds`` — no elapsed-time subtraction.

        Thread-safe: concurrent callers serialise through an internal lock;
        each call sleeps the full duration in sequence.

        When both ``progress_bus`` and ``sn`` are provided, calls
        ``progress_bus.update_status(sn, status_during)`` (when
        ``status_during`` is given), then
        ``progress_bus.set_cooldown(sn, remaining)`` just before sleeping
        and ``progress_bus.clear_cooldown(sn)`` in a ``finally`` so the UI
        can display a live countdown.

        Both the status flip and the cooldown timestamp are set inside the
        lock so that tasks blocked on the lock retain their previous status
        (e.g. "正在解析") until they actually acquire it — eliminating the
        "下載冷卻 with no countdown" race.
        """
        if self._seconds <= 0:
            return
        remaining = float(self._seconds)
        with self._lock:
            self._logger.info(
                None,
                self._label,
                f'{self._label}剩餘 {remaining:.1f} 秒 (設定: {self._seconds} 秒)',
                display=False,
            )
            if progress_bus is not None and sn is not None:
                if status_during is not None:
                    progress_bus.update_status(sn, status_during)
                progress_bus.set_cooldown(sn, remaining)
            try:
                self._sleep(remaining)
            finally:
                if progress_bus is not None and sn is not None:
                    progress_bus.clear_cooldown(sn)

    def schedule_release(self, release_callback: collections.abc.Callable[[], None]) -> None:
        """Spawn a daemon thread that sleeps ``seconds`` then calls
        ``release_callback``. The callback is invoked exactly once even
        if it raises — exceptions are logged, not re-raised."""

        def _run() -> None:
            try:
                if self._seconds > 0:
                    self._sleep(self._seconds)
            finally:
                try:
                    release_callback()
                except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                    self._logger.error(
                        None,
                        self._label,
                        f'release callback failed: {exc}',
                        display=False,
                    )

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    # ---- test hooks ---------------------------------------------------
    def _set_sleep(self, fn: collections.abc.Callable[[float], None]) -> None:
        """Inject a fake ``sleep`` for deterministic tests."""
        self._sleep = fn

"""Async-safe shim so the sync DownloadWorker can fire events at the
async TelegramNotifier without caring about event loops.

Architecture:
- A dedicated asyncio event loop runs in a private daemon thread.
- ``fire_*`` methods (all sync) submit a coroutine onto that loop via
  ``asyncio.run_coroutine_threadsafe``.  They return immediately without
  waiting for the coroutine to complete.
- A 5-second timeout is imposed on each in-flight coroutine so a slow
  Telegram API never blocks a future download.
- If the notifier is None (bot_token empty), all fire_* calls are no-ops.
- ``close()`` stops the event loop thread and waits for it to finish
  (up to 10 s) so in-flight notifications drain on graceful shutdown.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import threading
import typing as T

if T.TYPE_CHECKING:
    from .telegram_notifier import TelegramNotifier

_log = logging.getLogger(__name__)

_NOTIFY_TIMEOUT = 5.0  # seconds per notification call
_CLOSE_TIMEOUT = 10.0  # seconds to wait for event loop thread to exit


class DownloadEventSink:
    """Sync facade over an async TelegramNotifier.

    Instantiate once per scheduler process. Call ``close()`` on shutdown.
    """

    def __init__(self, notifier: TelegramNotifier | None) -> None:
        self._notifier = notifier
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        if notifier is not None:
            self._loop = asyncio.new_event_loop()
            _started = threading.Event()

            def _run() -> None:
                _started.set()
                self._loop.run_forever()  # type: ignore[union-attr]

            self._thread = threading.Thread(
                target=_run,
                name='telegram-notifier-loop',
                daemon=True,
            )
            self._thread.start()
            # Wait for the loop to actually start before returning, so callers
            # can safely submit work via run_coroutine_threadsafe immediately.
            _started.wait(timeout=5.0)

    # ------------------------------------------------------------------ public

    def fire_completed(
        self,
        *,
        owner_id: str | None,
        bangumi_name: str,
        episode: str | None,
        resolution: str | None,
        sn: int,
        file_size_mb: int | None,
        custom_name: str | None = None,
        season: int = 1,
        episode_number: int | None = None,
    ) -> None:
        """Fire a 'completed' notification.  Sync; returns immediately."""
        self._fire(
            event='completed',
            owner_id=owner_id,
            bangumi_name=bangumi_name,
            episode=episode,
            resolution=resolution,
            sn=sn,
            file_size_mb=file_size_mb,
            custom_name=custom_name,
            season=season,
            episode_number=episode_number,
        )

    def fire_failed(
        self,
        *,
        owner_id: str | None,
        bangumi_name: str,
        episode: str | None,
        resolution: str | None,
        sn: int,
        error_message: str | None,
        custom_name: str | None = None,
        season: int = 1,
        episode_number: int | None = None,
    ) -> None:
        """Fire a 'failed' notification.  Sync; returns immediately."""
        self._fire(
            event='failed',
            owner_id=owner_id,
            bangumi_name=bangumi_name,
            episode=episode,
            resolution=resolution,
            sn=sn,
            error_message=error_message,
            custom_name=custom_name,
            season=season,
            episode_number=episode_number,
        )

    def fire_cancelled(
        self,
        *,
        owner_id: str | None,
        bangumi_name: str,
        episode: str | None,
        resolution: str | None,
        sn: int,
        custom_name: str | None = None,
        season: int = 1,
        episode_number: int | None = None,
    ) -> None:
        """Fire a 'cancelled' notification.  Sync; returns immediately."""
        self._fire(
            event='cancelled',
            owner_id=owner_id,
            bangumi_name=bangumi_name,
            episode=episode,
            resolution=resolution,
            sn=sn,
            custom_name=custom_name,
            season=season,
            episode_number=episode_number,
        )

    def close(self) -> None:
        """Stop the background event loop and wait for it to finish."""
        if self._loop is None or self._thread is None:
            return
        loop = self._loop

        async def _cancel_pending() -> None:
            # Cancel all pending tasks and give them a chance to handle
            # CancelledError cleanly before the loop shuts down.
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        # Wait for in-flight tasks to finish (or be cancelled).
        # NOTE: do NOT call loop.stop() from inside the coroutine — doing so
        # causes run_forever() to exit before the task-completion callbacks
        # fire, which leaves the concurrent.futures.Future unresolved.
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(_cancel_pending(), loop).result(timeout=_CLOSE_TIMEOUT)
        # Now it's safe to stop the loop from outside.
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=_CLOSE_TIMEOUT)
        loop.close()
        self._loop = None
        self._thread = None

    # ------------------------------------------------------------------ internal

    def _fire(self, *, event: str, **kwargs: object) -> None:
        if self._notifier is None or self._loop is None:
            return
        notifier = self._notifier

        async def _call() -> None:
            await notifier.notify_download_event(
                event=event,
                **kwargs,
            )

        cf_future: concurrent.futures.Future[None] = asyncio.run_coroutine_threadsafe(_call(), self._loop)
        # We don't wait on the future here — fire-and-forget.
        # Attach a callback to log any timeout/exception without blocking.
        cf_future.add_done_callback(_log_future_exception)


def _log_future_exception(future: concurrent.futures.Future[None]) -> None:
    """Log any exception that escaped the notification coroutine."""
    try:
        exc = future.exception()
    except Exception:  # noqa: BLE001 — future itself may raise (cancelled etc.)
        return
    if exc is not None:
        _log.warning('TelegramNotifier background task failed: %s', exc)

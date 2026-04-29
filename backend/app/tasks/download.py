"""Dramatiq async actor that runs one manual / scheduled download task.

The actor registers its ``message_id`` against the ``sn`` on entry so
:meth:`TaskService.cancel_task` can call :func:`dramatiq_abort.abort`
and interrupt a running download.

The sync download pipeline (``ManualRunner.run``) is offloaded to a
thread via :func:`asyncio.to_thread` so the event loop stays free for
other concurrent work (e.g. Telegram outbound actors).

Cancellation paths converge on the existing :class:`TaskCancelledError`:
- cooperative: ``progress_bus.cancel(sn)`` sets the per-sn
  ``threading.Event`` which segment_downloader polls between segments.
- forceful: ``dramatiq_abort.abort(message_id, mode=ABORT, ...)`` raises
  :class:`dramatiq_abort.Abort` from the worker thread (this actor catches
  it and re-raises as ``TaskCancelledError``).
"""

from __future__ import annotations

import asyncio

import dramatiq
import dramatiq.middleware
import dramatiq_abort

from .. import dramatiq_setup as _setup

# Ensure broker is initialised before the @actor decorator runs.  Subsequent
# imports are no-ops because init_broker() is idempotent.
_setup.init_broker()


@dramatiq.actor(
    queue_name='downloads',
    max_retries=0,
    time_limit=4 * 60 * 60 * 1000,  # 4 hours hard limit per task
)
async def run_download(
    sn: int,
    *,
    resolution: str = '',
    mode: str = 'single',
    thread_limit: int = 1,
    ep_range: list[str] | None = None,
    classify: bool = True,
    realtime_show: bool = False,
    cui_danmu: bool = False,
    owner_id: str | None = None,
) -> None:
    """Run one download task to completion (or abort)."""
    from ..core import build_container
    from ..downloader.exceptions import TaskCancelledError

    container = build_container()

    # Fetch the dramatiq message_id from the worker's thread-local context so
    # we can register it for cancellation lookup.
    msg = dramatiq.middleware.CurrentMessage.get_current_message()
    message_id: str | None = msg.message_id if msg is not None else None

    if container.message_id_registry is not None and message_id is not None:
        await container.message_id_registry.set(int(sn), message_id)

    try:
        # Sync pipeline runs on a worker thread so the event loop stays free
        # for other concurrent work (mostly Telegram outbound actors).
        await asyncio.to_thread(
            container.manual_runner.run,
            int(sn),
            resolution=resolution,
            mode=mode,
            thread_limit=thread_limit,
            ep_range=list(ep_range) if ep_range else [],
            classify=classify,
            realtime_show=realtime_show,
            cui_danmu=cui_danmu,
            owner_id=owner_id,
        )
    except dramatiq_abort.Abort:
        # Surfaced when TaskService.cancel_task → dramatiq_abort.abort fires
        # while the actor is between bytecode boundaries.  Convert to the
        # downstream cancellation type so progress_bus / event_sink / log
        # paths handle it as a user-cancel instead of an unexpected error.
        raise TaskCancelledError() from None
    finally:
        if container.message_id_registry is not None:
            await container.message_id_registry.clear(int(sn))

"""Dramatiq async actor that force-redownloads one Telegram media entry.

Dispatched on-demand only — mirrors ``tg_backfill_tick``'s actor (not a
periodic tick like ``tg_poll_tick``). See
``app.services.tg_service.TgService.force_redownload`` for the call site:
``POST /api/tg/downloads/{id}/redownload``. The HTTP request only checks
ownership and dispatches this actor — it never waits on the actual
download — so the endpoint returns promptly regardless of file size or
connection speed.

``time_limit`` mirrors ``tg_backfill_actor``'s reasoning, scaled down: a
single-file re-download should never legitimately take anywhere near
``tg_backfill_actor``'s 6-hour budget (that one can walk a chat's entire
history), but a very large file over a slow connection can still take a
while. An hour is generous headroom for that while still bounding a stuck
run well clear of hanging forever. ``max_retries=0`` because a redownload
failure is not something a blind dramatiq retry should paper over — every
failure mode is already logged with a specific reason by
``TgRedownloadService.run``, and the user can just click "強制重新下載"
again from the UI if they want another attempt.
"""

from __future__ import annotations

import dramatiq

from .. import dramatiq_setup as _setup

# Ensure broker is initialised before the @actor decorator runs. Subsequent
# imports are no-ops because init_broker() is idempotent.
_setup.init_broker()


@dramatiq.actor(
    queue_name='downloads',
    max_retries=0,
    time_limit=60 * 60 * 1000,  # 1 hour — see module docstring
)
async def tg_redownload_actor(user_id: str, entry_id: int) -> None:
    """Force re-download one ``tg_downloaded_media`` row to completion (or a logged failure)."""
    from ..core import build_container

    container = build_container()
    if container.tg_redownload_service is None:
        return
    await container.tg_redownload_service.run(user_id=user_id, entry_id=entry_id)

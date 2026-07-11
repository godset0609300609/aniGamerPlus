"""Dramatiq async actor that runs one Telegram chat's historical backfill scan.

Dispatched on-demand — not on a periodic APScheduler tick like the other
``*_tick`` actors in this package. See
``app.services.tg_service.TgService._trigger_backfill`` for the call sites:
adding a watched chat with ``backfill_enabled=True``, flipping
``backfill_enabled`` False -> True on an existing chat, and the manual
``POST /api/tg/chats/{id}/backfill/retry`` endpoint.

``time_limit`` is a generous 6 hours — a chat with years of history and a
slow connection can legitimately take that long; dramatiq's default
30-minute limit would kill a legitimate scan partway through.
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
    time_limit=6 * 60 * 60 * 1000,  # 6 hours — see module docstring
)
async def tg_backfill_actor(user_id: str, chat_id: int, days: int) -> None:
    """Run one chat's historical backfill scan to completion (or failure)."""
    from ..core import build_container

    container = build_container()
    if container.tg_backfill_service is None:
        return
    await container.tg_backfill_service.run(user_id=user_id, chat_id=chat_id, days=days)

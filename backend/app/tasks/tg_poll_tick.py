"""Dramatiq async actor that runs one periodic Telegram catch-up sweep.

Unlike ``tg_backfill_tick`` (dispatched on-demand only — see that module's
docstring), this actor is scheduled by APScheduler
(``app.scheduler.aps_scheduler.ApsScheduler``) every
``ANIGAMERPLUS_TG_POLL_SECONDS`` seconds (default 900 — 15 minutes),
independent of whether the Telegram User API feature is even configured
(the actor body no-ops when ``container.tg_catchup_service`` is ``None``,
same convention as ``tg_backfill_actor``).

Where ``app.tg_downloader.downloader.TgDownloadWatcher`` only reacts to
messages pushed while its hydrogram handler is registered, this tick closes
the gap left by process restarts, disconnected clients, or a handler that
simply hasn't (re)registered yet: every enabled watched chat gets a
cursor-based catch-up scan (``app.tg_downloader.catchup.TgCatchupService``)
each tick, walking only the messages newer than its persisted
``last_scanned_message_id`` bookmark (or, on a chat's very first scan, the
last ``ANIGAMERPLUS_TG_CATCHUP_HOURS`` hours — see that service for the
cursor-vs-cutoff split).

``time_limit`` is a comparatively short 30 minutes — twice the default tick
interval, and an order of magnitude below ``tg_backfill_actor``'s 6-hour
budget. This sweep isn't asked to walk any chat's entire history the way a
backfill is; ``TgCatchupService`` caps how many messages it walks per chat
per run (see that module's ``_MAX_MESSAGES_PER_SCAN``), so 30 minutes is
generous headroom for clearing a legitimately large backlog across many
chats after extended downtime, while still guaranteeing a stuck run gets
killed well before the *next* tick would otherwise queue up behind it.
``max_retries=0`` because the next tick is itself the retry — the cursor is
persisted per successfully-scanned chat, so a failed sweep never has to
restart from scratch, only resume where the last successful chat left off.
"""

from __future__ import annotations

import os

import dramatiq

from .. import dramatiq_setup as _setup

# Ensure broker is initialised before the @actor decorator runs. Subsequent
# imports are no-ops because init_broker() is idempotent.
_setup.init_broker()

#: First-run lookback (hours) for a watched chat that has never had a
#: catch-up scan yet — see ``TgCatchupService.run_one``'s cursor-vs-cutoff
#: branch. Only takes effect once per chat: every scan after the first
#: walks forward from the persisted ``last_scanned_message_id`` cursor
#: instead, regardless of how long it's been since the previous scan.
_CATCHUP_HOURS_ENV_VAR = 'ANIGAMERPLUS_TG_CATCHUP_HOURS'
_DEFAULT_CATCHUP_HOURS = 24


def _catchup_hours() -> int:
    raw = os.environ.get(_CATCHUP_HOURS_ENV_VAR, '')
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_CATCHUP_HOURS
    return value if value > 0 else _DEFAULT_CATCHUP_HOURS


@dramatiq.actor(
    queue_name='downloads',
    max_retries=0,
    time_limit=30 * 60 * 1000,  # 30 minutes — see module docstring
)
async def tg_poll_tick() -> None:
    """One periodic catch-up sweep across every enabled watched chat."""
    from ..core import build_container

    container = build_container()
    if container.tg_catchup_service is None:
        return
    await container.tg_catchup_service.run_all(_catchup_hours())

"""Dramatiq async actor that runs one BT-retention housekeeping iteration.

Wired by APScheduler in the worker process: triggered once every 24 hours,
unconditionally (unlike ``bt_feed_tick`` / ``bt_landing_tick`` this does not
check ``settings.bt_downloader.enabled`` — see
:mod:`app.services.bt_retention_service` for why). The sync
``BtRetentionService.prune_stale`` call is offloaded to a thread via
``asyncio.to_thread`` so the event loop stays responsive during the DB
deletes.
"""

from __future__ import annotations

import asyncio

import dramatiq

from .. import dramatiq_setup as _setup

_setup.init_broker()


@dramatiq.actor(
    queue_name='meta',
    max_retries=0,
    time_limit=5 * 60 * 1000,  # 5 minutes — pure DB deletes, should be fast
)
async def bt_retention_tick() -> None:
    """One retention pass: prune stale ``bt_feed_entry`` + ``task_history`` rows."""
    from ..core import build_container

    container = build_container()
    await asyncio.to_thread(container.bt_retention_service.prune_stale)

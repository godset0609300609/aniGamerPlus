"""Dramatiq async actor that runs one BT downloader feed-tick iteration.

Wired by APScheduler in the worker process: triggered every
``settings.bt_downloader.poll_interval_seconds`` seconds. The sync
``BtDownloaderService.run_iteration`` call is offloaded to a thread via
:func:`asyncio.to_thread` so the event loop stays responsive during the
RSS fetch / filter-match / Put.io-dispatch pass.
"""

from __future__ import annotations

import asyncio

import dramatiq

from .. import dramatiq_setup as _setup

_setup.init_broker()


@dramatiq.actor(
    queue_name='meta',
    max_retries=0,
    time_limit=10 * 60 * 1000,  # 10 minutes — network I/O for feeds
)
async def bt_feed_tick() -> None:
    """One feed-fetch + filter-match + Put.io-dispatch iteration."""
    from ..core import build_container

    container = build_container()
    if not container.settings_repo.load().bt_downloader.enabled:
        return
    await asyncio.to_thread(container.bt_downloader_service.run_iteration)

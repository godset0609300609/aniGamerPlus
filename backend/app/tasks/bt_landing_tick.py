"""Dramatiq async actor that runs one BT downloader landing-tick iteration.

Wired by APScheduler in the worker process: triggered every
``settings.bt_downloader.landing_poll_seconds`` seconds. Polls in-flight
Put.io transfers and lands completed files into ``bangumi_dir`` (or the
configured ``landing_dir``). Runs on the ``downloads`` queue — separate
from ``bt_feed_tick``'s ``meta`` queue — so a large file download doesn't
block RSS feed fetching.
"""

from __future__ import annotations

import asyncio

import dramatiq

from .. import dramatiq_setup as _setup

_setup.init_broker()


@dramatiq.actor(
    queue_name='downloads',
    max_retries=0,
    time_limit=60 * 60 * 1000,  # 60 minutes — large torrents may take a while to pull
)
async def bt_landing_tick() -> None:
    """One Put.io transfer-status poll + completed-file landing iteration."""
    from ..core import build_container

    container = build_container()
    if not container.settings_repo.load().bt_downloader.enabled:
        return
    await asyncio.to_thread(container.bt_landing_worker.run_iteration)

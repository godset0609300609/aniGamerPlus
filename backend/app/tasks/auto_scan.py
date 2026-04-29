"""Dramatiq async actor that performs one UpdateLoop scan iteration.

Wired by APScheduler in the worker process: triggered every
``settings.check_frequency`` minutes.  The sync ``run_one_iteration``
call is offloaded to a thread via :func:`asyncio.to_thread` so the
event loop stays responsive during the scan.
"""

from __future__ import annotations

import asyncio

import dramatiq

from .. import dramatiq_setup as _setup

_setup.init_broker()


@dramatiq.actor(
    queue_name='meta',
    max_retries=0,
    time_limit=10 * 60 * 1000,  # 10 minutes is plenty for a scan
)
async def auto_scan_tick() -> None:
    """One pass through the user's anime list → enqueue new episodes."""
    from ..core import build_container

    container = build_container()
    update_loop = container.build_update_loop()
    await asyncio.to_thread(update_loop.run_one_iteration)

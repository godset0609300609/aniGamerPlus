"""Dramatiq async actor that runs one post-landing remote-status-refresh iteration.

Wired by APScheduler in the worker process: triggered every
``ANIGAMERPLUS_BT_REMOTE_REFRESH_SECONDS`` seconds (default 600 — 10
minutes). Re-polls Put.io for entries that already landed locally but
whose remote copy hasn't been cleared yet, so a SEEDING -> COMPLETED
transition (or an externally deleted transfer) is still reflected in
``putio_status`` even after the entry drops out of the landing poll's
target set. Runs on the ``meta`` queue — same as ``bt_retention_tick`` —
since this is a light DB + API-poll pass, not a large-file download.
"""

from __future__ import annotations

import asyncio
import os

import dramatiq

from .. import dramatiq_setup as _setup

_setup.init_broker()

#: MEDIUM-4 (security audit): caps how many landed-but-uncleared rows one
#: tick checks — see LandingWorker.run_remote_refresh_iteration's docstring.
_BATCH_SIZE_ENV_VAR = 'ANIGAMERPLUS_BT_REMOTE_REFRESH_BATCH'
_DEFAULT_BATCH_SIZE = 100


def _batch_size() -> int:
    raw = os.environ.get(_BATCH_SIZE_ENV_VAR, '')
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_BATCH_SIZE
    return value if value > 0 else _DEFAULT_BATCH_SIZE


@dramatiq.actor(
    queue_name='meta',
    max_retries=0,
    time_limit=5 * 60 * 1000,  # 5 minutes — a handful of status polls, should be fast
)
async def bt_remote_refresh_tick() -> None:
    """One post-landing remote-status-refresh iteration."""
    from ..core import build_container

    container = build_container()
    if not container.settings_repo.load().bt_downloader.enabled:
        return
    await asyncio.to_thread(container.bt_landing_worker.run_remote_refresh_iteration, _batch_size())

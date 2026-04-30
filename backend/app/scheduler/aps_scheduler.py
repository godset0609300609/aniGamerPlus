"""APScheduler that fires dramatiq periodic actors.

Runs alongside the dramatiq worker in the scheduler container.  Provides
``start()`` / ``stop()`` and currently schedules:

* ``auto_scan_tick`` every ``settings.check_frequency`` minutes — replaces
  the legacy ``UpdateLoop.run_forever`` thread.
* ``progress_publish_tick`` every 5 seconds — edits live progress DMs.
* ``health_check_tick`` every 5 minutes — alerts admins on disk-low /
  cookie-expired conditions.
"""

from __future__ import annotations

import typing as T

import apscheduler.schedulers.background

if T.TYPE_CHECKING:
    from ..persistence.settings_repo import SettingsRepository


class ApsScheduler:
    """Wraps a `BackgroundScheduler` with our application-specific jobs.

    Sync facade — APScheduler's BackgroundScheduler runs jobs in worker
    threads internally; we don't need an asyncio-based scheduler since
    the actor-dispatch (``actor.send()``) is sync and very fast.
    """

    def __init__(self, settings_repo: SettingsRepository) -> None:
        self._settings_repo = settings_repo
        self._scheduler = apscheduler.schedulers.background.BackgroundScheduler()

    def start(self) -> None:
        """Schedule and start every periodic job.  Idempotent — safe to call twice."""
        if self._scheduler.running:
            return

        from ..services.telegram_health_monitor import health_check_tick
        from ..services.telegram_progress_publisher import progress_publish_tick
        from ..tasks.auto_scan import auto_scan_tick

        check_minutes = max(1, int(self._settings_repo.load().check_frequency))
        self._scheduler.add_job(
            auto_scan_tick.send,
            trigger='interval',
            minutes=check_minutes,
            id='auto_scan_tick',
            replace_existing=True,
            next_run_time=None,  # let interval start the first run
            max_instances=3,
            coalesce=True,
            misfire_grace_time=60,
        )
        self._scheduler.add_job(
            progress_publish_tick.send,
            trigger='interval',
            seconds=5,
            id='progress_publish_tick',
            replace_existing=True,
            max_instances=3,
            coalesce=True,
            misfire_grace_time=30,
        )
        self._scheduler.add_job(
            health_check_tick.send,
            trigger='interval',
            minutes=5,
            id='health_check_tick',
            replace_existing=True,
            max_instances=3,
            coalesce=True,
            misfire_grace_time=60,
        )

        self._scheduler.start()

    def stop(self) -> None:
        """Stop accepting jobs and join the worker pool."""
        if not self._scheduler.running:
            return
        self._scheduler.shutdown(wait=False)

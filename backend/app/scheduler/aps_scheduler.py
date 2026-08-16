"""APScheduler that fires dramatiq periodic actors.

Runs alongside the dramatiq worker in the scheduler container.  Provides
``start()`` / ``stop()`` and currently schedules:

* ``auto_scan_tick`` every ``settings.check_frequency`` minutes — replaces
  the legacy ``UpdateLoop.run_forever`` thread.
* ``progress_publish_tick`` every 5 seconds — edits live progress DMs.
* ``health_check_tick`` every 5 minutes — alerts admins on disk-low /
  cookie-expired conditions.
* ``bt_feed_tick`` / ``bt_landing_tick`` — only when
  ``settings.bt_downloader.enabled`` is ``True``; RSS -> filter -> Put.io
  dispatch and Put.io transfer landing, respectively.
* ``bt_retention_tick`` every 24 hours — always scheduled (independent of
  ``settings.bt_downloader.enabled``); prunes stale ``bt_feed_entry`` and
  ``task_history`` rows.
* ``tg_poll_tick`` every ``ANIGAMERPLUS_TG_POLL_SECONDS`` seconds (default
  900 — 15 minutes) — always scheduled, independent of TG_API_ID/TG_API_HASH
  being configured (same "always scheduled" convention as
  ``bt_retention_tick``; the actor itself no-ops when the Telegram User API
  feature isn't set up). Runs a cursor-based catch-up scan
  (``app.tg_downloader.catchup.TgCatchupService``) across every enabled
  watched chat, closing the gap the real-time handler
  (``app.tg_downloader.downloader.TgDownloadWatcher``) leaves whenever the
  process restarts, a client disconnects, or a handler hasn't
  (re)registered yet.
* ``bt_remote_refresh_tick`` — only when ``settings.bt_downloader.enabled``
  is ``True``; re-polls Put.io for landed-but-not-remote-cleared entries so
  SEEDING -> COMPLETED transitions and externally-deleted transfers are
  still reflected after landing. Interval is
  ``ANIGAMERPLUS_BT_REMOTE_REFRESH_SECONDS`` (default 600s / 10 minutes) —
  not surfaced in Settings since remote-state churn is slow and this is an
  operator-tunable knob, not a user-facing one.
"""

from __future__ import annotations

import os
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

        settings = self._settings_repo.load()
        check_minutes = max(1, int(settings.check_frequency))
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

        # Retention housekeeping — always runs once daily regardless of
        # bt_downloader.enabled: task_history pruning is independent of the
        # BT downloader feature, and stale bt_feed_entry rows should still
        # be cleaned up even after the feature is disabled.
        from ..tasks.bt_retention_tick import bt_retention_tick

        self._scheduler.add_job(
            bt_retention_tick.send,
            trigger='interval',
            hours=24,
            id='bt_retention_tick',
            replace_existing=True,
            max_instances=3,
            coalesce=True,
            misfire_grace_time=3600,
        )

        # Telegram catch-up sweep — always scheduled (independent of
        # TG_API_ID/TG_API_HASH; see the actor's own no-op guard), same
        # "always on" convention as bt_retention_tick above.
        # max_instances=1: a catch-up sweep must never overlap itself — the
        # per-chat cursor writes assume each chat is only ever being scanned
        # by one sweep at a time.
        from ..tasks.tg_poll_tick import tg_poll_tick

        self._scheduler.add_job(
            tg_poll_tick.send,
            trigger='interval',
            seconds=int(os.environ.get('ANIGAMERPLUS_TG_POLL_SECONDS', '900')),
            id='tg_poll_tick',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

        if settings.bt_downloader.enabled:
            from ..tasks.bt_feed_tick import bt_feed_tick
            from ..tasks.bt_landing_tick import bt_landing_tick

            self._scheduler.add_job(
                bt_feed_tick.send,
                trigger='interval',
                seconds=settings.bt_downloader.poll_interval_seconds,
                id='bt_feed_tick',
                replace_existing=True,
                max_instances=3,
                coalesce=True,
                misfire_grace_time=60,
            )
            self._scheduler.add_job(
                bt_landing_tick.send,
                trigger='interval',
                seconds=settings.bt_downloader.landing_poll_seconds,
                id='bt_landing_tick',
                replace_existing=True,
                max_instances=3,
                coalesce=True,
                misfire_grace_time=30,
            )

            from ..tasks.bt_remote_refresh_tick import bt_remote_refresh_tick

            self._scheduler.add_job(
                bt_remote_refresh_tick.send,
                trigger='interval',
                seconds=int(os.environ.get('ANIGAMERPLUS_BT_REMOTE_REFRESH_SECONDS', '600')),
                id='bt_remote_refresh_tick',
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

        self._scheduler.start()

    def stop(self) -> None:
        """Stop accepting jobs and join the worker pool."""
        if not self._scheduler.running:
            return
        self._scheduler.shutdown(wait=False)

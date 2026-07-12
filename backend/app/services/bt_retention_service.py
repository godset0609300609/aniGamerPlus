"""BtRetentionService — daily housekeeping for BT-downloader-adjacent tables.

Two independent prune passes, run together once a day by the
``bt_retention_tick`` dramatiq actor (via ``asyncio.to_thread``, same
pattern as ``bt_feed_tick`` / ``bt_landing_tick``):

* ``bt_feed_entry`` — rows older than ``entry_retention_days`` that are
  either unmatched or already landed are deleted; see
  :meth:`~app.persistence.bt_feed_entry_repo.BtFeedEntryRepository.delete_stale`
  for the exact "safe to drop" definition.
* ``task_history`` — rows older than ``task_history_retention_days`` (by
  ``finished_at``) are deleted outright.

Unlike ``bt_feed_tick`` / ``bt_landing_tick`` this pass is **not** gated on
``settings.bt_downloader.enabled`` — ``task_history`` retention is useful
regardless of whether the BT downloader feature is on, and stale
``bt_feed_entry`` rows should still be cleaned up after the feature is
disabled rather than lingering forever.
"""

from __future__ import annotations

import typing as T

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..persistence.bt_feed_entry_repo import BtFeedEntryRepository
    from ..persistence.settings_repo import SettingsRepository
    from ..persistence.task_history_repo import TaskHistoryRepository

_LOG_TAG = 'BT保留清理'


class BtRetentionService:
    """Prunes stale rows from ``bt_feed_entry`` and ``task_history``."""

    def __init__(
        self,
        bt_feed_entry_repo: BtFeedEntryRepository,
        task_history_repo: TaskHistoryRepository,
        settings_repo: SettingsRepository,
        *,
        logger: Logger | None = None,
    ) -> None:
        self._bt_feed_entry_repo = bt_feed_entry_repo
        self._task_history_repo = task_history_repo
        self._settings_repo = settings_repo
        self._logger = logger

    def prune_stale(self) -> tuple[int, int]:
        """Run both prune passes.

        Returns ``(bt_feed_entry_deleted, task_history_deleted)``.
        """
        bt_downloader = self._settings_repo.load().bt_downloader
        entry_days = max(1, int(bt_downloader.entry_retention_days))
        history_days = max(1, int(bt_downloader.task_history_retention_days))

        deleted_entries = self._bt_feed_entry_repo.delete_stale(entry_days)
        deleted_history = self._task_history_repo.delete_older_than(history_days)

        if deleted_entries or deleted_history:
            self._log(f'已清理 {deleted_entries} 筆過期 BT 條目、{deleted_history} 筆過期任務歷史紀錄')

        return deleted_entries, deleted_history

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger.info(None, _LOG_TAG, message, display=False)


__all__ = ['BtRetentionService']

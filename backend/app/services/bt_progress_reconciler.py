"""BtProgressReconciler — boot-time ghost-task reconciliation for the live
progress monitor (MonitorView / ``/api/ws/tasks_progress``).

The bug this fixes
-------------------
``ProgressBus`` is per-process, in-memory state that is write-through
mirrored into Redis (:class:`~app.downloader.redis_progress_mirror.RedisProgressMirror`)
so the API process can read it cross-process
(:class:`~app.services.redis_progress_reader.RedisProgressReader`). If the
*scheduler* process — the one running :class:`~app.bt_downloader.landing_worker.LandingWorker`
/ :class:`~app.tg_downloader.downloader.TgDownloadWatcher` — is killed
mid-flight (a deploy, an OOM, a crash), its in-memory ``_entries`` table is
gone, but the Redis hash for that ``sn`` survives: a hash only gets a TTL once
``finish()`` sets ``finished_at`` and the mirror calls ``expire()``. A task
that died between "started" and "finished" therefore leaves a **permanently
stuck card** ("等待 Put.io 0%", "落地中 1%", …) even though
``bt_feed_entry.local_path`` / ``putio_status`` (BT) or
``tg_downloaded_media.local_path`` (TG) already reflect the real, finished
outcome — the DB write and the live-progress write are two separate calls,
and only the second one was lost.

The fix
-------
:meth:`BtProgressReconciler.reconcile_on_boot` runs once at scheduler startup
(see ``scheduler_server.py``'s lifespan). It reads the actual durable
outcome from the DB (``bt_feed_entry`` / ``tg_downloaded_media``),
cross-references each row's ``sn`` against a single Redis snapshot
(:meth:`~app.services.redis_progress_reader.RedisProgressReader.snapshot`),
and force-finishes any entry that is stuck non-terminal — via
:meth:`~app.downloader.progress.ProgressBus.force_finish`, which (unlike
``finish()``) can close out an sn this process never locally tracked, and
deliberately never touches ``task_history`` (already closed out by
``LandingWorker``/``TgDownloadWatcher``'s own direct repo calls).

Rows with no corresponding Redis entry (nothing was ever tracking them —
e.g. landed before this feature existed, or the Redis key already expired)
and rows whose Redis entry is already terminal are left untouched, so this
is safe to run on every boot: once a row has been reconciled, the Redis hash
carries a real ``finished_at`` and a TTL, and stops showing up as stale on
the next run.

Stale in-flight ghosts
----------------------
The landed/terminal-unlanded passes above only catch rows whose *outcome* is
already known in the DB. There is a third class of ghost that falls between
them: a BT transfer dispatched to Put.io (or a TG download started) that
never reaches either a landed or a terminal-failure state at all — the
scheduler process was killed mid-flight and nothing ever wrote the next
state transition, so the row just sits there (``putio_status='IN_QUEUE'``/
``'COMPLETED'``, ``local_path IS NULL``, or a still-open ``task_history``
row for TG) while its Redis-mirrored ``ProgressBus`` entry is frozen at
whatever percentage it last reported ("等待 Put.io 0%", "落地中 1%", …),
potentially forever.

:meth:`BtProgressReconciler.reconcile_on_boot` additionally sweeps for these
via :meth:`~app.persistence.bt_feed_entry_repo.BtFeedEntryRepository.list_stale_in_flight_ghosts`
(BT) and :meth:`~app.persistence.task_history_repo.TaskHistoryRepository.list_stale_in_progress`
(TG — ``tg_downloaded_media`` cannot represent an in-flight row at all, see
that method's docstring), force-finishing each as ``'中斷'`` rather than a
success/failure outcome, since the real outcome is genuinely unknown. A row
only counts as stale once it is older than ``ANIGAMERPLUS_BT_STALE_GHOST_HOURS``
hours (default 1, clamped to 0-24) — see :func:`_stale_ghost_cutoff_hours` —
so a transfer that is merely slow, not stuck, is left alone.
"""

from __future__ import annotations

import os
import typing as T

if T.TYPE_CHECKING:
    from ..downloader.progress import ProgressBus, TaskProgress
    from ..logging_ import Logger
    from ..persistence.bt_feed_entry_repo import BtFeedEntryRepository
    from ..persistence.task_history_repo import TaskHistoryRepository
    from ..persistence.task_id_map_repo import TaskIdMapRepository
    from ..persistence.tg_downloaded_media_repo import TgDownloadedMediaRepository
    from .redis_progress_reader import RedisProgressReader

_LOG_TAG = 'BT/TG啟動對帳'

# task_history source tags — must match LandingWorker._TASK_HISTORY_SOURCE /
# app.tg_downloader.downloader._TASK_HISTORY_SOURCE exactly, since both feed
# the same TaskIdMapRepository.allocate(source, external_id) table.
_BT_SOURCE = 'bt'
_TG_SOURCE = 'tg'

# Put.io transfer statuses that are terminal but will never produce a
# local_path — see BtFeedEntryRepository.mark_remote_cleared / mark_remote_removed
# (only ever set post-landing in the current code path, kept here anyway for
# defense-in-depth) and LandingWorker's own error handling (_ERROR_STATUS).
_TERMINAL_UNLANDED_PUTIO_STATUSES = ('遠端已清理', '遠端已移除', '失敗', 'ERROR')

# ---------------------------------------------------------------------------
# Stale-ghost cutoff — how old (hours) a dispatched-but-unresolved row must
# be before it is treated as stuck rather than merely slow.
# ---------------------------------------------------------------------------
_STALE_GHOST_HOURS_ENV_VAR = 'ANIGAMERPLUS_BT_STALE_GHOST_HOURS'
_DEFAULT_STALE_GHOST_HOURS = 1
_MIN_STALE_GHOST_HOURS = 0
_MAX_STALE_GHOST_HOURS = 24


def _stale_ghost_cutoff_hours() -> int:
    """Read + clamp ``ANIGAMERPLUS_BT_STALE_GHOST_HOURS`` (default 1, range 0-24).

    Read at call time (not cached) so the env var is honoured live without a
    process restart, matching the pattern used by
    ``app.tasks.bt_remote_refresh_tick``'s batch-size env var. An unparsable
    value falls back to the default rather than raising.
    """
    raw = os.environ.get(_STALE_GHOST_HOURS_ENV_VAR, '')
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_STALE_GHOST_HOURS
    return max(_MIN_STALE_GHOST_HOURS, min(_MAX_STALE_GHOST_HOURS, value))


class BtProgressReconciler:
    """Closes out stale live-progress entries left behind by a dead scheduler process."""

    def __init__(
        self,
        bt_feed_entry_repo: BtFeedEntryRepository,
        tg_downloaded_media_repo: TgDownloadedMediaRepository,
        task_id_map_repo: TaskIdMapRepository,
        bt_progress_bus: ProgressBus,
        progress_bus: ProgressBus,
        redis_progress_reader: RedisProgressReader | None,
        *,
        task_history_repo: TaskHistoryRepository | None = None,
        logger: Logger | None = None,
    ) -> None:
        self._bt_feed_entry_repo = bt_feed_entry_repo
        self._tg_downloaded_media_repo = tg_downloaded_media_repo
        self._task_id_map_repo = task_id_map_repo
        self._bt_progress_bus = bt_progress_bus
        self._progress_bus = progress_bus
        self._redis_progress_reader = redis_progress_reader
        # Optional: only the stale in-flight TG ghost sweep needs this (see
        # TaskHistoryRepository.list_stale_in_progress's docstring for why
        # tg_downloaded_media_repo alone cannot represent an in-flight row).
        # None is a safe, fully backward-compatible default — that sweep is
        # simply skipped, same as when no Redis mirror is configured.
        self._task_history_repo = task_history_repo
        self._logger = logger

    async def reconcile_on_boot(self) -> tuple[int, int]:
        """Run the full BT + TG reconciliation pass. Returns ``(bt_fixed, tg_fixed)``.

        No-op (returns ``(0, 0)``) when no Redis mirror is configured —
        without cross-process persistence there is no ghost state to clean
        up; a freshly booted process's in-memory ``ProgressBus`` is already
        authoritative for itself.
        """
        if self._redis_progress_reader is None:
            return 0, 0

        try:
            live_snapshot = await self._redis_progress_reader.snapshot()
        except Exception:  # noqa: BLE001 — reconciliation must never block boot
            return 0, 0

        bt_landed_fixed = self._reconcile_bt(live_snapshot)
        tg_landed_fixed = self._reconcile_tg(live_snapshot)

        stale_cutoff_hours = _stale_ghost_cutoff_hours()
        bt_stale_fixed = self._reconcile_bt_stale_ghosts(live_snapshot, stale_cutoff_hours)
        tg_stale_fixed = self._reconcile_tg_stale_ghosts(live_snapshot, stale_cutoff_hours)
        stale_fixed = bt_stale_fixed + tg_stale_fixed

        bt_fixed = bt_landed_fixed + bt_stale_fixed
        tg_fixed = tg_landed_fixed + tg_stale_fixed

        if bt_fixed or tg_fixed:
            self._log(
                f'補上 {bt_landed_fixed} 個 BT + {tg_landed_fixed} 個 TG 已完成任務 + {stale_fixed} 個 stale 中斷任務'
            )

        return bt_fixed, tg_fixed

    # ------------------------------------------------------------------ BT

    def _reconcile_bt(self, live_snapshot: dict[int, TaskProgress]) -> int:
        fixed = 0
        for row in self._bt_feed_entry_repo.list_landed():
            try:
                if self._maybe_force_finish_bt(row.id, live_snapshot, final_status='下載完成', filename=row.local_path):
                    fixed += 1
            except Exception:  # noqa: BLE001 — one bad row must not abort the pass
                continue
        for row in self._bt_feed_entry_repo.list_terminal_unlanded(_TERMINAL_UNLANDED_PUTIO_STATUSES):
            try:
                if self._maybe_force_finish_bt(row.id, live_snapshot, final_status='失敗', filename=None):
                    fixed += 1
            except Exception:  # noqa: BLE001
                continue
        return fixed

    def _maybe_force_finish_bt(
        self,
        entry_id: int,
        live_snapshot: dict[int, TaskProgress],
        *,
        final_status: str,
        filename: str | None,
    ) -> bool:
        sn = self._task_id_map_repo.allocate(_BT_SOURCE, str(entry_id))
        live_entry = live_snapshot.get(sn)
        if live_entry is None or live_entry.finished_at is not None:
            return False
        self._bt_progress_bus.force_finish(sn, status=final_status, filename=filename)
        return True

    # ------------------------------------------------------------------ TG

    def _reconcile_tg(self, live_snapshot: dict[int, TaskProgress]) -> int:
        fixed = 0
        for row in self._tg_downloaded_media_repo.list_landed_with_progress_sn():
            try:
                sn = row.progress_sn
                if sn is None:
                    continue
                live_entry = live_snapshot.get(sn)
                if live_entry is None or live_entry.finished_at is not None:
                    continue
                self._progress_bus.force_finish(sn, status='下載完成', filename=row.file_name)
                fixed += 1
            except Exception:  # noqa: BLE001 — one bad row must not abort the pass
                continue
        return fixed

    # ------------------------------------------------------------------ stale in-flight ghosts

    def _reconcile_bt_stale_ghosts(self, live_snapshot: dict[int, TaskProgress], cutoff_hours: int) -> int:
        """Force-finish (as ``'中斷'``) BT transfers dispatched but never resolved.

        See :meth:`~app.persistence.bt_feed_entry_repo.BtFeedEntryRepository.list_stale_in_flight_ghosts`
        for the exact row shape this catches.
        """
        fixed = 0
        for row in self._bt_feed_entry_repo.list_stale_in_flight_ghosts(cutoff_hours):
            try:
                if self._maybe_force_finish_bt(row.id, live_snapshot, final_status='中斷', filename=row.title):
                    fixed += 1
            except Exception:  # noqa: BLE001 — one bad row must not abort the pass
                continue
        return fixed

    def _reconcile_tg_stale_ghosts(self, live_snapshot: dict[int, TaskProgress], cutoff_hours: int) -> int:
        """Force-finish (as ``'中斷'``) TG downloads started but never resolved.

        No-op when ``task_history_repo`` was not wired in — see the
        constructor's docstring note on that collaborator being optional.
        """
        if self._task_history_repo is None:
            return 0
        fixed = 0
        for row in self._task_history_repo.list_stale_in_progress(_TG_SOURCE, cutoff_hours):
            try:
                sn = row.sn
                live_entry = live_snapshot.get(sn)
                if live_entry is None or live_entry.finished_at is not None:
                    continue
                self._progress_bus.force_finish(sn, status='中斷', filename=row.filename)
                fixed += 1
            except Exception:  # noqa: BLE001 — one bad row must not abort the pass
                continue
        return fixed

    # ------------------------------------------------------------------ logging

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger.info(None, _LOG_TAG, message, display=False)


__all__ = ['BtProgressReconciler']

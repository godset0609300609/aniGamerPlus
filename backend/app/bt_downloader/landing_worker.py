"""Polls in-flight Put.io transfers and lands completed files on disk.

Reuses :meth:`~app.downloader.filename.FilenameBuilder.legalize` for
filename sanitisation so BT-downloaded files get the same reserved-character
handling (``|``, ``?``, ``*``, ``:`` -> full-width equivalents) as every
other download path in the app, instead of a second bespoke sanitizer.
"""

from __future__ import annotations

import collections.abc
import contextlib
import datetime
import time
import typing as T

from ..downloader.filename import FilenameBuilder
from .putio_client import PutioClientError, PutioNotFoundError, PutioRateLimitError

if T.TYPE_CHECKING:
    import pathlib

    from ..downloader.progress import ProgressBus
    from ..logging_ import Logger
    from ..models import BtFeedEntry
    from ..persistence.bt_feed_entry_repo import BtFeedEntryRepository
    from ..persistence.bt_feed_repo import BtFeedRepository
    from ..persistence.bt_filter_repo import BtFilterRepository
    from ..persistence.settings_repo import SettingsRepository
    from ..persistence.task_history_repo import TaskHistoryRepository
    from ..persistence.task_id_map_repo import TaskIdMapRepository
    from .putio_client import PutioClient

_LANDABLE_STATUSES = ('COMPLETED', 'SEEDING')
_TRANSIENT_STATUSES = ('IN_QUEUE', 'DOWNLOADING')
_ERROR_STATUS = 'ERROR'

_LOG_TAG = 'BT落地'

# task_history source tag for BT-originated rows — see TaskIdMapRepository,
# which allocates a collision-free task_sn (BASE_OFFSET + row_id) for
# (source, external_id) pairs so a BT entry_id can never collide with an
# animad Bahamut episode sn.
_TASK_HISTORY_SOURCE = 'bt'

# ProgressBus status strings for each pre-landing Put.io transfer status —
# distinct from the Telegram header text (see telegram_notifier._bt_header)
# but conceptually the same state machine; kept here since only
# LandingWorker sees the raw Put.io ``status`` string.
_PROGRESS_STATUS_BY_PUTIO_STATUS = {
    'IN_QUEUE': 'Put.io 排隊中',
    'DOWNLOADING': 'Put.io 下載中',
    'COMPLETED': '準備落地',
    'SEEDING': '準備落地 (Seeding)',
}

# Landing-progress emit throttle (Telegram edit + ProgressBus update share
# this cadence) — only fire on a 5s gap or a >=10-point percent jump; the
# very first callback (last_edit_at is None) always fires so the user sees
# the 0% state transition immediately.
_LANDING_PROGRESS_MIN_INTERVAL_SECONDS = 5.0
_LANDING_PROGRESS_MIN_PERCENT_JUMP = 10

# MEDIUM-4 (security audit): default per-tick batch size for
# run_remote_refresh_iteration — see list_landed_pending_remote_check's
# docstring for why an unbounded scan was a problem.
_DEFAULT_REMOTE_REFRESH_BATCH_SIZE = 100

# MEDIUM-5 (security audit): a 429 retry-after is honoured once per row,
# capped at this many seconds so one very generous Retry-After header can't
# stall an entire landing tick.
_MAX_RATE_LIMIT_RETRY_DELAY_SECONDS = 60

# MEDIUM-5: log a note once auto-delete-remote-on-landed has fired this many
# deletions within a single run_iteration() tick — informational only (the
# per-tick dispatch cap elsewhere is 20; a large drain of newly-landed rows
# in one tick could still exceed that here).
_AUTO_DELETE_RATE_NOTE_THRESHOLD = 20


def _safe_int(value: object) -> int | None:
    """Best-effort int coercion for an untyped Put.io transfer JSON field.

    Put.io's transfer object shape isn't formally documented; fields like
    ``percent_done`` are assumed to be present as an int while a transfer is
    DOWNLOADING, but this defensively tolerates ``None``/missing/malformed
    values instead of raising, matching the existing ``transfer.get(...)``
    defensive style used elsewhere in this module (e.g. ``file_id``).
    """
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except TypeError, ValueError:
        return None


def _bytes_to_mb(value: object) -> int | None:
    """Best-effort bytes->MB coercion for a Put.io transfer's ``size`` field."""
    raw = _safe_int(value)
    if raw is None:
        return None
    return int(raw / (1024 * 1024))


class LandingWorker:
    """Advances ``bt_feed_entry`` rows through the Put.io transfer lifecycle."""

    def __init__(
        self,
        putio_client: PutioClient,
        feed_entry_repo: BtFeedEntryRepository,
        landing_dir: pathlib.Path,
        *,
        bt_feed_repo: BtFeedRepository | None = None,
        bt_filter_repo: BtFilterRepository | None = None,
        notify_event_send: collections.abc.Callable[..., None] | None = None,
        task_history_repo: TaskHistoryRepository | None = None,
        task_id_map_repo: TaskIdMapRepository | None = None,
        logger: Logger | None = None,
        progress_bus: ProgressBus | None = None,
        settings_repo: SettingsRepository | None = None,
    ) -> None:
        self._putio_client = putio_client
        self._feed_entry_repo = feed_entry_repo
        self._landing_dir = landing_dir
        self._bt_feed_repo = bt_feed_repo
        self._bt_filter_repo = bt_filter_repo
        self._notify_event_send = notify_event_send
        self._task_history_repo = task_history_repo
        self._task_id_map_repo = task_id_map_repo
        self._logger = logger
        self._progress_bus = progress_bus
        # Read fresh on every landing (rather than snapshotted once at
        # construction) so a live toggle of "auto-delete-remote-on-landed"
        # in Settings takes effect without a worker restart — same
        # always-reload-on-use pattern as BtRetentionService.prune_stale.
        self._settings_repo = settings_repo
        # MEDIUM-5: count of auto-delete-remote-on-landed deletions fired in
        # the current run_iteration() tick — reset at the top of each call,
        # used only to log a one-time rate note past the threshold.
        self._deletes_this_iteration = 0

    def run_iteration(self) -> None:
        self._deletes_this_iteration = 0
        for row in self._feed_entry_repo.list_pending_landing():
            if row.putio_transfer_id is None:
                continue
            try:
                self._process_row(row)
            except PutioRateLimitError as exc:
                self._handle_rate_limit_and_retry_once(row, exc)
            except Exception as exc:  # noqa: BLE001 — any failure fires bt_failed, loop continues
                self._log_error(f'處理 entry_id={row.id} (transfer_id={row.putio_transfer_id}) 時發生未預期錯誤: {exc}')
                self._emit('bt_failed', row, error_message=str(exc))
                self._finish_task_history(row, final_status='下載失敗')
                self._finish_progress(row, status='失敗')

    def _handle_rate_limit_and_retry_once(self, row: BtFeedEntry, exc: PutioRateLimitError) -> None:
        """MEDIUM-5 security fix: back off for Put.io's advertised ``Retry-After``, then retry *row* once.

        Put.io's 429 is a transient throttling signal, not a real failure —
        treating it like any other exception would incorrectly fire a
        user-facing ``bt_failed`` notification. The delay is capped at
        :data:`_MAX_RATE_LIMIT_RETRY_DELAY_SECONDS` so one very generous
        ``Retry-After`` can't stall an entire tick. If the retry also hits a
        429 (or any other failure), this row is deferred to the next
        ``run_iteration()`` tick — a second, unbounded wait here would risk
        blocking every other row behind it.
        """
        delay = min(exc.retry_after, _MAX_RATE_LIMIT_RETRY_DELAY_SECONDS)
        self._log_info(
            f'Put.io rate limit (429)，等待 {delay}s 後重試一次'
            f'（entry_id={row.id}, transfer_id={row.putio_transfer_id}）'
        )
        time.sleep(delay)
        try:
            self._process_row(row)
        except PutioRateLimitError as exc2:
            self._log_error(f'重試後仍為 Put.io rate limit（entry_id={row.id}），延後至下個 tick 再處理: {exc2}')
        except Exception as exc2:  # noqa: BLE001 — mirrors run_iteration's own outer handler
            self._log_error(f'重試處理 entry_id={row.id} 時發生未預期錯誤: {exc2}')
            self._emit('bt_failed', row, error_message=str(exc2))
            self._finish_task_history(row, final_status='下載失敗')
            self._finish_progress(row, status='失敗')

    def _process_row(self, row: BtFeedEntry) -> None:
        try:
            transfer = self._putio_client.get_transfer(T.cast('int', row.putio_transfer_id))
        except PutioNotFoundError as exc:
            # The transfer was deleted on Put.io's side (e.g. the user's
            # previous dispatch got cleared out remotely). Reset local
            # dispatch state so the entry falls back into the "matched but
            # not dispatched" set and gets re-dispatched fresh, instead of
            # firing a confusing bt_failed for a transfer that no longer
            # exists — see reset_dispatch's docstring. No Telegram
            # notification for this case (matches the state machine's
            # "NOT_FOUND -> no notification" row) but the ProgressBus entry
            # (if any) is still finished as '中斷' so it drops out of the
            # live monitor — task_history is intentionally left open since
            # the entry is expected to be re-dispatched and finish normally.
            self._feed_entry_repo.reset_dispatch(row.id)
            self._log_info(
                f'Put.io transfer 不存在 (transfer_id={row.putio_transfer_id}, entry_id={row.id})，'
                f'已重置派送狀態，將於下次比對時重新派送: {exc}'
            )
            self._finish_progress(row, status='中斷')
            return

        status = T.cast('str', transfer.get('status', ''))
        previous_status = row.putio_status
        self._feed_entry_repo.update_putio_status(row.id, status)

        if status == _ERROR_STATUS:
            self._log_error(f'transfer {row.putio_transfer_id} 狀態為 ERROR（entry_id={row.id}）')
            self._emit(
                'bt_failed',
                row,
                error_message=f'Put.io transfer 狀態為 ERROR (transfer_id={row.putio_transfer_id})',
            )
            self._finish_task_history(row, final_status='下載失敗')
            self._finish_progress(row, status='失敗')
            return

        if status != previous_status:
            percent_done = _safe_int(transfer.get('percent_done'))
            file_size_mb = _bytes_to_mb(transfer.get('size'))
            self._emit(
                'bt_status_update',
                row,
                putio_status=status,
                percent_done=percent_done,
                file_size_mb=file_size_mb,
            )
            progress_status = _PROGRESS_STATUS_BY_PUTIO_STATUS.get(status)
            if progress_status is not None:
                rate = 1.0 if status in _LANDABLE_STATUSES else (percent_done or 0) / 100.0
                self._update_progress(row, status=progress_status, rate=rate)

        if status in _TRANSIENT_STATUSES:
            self._log_info(f'transfer {row.putio_transfer_id} 狀態為 {status}（entry_id={row.id}）')
            return

        if status not in _LANDABLE_STATUSES:
            return

        file_id = transfer.get('file_id')
        if file_id is None:
            self._log_error(
                f'transfer {row.putio_transfer_id} 狀態為 {status} 但缺少 file_id'
                f'（entry_id={row.id}）— Put.io 尚未 populate，下 tick 再試'
            )
            return

        file_id = T.cast('int', file_id)
        files = list(self._putio_client.list_files(file_id))
        if not files:
            # Single-file torrents: transfer.file_id points at the file
            # itself, not a containing folder — list_files(parent_id=file_id)
            # legitimately comes back empty because a file has no children.
            # Confirm via GET /files/{id}: if it really is an (empty) folder,
            # log and retry next tick; otherwise download the file directly.
            meta = self._putio_client.get_file(file_id)
            if str(meta.get('file_type', '')).upper() == 'FOLDER':
                self._log_error(
                    f'transfer {row.putio_transfer_id} 狀態為 {status} 但資料夾為空'
                    f'（entry_id={row.id}, file_id={file_id}）— 下 tick 再試'
                )
                return
            files = [meta]

        landed_name: str | None = None
        for f in files:
            dest = self._landing_dir / FilenameBuilder.legalize(T.cast('str', f['name']))
            on_progress = self._make_landing_progress_callback(row)
            resolved = self._putio_client.download_file(
                T.cast('int', f['id']), dest, landing_dir=self._landing_dir, on_progress=on_progress
            )
            landed_name = resolved.name

        if landed_name is not None:
            self._feed_entry_repo.update_local_path(row.id, landed_name)
            self._log_info(f'已落地 {landed_name}（entry_id={row.id}）')
            self._emit('bt_landed', row, local_path=landed_name)
            self._finish_task_history(row, final_status='下載完成', filename=landed_name)
            self._finish_progress(row, status='下載完成', filename=landed_name)
            self._maybe_auto_delete_remote(row, file_id)

    # ------------------------------------------------------------------ remote cleanup / refresh

    def _maybe_auto_delete_remote(self, row: BtFeedEntry, file_id: int) -> None:
        """Best-effort Put.io remote cleanup right after a successful landing.

        Gated by ``settings.bt_downloader.auto_delete_remote_on_landed``.
        Must never raise: this runs *after* the row has already been marked
        landed and its success events already fired — an exception escaping
        here would be caught by :meth:`run_iteration`'s outer handler, which
        would incorrectly re-fire ``bt_failed`` / downgrade the just-recorded
        '下載完成' status for an entry that in fact landed successfully.

        The actual delete + ``mark_remote_cleared`` work is shared with
        :meth:`_maybe_retro_delete_remote` via :meth:`_delete_remote_and_mark_cleared`
        — see that method for the retry/race handling.
        """
        if self._settings_repo is None:
            return
        try:
            should_delete = self._settings_repo.load().bt_downloader.auto_delete_remote_on_landed
        except Exception as exc:  # noqa: BLE001 — must never fail an already-successful landing
            self._log_error(f'transfer {row.putio_transfer_id} 遠端清理時發生未預期錯誤（entry_id={row.id}）: {exc}')
            return
        if not should_delete:
            return
        self._delete_remote_and_mark_cleared(row, file_id, from_landing=True)

    def _maybe_retro_delete_remote(self, row: BtFeedEntry, file_id: int) -> None:
        """Retro-active Put.io remote cleanup for rows observed at a landable status
        during :meth:`run_remote_refresh_iteration`.

        Covers two cases that :meth:`_maybe_auto_delete_remote` (fired once,
        right after landing) can never retry:

        * Rows that landed *before* ``auto_delete_remote_on_landed`` existed
          (or while it was toggled off) — these have ``remote_cleared_at IS
          NULL`` forever unless something retries the delete.
        * Rows whose landing-time delete attempt failed (network blip, 429,
          etc.) — :meth:`_maybe_auto_delete_remote` never gets a second
          chance since it only runs once, inline with landing.

        Same gating + must-never-raise contract as :meth:`_maybe_auto_delete_remote`;
        kept as a distinct entry point (rather than calling that method
        directly) only so :meth:`_delete_remote_and_mark_cleared` can log a
        different tag (``遠端補刪失敗`` vs ``遠端刪除失敗``) for a genuine
        delete failure, letting log grep tell landing-time deletes apart
        from this backfill path.
        """
        if self._settings_repo is None:
            return
        try:
            should_delete = self._settings_repo.load().bt_downloader.auto_delete_remote_on_landed
        except Exception as exc:  # noqa: BLE001 — must never fail the refresh tick
            self._log_error(f'transfer {row.putio_transfer_id} 遠端清理時發生未預期錯誤（entry_id={row.id}）: {exc}')
            return
        if not should_delete:
            return
        self._delete_remote_and_mark_cleared(row, file_id, from_landing=False)

    def _delete_remote_and_mark_cleared(self, row: BtFeedEntry, file_id: int, *, from_landing: bool) -> None:
        """Delete *file_id* on Put.io and mark *row* remote-cleared. Never raises.

        Shared tail of :meth:`_maybe_auto_delete_remote` and
        :meth:`_maybe_retro_delete_remote` — *from_landing* only selects the
        log tag used when the delete genuinely fails (``遠端刪除失敗`` for
        the landing-time trigger, ``遠端補刪失敗`` for the retro/refresh
        trigger); the delete + ``mark_remote_cleared`` behaviour is
        otherwise identical for both callers.

        Race handling: if Put.io responds 404 (:class:`PutioNotFoundError`)
        — the file was already gone by the time we called ``delete_file``
        (e.g. removed externally between our status observation and this
        call, or a previous attempt's delete actually succeeded but the
        process crashed before ``mark_remote_cleared`` recorded it) — the
        end state we wanted (no remote file, row marked cleared) already
        holds. That is treated as success rather than failure: this calls
        ``mark_remote_cleared`` and logs at INFO, not as an error, since
        nothing actually went wrong from our side. (An alternative would be
        ``mark_remote_removed`` to mirror :meth:`_refresh_remote_status`'s
        own 404 handling, but both states are terminal and mutually
        exclusive by
        :meth:`~app.persistence.bt_feed_entry_repo.BtFeedEntryRepository.list_landed_pending_remote_check`'s
        filter — the row won't be re-polled either way — so
        ``mark_remote_cleared`` is preferred since a delete was in fact the
        intended outcome here.)
        """
        try:
            self._putio_client.delete_file(file_id)
        except PutioNotFoundError:
            self._feed_entry_repo.mark_remote_cleared(row.id)
            self._log_info(
                f'transfer {row.putio_transfer_id} 遠端檔案已不存在（可能已被外部刪除或先前已刪除成功），'
                f'視為清理完成（entry_id={row.id}）'
            )
            return
        except PutioClientError as exc:
            tag = '遠端刪除失敗' if from_landing else '遠端補刪失敗'
            self._log_error(f'transfer {row.putio_transfer_id} {tag}（entry_id={row.id}）: {exc}')
            return
        except Exception as exc:  # noqa: BLE001 — must never fail the caller's already-successful flow
            self._log_error(f'transfer {row.putio_transfer_id} 遠端清理時發生未預期錯誤（entry_id={row.id}）: {exc}')
            return

        self._feed_entry_repo.mark_remote_cleared(row.id)
        self._log_info(f'transfer {row.putio_transfer_id} 遠端已清理（entry_id={row.id}）')
        self._deletes_this_iteration += 1
        if self._deletes_this_iteration == _AUTO_DELETE_RATE_NOTE_THRESHOLD:
            # MEDIUM-5: informational only — _MAX_PUTIO_DISPATCH_PER_TICK
            # (20, in BtDownloaderService) caps *new dispatches* per
            # tick, but a large drain of already-dispatched rows landing
            # (or a large retro-cleanup backlog) in the same tick can still
            # fire more than 20 deletions here; this note just makes that
            # visible in the logs.
            self._log_info(f'本次已觸發 {self._deletes_this_iteration} 次遠端刪除，Put.io API 呼叫量偏高，留意速率限制')

    def run_remote_refresh_iteration(self, batch_size: int = _DEFAULT_REMOTE_REFRESH_BATCH_SIZE) -> None:
        """Periodic pass over landed-but-not-remote-cleared entries.

        Distinct from :meth:`run_iteration` (which handles landing itself):
        this re-polls Put.io for entries that already landed so a
        SEEDING -> COMPLETED transition (or an externally deleted transfer)
        is reflected in ``putio_status`` even though the entry no longer
        appears in :meth:`~app.persistence.bt_feed_entry_repo.BtFeedEntryRepository.list_pending_landing`.
        Called by the ``bt_remote_refresh_tick`` actor at a much lower
        cadence than landing itself, since remote state changes are slow.

        Also doubles as the retro-cleanup path for
        ``auto_delete_remote_on_landed`` (see :meth:`_maybe_retro_delete_remote`):
        every row this method polls is, by construction, one whose remote
        hasn't been cleared yet, so observing a landable status here — new
        transition or not — is a legitimate opportunity to (re)attempt the
        delete for rows that landed before the setting existed, or whose
        landing-time delete previously failed.

        MEDIUM-4 (security audit): *batch_size* bounds how many rows are
        checked in one tick (see
        :meth:`~app.persistence.bt_feed_entry_repo.BtFeedEntryRepository.list_landed_pending_remote_check`,
        which orders newest-first). If the batch comes back full, more rows
        remain — they'll be picked up on a subsequent tick since already-
        refreshed rows without a status change don't leave this set.
        """
        rows = self._feed_entry_repo.list_landed_pending_remote_check(limit=batch_size)
        if len(rows) == batch_size:
            self._log_info(f'本次 remote refresh 批次已滿（{batch_size} 筆），仍有更多待檢查項目將於下次排程處理')
        for row in rows:
            if row.putio_transfer_id is None:
                continue
            try:
                self._refresh_remote_status(row)
            except Exception as exc:  # noqa: BLE001 — isolate row failures, loop continues
                self._log_error(
                    f'檢查 entry_id={row.id} (transfer_id={row.putio_transfer_id}) 遠端狀態時發生未預期錯誤: {exc}'
                )

    def _refresh_remote_status(self, row: BtFeedEntry) -> None:
        try:
            transfer = self._putio_client.get_transfer(T.cast('int', row.putio_transfer_id))
        except PutioNotFoundError:
            self._feed_entry_repo.mark_remote_removed(row.id)
            self._log_info(f'transfer {row.putio_transfer_id} 已在遠端移除（entry_id={row.id}）')
            return
        except PutioClientError as exc:
            self._log_error(f'transfer {row.putio_transfer_id} 遠端狀態檢查失敗（entry_id={row.id}）: {exc}')
            return

        new_status = T.cast('str', transfer.get('status', ''))
        if new_status and new_status != row.putio_status:
            self._feed_entry_repo.update_putio_status(row.id, new_status)
            self._log_info(
                f'transfer {row.putio_transfer_id} 狀態轉換 {row.putio_status} -> {new_status}（entry_id={row.id}）'
            )

        # Retro-cleanup: this row (by list_landed_pending_remote_check's
        # filter) has landed but never been remote-cleared. If Put.io now
        # reports a landable status — whether that's a fresh transition or
        # simply confirming what was already known — attempt the
        # auto-delete-remote-on-landed delete that either never ran (row
        # predates the feature) or failed on a previous attempt.
        if new_status in _LANDABLE_STATUSES:
            file_id = transfer.get('file_id')
            if file_id is not None:
                self._maybe_retro_delete_remote(row, T.cast('int', file_id))

    # ------------------------------------------------------------------ telegram

    def _emit(
        self,
        event: str,
        row: BtFeedEntry,
        *,
        local_path: str | None = None,
        error_message: str | None = None,
        putio_status: str | None = None,
        percent_done: int | None = None,
        file_size_mb: int | None = None,
        bytes_written: int | None = None,
        total_bytes: int | None = None,
    ) -> None:
        """Fire a BT lifecycle event to Telegram (best-effort).

        Wraps name resolution + the send call in one ``suppress`` block so
        neither a lookup failure nor a telegram failure can break the
        landing loop for the current row (the caller already isolates
        per-row work, but ``_emit`` must stay safe on its own too since it
        is also called from inside an ``except`` handler).
        """
        if self._notify_event_send is None:
            return
        with contextlib.suppress(Exception):
            payload: dict[str, object] = {
                'event': event,
                'title': row.title,
                'feed_name': self._feed_name(row.feed_id),
                'filter_name': self._filter_name(row.matched_filter_id),
                'putio_transfer_id': row.putio_transfer_id,
                'entry_id': row.id,
            }
            if local_path is not None:
                payload['local_path'] = local_path
            if error_message is not None:
                payload['error_message'] = error_message[:200]
            if putio_status is not None:
                payload['putio_status'] = putio_status
            if percent_done is not None:
                payload['percent_done'] = percent_done
            if file_size_mb is not None:
                payload['file_size_mb'] = file_size_mb
            if bytes_written is not None:
                payload['bytes_written'] = bytes_written
            if total_bytes is not None:
                payload['total_bytes'] = total_bytes
            self._notify_event_send(kwargs=payload)

    # ------------------------------------------------------------------ progress bus

    def _bt_sn(self, row: BtFeedEntry) -> int | None:
        """Derive the collision-free ``sn`` ProgressBus shares with task_history.

        Same ``TaskIdMapRepository.allocate('bt', str(row.id))`` derivation
        as :meth:`_finish_task_history` — kept as a separate helper since
        callers here don't need task_history_repo, only the sn itself.
        """
        if self._task_id_map_repo is None:
            return None
        with contextlib.suppress(Exception):
            return self._task_id_map_repo.allocate(_TASK_HISTORY_SOURCE, str(row.id))
        return None

    def _update_progress(self, row: BtFeedEntry, *, status: str, rate: float | None = None) -> None:
        """Update the live ProgressBus entry for *row* (best-effort, never raises).

        Silent no-op when no ``progress_bus`` is wired, or when ``row`` has
        no live entry yet (e.g. the dispatching service didn't call
        ``progress_bus.start()`` — matches ``update_status``/``update_stats``'s
        own silent-no-op-on-missing-sn semantics).
        """
        if self._progress_bus is None:
            return
        sn = self._bt_sn(row)
        if sn is None:
            return
        with contextlib.suppress(Exception):
            self._progress_bus.update_status(sn, status)
            if rate is not None:
                self._progress_bus.update_stats(sn, rate=rate)

    def _finish_progress(self, row: BtFeedEntry, *, status: str, filename: str | None = None) -> None:
        """Terminate the live ProgressBus entry for *row* (best-effort, never raises).

        Separate from :meth:`_finish_task_history` — this is purely the
        in-memory (+ Redis-mirrored) live-monitor entry that feeds
        ``/api/ws/tasks_progress``; the DB task_history row is written
        independently via ``self._task_history_repo`` so the two never
        double-INSERT the same row (see the ``progress_bus`` constructor
        argument's docstring in ``core.py`` for why LandingWorker is wired
        with a ``history_repo=None`` ProgressBus instance).
        """
        if self._progress_bus is None:
            return
        sn = self._bt_sn(row)
        if sn is None:
            return
        with contextlib.suppress(Exception):
            if filename is not None:
                self._progress_bus.update_metadata(sn, filename=filename)
            self._progress_bus.update_status(sn, status)
            self._progress_bus.finish(sn)

    def _make_landing_progress_callback(self, row: BtFeedEntry) -> collections.abc.Callable[[int, int], None]:
        """Build the ``on_progress`` callback passed to ``PutioClient.download_file``.

        Throttles both the ``bt_landing_progress`` Telegram emit and the
        ProgressBus update to the same cadence — at most once every
        :data:`_LANDING_PROGRESS_MIN_INTERVAL_SECONDS` seconds, or sooner if
        the percentage has jumped by :data:`_LANDING_PROGRESS_MIN_PERCENT_JUMP`
        points since the last emit. The very first callback always fires
        (state's ``last_edit_at`` starts as ``None``) so the user sees the
        0%-landing-started transition immediately instead of waiting 5s.

        Throttle state (``last_edit_at`` / ``last_percent``) lives entirely
        in this closure — a local var scoped to one file's download — not in
        any registry, since a single ``download_file()`` call runs to
        completion synchronously within one ``run_iteration()`` tick.

        Speed/ETA are computed from the delta against the *previous callback
        invocation* (not the previous throttled emit) so an emitted sample
        always reflects the freshest chunk, even though it's only published
        on the throttled cadence.
        """
        state: dict[str, float | int | None] = {
            'last_edit_at': None,
            'last_percent': None,
            'prev_time': None,
            'prev_bytes': 0,
        }

        def _on_progress(bytes_written: int, total_bytes: int) -> None:
            now = time.monotonic()
            percent = int(bytes_written / total_bytes * 100) if total_bytes else 0

            prev_time = state['prev_time']
            prev_bytes = T.cast('int', state['prev_bytes'])
            speed_mbps: float | None = None
            eta_seconds: int | None = None
            if prev_time is not None:
                dt = now - T.cast('float', prev_time)
                if dt > 0:
                    bytes_per_sec = (bytes_written - prev_bytes) / dt
                    if bytes_per_sec > 0:
                        speed_mbps = bytes_per_sec / 1_000_000
                        remaining = max(total_bytes - bytes_written, 0)
                        eta_seconds = int(remaining / bytes_per_sec)
            state['prev_time'] = now
            state['prev_bytes'] = bytes_written

            last_edit_at = state['last_edit_at']
            last_percent = state['last_percent']
            should_emit = (
                last_edit_at is None
                or (now - T.cast('float', last_edit_at)) >= _LANDING_PROGRESS_MIN_INTERVAL_SECONDS
                or last_percent is None
                or (percent - T.cast('int', last_percent)) >= _LANDING_PROGRESS_MIN_PERCENT_JUMP
            )
            if not should_emit:
                return

            state['last_edit_at'] = now
            state['last_percent'] = percent

            self._emit(
                'bt_landing_progress',
                row,
                bytes_written=bytes_written,
                total_bytes=total_bytes,
            )

            if self._progress_bus is not None:
                sn = self._bt_sn(row)
                if sn is not None:
                    fraction = bytes_written / total_bytes if total_bytes else 0.0
                    with contextlib.suppress(Exception):
                        self._progress_bus.update_status(sn, '落地中')
                        self._progress_bus.update_stats(
                            sn, rate=fraction, speed_mbps=speed_mbps, eta_seconds=eta_seconds
                        )

        return _on_progress

    def _feed_name(self, feed_id: int) -> str:
        if self._bt_feed_repo is not None:
            feed = self._bt_feed_repo.get(feed_id)
            if feed is not None:
                return feed.name
        return str(feed_id)

    def _filter_name(self, filter_id: int | None) -> str | None:
        if filter_id is None or self._bt_filter_repo is None:
            return None
        filt = self._bt_filter_repo.get(filter_id)
        return filt.name if filt is not None else None

    # ------------------------------------------------------------------ task_history

    def _finish_task_history(
        self,
        row: BtFeedEntry,
        *,
        final_status: str,
        filename: str | None = None,
    ) -> None:
        """Close out the task_history row opened by the dispatching service (best-effort).

        Looks the row up by re-deriving its task_sn from ``TaskIdMapRepository``
        (rather than a shared in-memory ``sn -> row_id`` map, which BT has no
        equivalent of — dispatch and landing/failure run in separate service
        instances) and finding the newest still-open row for that sn. A miss
        (no task_history_repo/task_id_map_repo wired, or no open row found —
        e.g. the entry was dispatched before this integration existed) is a
        silent no-op, mirroring how ``_emit`` never lets a Telegram failure
        break the landing loop.
        """
        if self._task_history_repo is None or self._task_id_map_repo is None:
            return
        with contextlib.suppress(Exception):
            task_sn = self._task_id_map_repo.allocate(_TASK_HISTORY_SOURCE, str(row.id))
            entry = self._task_history_repo.get_latest_in_progress_by_sn(task_sn)
            if entry is None:
                return
            self._task_history_repo.record_finish(
                entry.id,
                final_status=final_status,
                finished_at=datetime.datetime.now(datetime.UTC),
                filename=filename,
            )

    # ------------------------------------------------------------------ logging

    def _log_info(self, message: str) -> None:
        if self._logger is not None:
            self._logger.info(None, _LOG_TAG, message, display=False)

    def _log_error(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)

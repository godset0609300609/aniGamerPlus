"""Thread-safe ``ProgressBus`` — the replacement for ``Config.tasks_progress_rate``.

The legacy downloader (and the whole web dashboard) scribbled directly
into a module-level dict. That works but couples every caller to the
``Config`` module and makes it hard to test the snapshot plumbing. The
``ProgressBus`` owns the dict behind a ``threading.RLock`` and offers a
narrow typed API for both writers (downloader workers) and readers (the
progress websocket).

Persistence
-----------
When a :class:`~app.persistence.task_history_repo.TaskHistoryRepository` is
wired in, the bus persists every task to the DB:

* ``start()``  → INSERT row with sentinel ``final_status`` + returns ``_row_id``
* ``finish()`` → UPDATE row with real terminal status + ``finished_at``
* Scheduler reboot → ``mark_interrupted_on_boot()`` flips stale rows

Both DB calls happen **outside** the ``_lock`` to avoid holding the lock
during I/O.  An internal ``_row_ids`` dict maps ``sn`` → DB row id.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import functools
import threading
import typing as T

if T.TYPE_CHECKING:
    from ..persistence.task_history_repo import TaskHistoryRepository

_COMPLETION_TTL_SECONDS = 7 * 86400  # 7 days


class _ProgressMirror(T.Protocol):
    """Write-through hook called after every mutation.  Implementations
    must serialise the given entry to a cross-process store (Redis hash
    in production)."""

    def publish(self, sn: int, entry: TaskProgress) -> None: ...
    def publish_finish(self, sn: int, entry: TaskProgress) -> None: ...


# Statuses that represent a genuine terminal outcome — the task reached one of
# these states intentionally before finish() was called.
TERMINAL_STATUSES: frozenset[str] = frozenset({'下載完成', '任務完成'})

# All statuses that should be preserved as-is when finish() writes the DB row.
# Includes the two success terminals plus the explicit failure/abort statuses.
# Any status NOT in this set means the task died mid-flight (exception, kill,
# or parse failure) and finish() will coerce it to '中斷'.
ALREADY_TERMINAL: frozenset[str] = frozenset({'下載完成', '任務完成', '已取消', '中斷', '失敗'})


@dataclasses.dataclass(slots=True)
class TaskProgress:
    """A single row in the progress table."""

    sn: int
    rate: float
    status: str
    filename: str
    # Metadata
    bangumi_name: str | None = None
    episode: str | None = None
    resolution: str | None = None  # e.g. "1080p"
    # Real-time stats
    speed_mbps: float | None = None  # instantaneous download speed MB/s
    eta_seconds: int | None = None  # estimated remaining seconds
    retries: int = 0
    started_at: datetime.datetime | None = None
    # Completion timestamp — set by finish(); None while task is active.
    finished_at: datetime.datetime | None = None
    # Owner tracking (user_id of the user who triggered this task)
    owner_id: str | None = None
    # Source platform and platform-native identifier
    source: str | None = None
    external_id: str | None = None
    # Cooldown deadline — set by ProgressBus.set_cooldown(); cleared by
    # clear_cooldown() or when finish() is called.  Clients use this to
    # display a live "冷卻 Ns" countdown without the backend having to push
    # per-second updates.
    cooldown_until: datetime.datetime | None = None
    # Cancel signal — excluded from asdict/snapshot via private naming.
    # slots=True dataclass: __dataclass_fields__ only lists the declared
    # fields above; a private attribute stored under a different name is
    # invisible to dataclasses.asdict() by design.
    _cancel_event: threading.Event | None = dataclasses.field(default=None, repr=False, compare=False)


class ProgressBus:
    """Thread-safe in-memory progress table keyed by sn."""

    def __init__(
        self,
        history_repo: TaskHistoryRepository | None = None,
        *,
        mirror: _ProgressMirror | None = None,
    ) -> None:
        self._entries: dict[int, TaskProgress] = {}
        self._lock = threading.RLock()
        self._history_repo = history_repo
        self._mirror = mirror
        # Maps sn -> DB row id (populated by record_start; consumed by record_finish).
        self._row_ids: dict[int, int] = {}

    # ------------------------------------------------------------------ writers

    def start(
        self,
        sn: int,
        filename: str,
        status: str = '等待下載',
        *,
        bangumi_name: str | None = None,
        episode: str | None = None,
        resolution: str | None = None,
        owner_id: str | None = None,
        source: str | None = None,
        external_id: str | None = None,
    ) -> None:
        """Register a new task or update an in-progress one without double-inserting.

        All keyword arguments beyond ``status`` are optional and
        backward-compatible — callers that only pass ``(sn, filename)``
        continue to work without modification.

        ``owner_id`` tracks which user triggered this task for per-user
        progress filtering in the RBAC layer.

        When a ``history_repo`` is wired, also INSERTs a ``task_history`` row
        with the in-progress sentinel status so crash recovery can detect
        interrupted tasks on the next boot.

        **Idempotency rules** (three cases):

        1. If a DB row is already open (``sn`` in ``_row_ids``): only update
           in-memory fields — no second INSERT.  Prevents duplicate rows when
           ``_announce_waiting`` and ``Anime.download`` both call ``start()``
           for the same sn.

        2. If an in-memory entry exists and is still active (``finished_at``
           is ``None``): update in-memory fields only without touching the DB.
           Preserves metadata written by a concurrent pre-parse thread so that
           a later ``_announce_waiting`` call does not wipe ``bangumi_name`` /
           ``episode`` that the pre-parse already populated.

        3. Otherwise (no entry, or entry is finished): create a new
           ``TaskProgress`` entry and a new DB row.  This allows a genuine
           re-submission (user queues the same sn after it has already
           finished) to produce a fresh history entry.
        """
        cancel_event = threading.Event()
        started_at = datetime.datetime.now(datetime.UTC)
        already_has_db_row: bool
        _branch: int = 0  # 1=has-db-row, 2=active-no-db-row, 3=fresh
        with self._lock:
            already_has_db_row = sn in self._row_ids
            existing = self._entries.get(sn)

            if already_has_db_row:
                # DB row is still open — update in-memory fields only.
                # This handles the _announce_waiting + Anime.download double-call
                # pattern without inserting a duplicate DB row.
                if existing is not None:
                    existing.status = status
                    existing.filename = filename
                    # Reset transient progress-tracking fields so that a
                    # re-entered task (e.g. parse phase following a stale
                    # pre-parse pool race) always starts at 0%.  Retries are
                    # intentionally preserved — they are cumulative within a
                    # single attempt and should not be reset here.
                    existing.rate = 0.0
                    existing.speed_mbps = None
                    existing.eta_seconds = None
                    existing.cooldown_until = None
                    if bangumi_name is not None:
                        existing.bangumi_name = bangumi_name
                    if episode is not None:
                        existing.episode = episode
                    if resolution is not None:
                        existing.resolution = resolution
                _branch = 1

            elif existing is not None and existing.finished_at is None:
                # An active in-memory entry exists but has no open DB row (either
                # no history_repo is wired, or the row was already closed by a
                # previous finish()).  Update in-memory only so that fields written
                # by a concurrent pre-parse (bangumi_name, episode) are not wiped
                # by a subsequent _announce_waiting → start() call.
                existing.status = status
                existing.filename = filename
                # Same transient-field reset as Case 1 — see comment above.
                existing.rate = 0.0
                existing.speed_mbps = None
                existing.eta_seconds = None
                existing.cooldown_until = None
                if bangumi_name is not None:
                    existing.bangumi_name = bangumi_name
                if episode is not None:
                    existing.episode = episode
                if resolution is not None:
                    existing.resolution = resolution
                _branch = 2

            else:
                # No active entry (either first call, or entry was finished).
                # Create a fresh TaskProgress and a new DB row so that genuine
                # re-submissions (user manually queues the same sn again after it
                # finished) produce a proper history entry.
                entry = TaskProgress(
                    sn=sn,
                    rate=0.0,
                    status=status,
                    filename=filename,
                    bangumi_name=bangumi_name,
                    episode=episode,
                    resolution=resolution,
                    started_at=started_at,
                    owner_id=owner_id,
                    source=source,
                    external_id=external_id,
                )
                entry._cancel_event = cancel_event
                self._entries[sn] = entry
                _branch = 3

        if _branch == 1:
            self._mirror_publish(sn)
            return

        if _branch == 2:
            self._mirror_publish(sn)
            return

        # Persist outside the lock to avoid holding it during DB I/O.
        if self._history_repo is not None:
            row_id = self._history_repo.record_start(
                sn=sn,
                filename=filename,
                owner_id=owner_id,
                bangumi_name=bangumi_name,
                episode=episode,
                resolution=resolution,
                started_at=started_at,
                source=source,
                external_id=external_id,
            )
            with self._lock:
                self._row_ids[sn] = row_id

        # Branch 3: fresh entry created — publish after optional DB write so
        # _row_id is already populated in the mirror snapshot.
        self._mirror_publish(sn)

    def update_rate(self, sn: int, rate: float) -> None:
        """Mutate ``rate`` on an existing entry; silent no-op if missing."""
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            entry.rate = rate
        self._mirror_publish(sn)

    def update_status(self, sn: int, status: str) -> None:
        """Mutate ``status`` on an existing entry; silent no-op if missing."""
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            entry.status = status
        self._mirror_publish(sn)

    def update_stats(
        self,
        sn: int,
        *,
        speed_mbps: float | None = None,
        eta_seconds: int | None = None,
        rate: float | None = None,
    ) -> None:
        """Atomically update speed/ETA/rate. ``None`` arguments are not written.

        This is the single call-site for the speed-reporting hot path so
        that callers never hold the lock for more than one field at a time
        and we avoid partial-visibility races.
        """
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            if speed_mbps is not None:
                entry.speed_mbps = speed_mbps
            if eta_seconds is not None:
                entry.eta_seconds = eta_seconds
            if rate is not None:
                entry.rate = rate
        self._mirror_publish(sn)

    def update_metadata(
        self,
        sn: int,
        *,
        bangumi_name: str | None = None,
        episode: str | None = None,
        resolution: str | None = None,
        filename: str | None = None,
    ) -> None:
        """Update metadata fields in-memory.

        For pre-parsed-ahead-of-time cases where we know the bangumi name /
        episode / resolution before the real download pipeline starts.

        ``None`` arguments are not written — only non-``None`` values overwrite
        the existing field.  No DB write is performed; ``finish()`` will persist
        the final metadata later.
        """
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            if bangumi_name is not None:
                entry.bangumi_name = bangumi_name
            if episode is not None:
                entry.episode = episode
            if resolution is not None:
                entry.resolution = resolution
            if filename is not None:
                entry.filename = filename
        self._mirror_publish(sn)

    def update_resolution(self, sn: int, resolution: str) -> None:
        """Set the ``resolution`` field on an existing entry; silent no-op if missing."""
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            entry.resolution = resolution
        self._mirror_publish(sn)

    def mark_retry(self, sn: int) -> None:
        """Increment ``retries`` counter and set status to '失敗! 重啓中'."""
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            entry.retries += 1
            entry.status = '失敗! 重啓中'
        self._mirror_publish(sn)

    def finish(self, sn: int) -> None:
        """Mark a task as finished.

        The entry is *not* removed immediately — it is kept so the frontend
        can display it in the "recently completed" column.  ``_prune_stale``
        (called from ``snapshot``) will remove it once the TTL expires.

        When a ``history_repo`` is wired, also UPDATEs the ``task_history``
        row with the real terminal status and ``finished_at``.  The DB write
        happens outside the lock.

        **Status normalisation**: if the in-memory status at the time finish()
        is called is not a recognised terminal status (i.e. it is something
        like ``'正在解析'`` or ``'正在下載'``), the task died mid-flight due to
        an unhandled exception, a kill signal, or a logic gap.  In that case
        the status is coerced to ``'中斷'`` before the DB row is written so
        the history table always contains semantically correct final statuses.

        **Rate normalisation**: if the status is one of :data:`TERMINAL_STATUSES`
        (a genuine success terminal — ``'下載完成'``/``'任務完成'``), ``rate`` is
        forced to ``1.0`` regardless of whatever value was last written. Without
        this, a task that finishes via an event carrying no incremental progress
        (e.g. BT's ``bt_landed``, which jumps straight from a low-percentage
        ``landing_progress`` sample to done) would display a "完成" label next
        to a near-empty progress bar.
        """
        finished_at = datetime.datetime.now(datetime.UTC)
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            # Idempotency: if finish() was already called (e.g. cancel() scheduled
            # a Timer-based finish and the safety-net in scheduler_server also
            # calls finish), do not overwrite finished_at or re-issue the DB UPDATE.
            if entry.finished_at is not None:
                return
            # Normalize non-terminal status → '中斷' so the DB row always
            # reflects reality, not a transient mid-flight snapshot.
            if entry.status not in ALREADY_TERMINAL:
                entry.status = '中斷'
            if entry.status in TERMINAL_STATUSES:
                entry.rate = 1.0
            entry.finished_at = finished_at
            # Snapshot fields needed for DB write — copy under lock.
            _status = entry.status
            _retries = entry.retries
            _bangumi_name = entry.bangumi_name
            _episode = entry.episode
            _resolution = entry.resolution
            _filename = entry.filename
            _row_id = self._row_ids.pop(sn, None)

        # Persist outside the lock.
        if self._history_repo is not None and _row_id is not None:
            self._history_repo.record_finish(
                _row_id,
                final_status=_status,
                finished_at=finished_at,
                retries=_retries,
                bangumi_name=_bangumi_name,
                episode=_episode,
                resolution=_resolution,
                filename=_filename,
            )

        self._mirror_publish_finish(sn)

    def force_finish(
        self,
        sn: int,
        *,
        status: str,
        filename: str | None = None,
        source: str | None = None,
    ) -> None:
        """Force-close an entry this *process* never had a live copy of.

        ``update_status``/``finish`` are silent no-ops when ``sn`` is not in
        ``self._entries`` — by design, so a stray call for an sn nobody is
        tracking can't fabricate a bogus row. That guard is exactly what makes
        them useless for **boot-time ghost reconciliation**
        (:class:`~app.services.bt_progress_reconciler.BtProgressReconciler`):
        ``ProgressBus`` is per-process, in-memory state; when the process
        holding the real entry dies mid-flight (e.g. the scheduler is killed
        while a BT transfer is landing), the *Redis* mirror it wrote through
        survives — a hash with ``finished_at`` empty has no TTL — but the
        freshly-booted replacement process's ``self._entries`` is empty, so it
        can never "already have" the row ``finish()`` requires.

        This method exists specifically for that case: the caller has already
        confirmed (via the Redis snapshot) that a stale, non-terminal entry
        exists for ``sn`` and that the underlying DB row (``bt_feed_entry`` /
        ``tg_downloaded_media``) shows the task actually completed. It
        synthesises a terminal local entry if none exists yet (rather than
        requiring one to pre-exist) and publishes the finish through the
        mirror so the Redis hash gets the same ``zrem`` + TTL treatment
        ``finish()`` would have given it.

        Deliberately never touches ``history_repo`` — unlike ``finish()``.
        The BT/TG task_history row for this sn was already closed out by the
        caller's own direct repo call (``LandingWorker._finish_task_history``
        / ``TgDownloadWatcher._finish_history``), so writing here too would
        either double-INSERT (if no DB row is currently open) or overwrite an
        already-correct row with a second, redundant UPDATE.

        ``source`` is only applied when a *new* entry is synthesised (no local
        copy existed). It is the caller's job to pass the correct platform tag
        (``'bt'`` / ``'tg'``) — without it, the synthesised entry's ``source``
        stays ``None`` and the frontend badge falls back to a neutral "unknown"
        label rather than mislabeling the card. When a local entry already
        exists, its ``source`` (set by the original ``start()`` call) is left
        untouched — this call never overwrites a known source with ``None`` or
        a different value.

        Idempotent: no-op if a local entry already exists and is finished.
        """
        finished_at = datetime.datetime.now(datetime.UTC)
        with self._lock:
            entry = self._entries.get(sn)
            if entry is not None and entry.finished_at is not None:
                return
            if entry is None:
                entry = TaskProgress(sn=sn, rate=0.0, status=status, filename=filename or '', source=source)
                self._entries[sn] = entry
            else:
                entry.status = status
                if filename is not None:
                    entry.filename = filename
            if status in TERMINAL_STATUSES:
                entry.rate = 1.0
            entry.finished_at = finished_at

        self._mirror_publish_finish(sn)

    def cancel(self, sn: int) -> bool:
        """Signal the running task for ``sn`` to cancel.

        Sets the cancel event, updates status to ``'已取消'``, and schedules
        a delayed ``finish(sn)`` 1 second later so the UI can show the
        cancelled state briefly before the entry disappears.

        Returns ``True`` if the sn was tracked, ``False`` otherwise.
        """
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return False
            entry.status = '已取消'
            if entry._cancel_event is not None:
                entry._cancel_event.set()

        self._mirror_publish(sn)

        # Schedule finish after 1 second so the UI notices the cancelled state.
        timer = threading.Timer(1.0, self.finish, args=(sn,))
        timer.daemon = True
        timer.start()
        return True

    def set_cooldown(self, sn: int, seconds: float) -> None:
        """Mark ``sn`` as being in cooldown for ``seconds`` more.

        Computes an absolute UTC deadline and attaches it to the
        ``TaskProgress`` entry.  The frontend uses this to display a live
        "冷卻 Ns" countdown without the backend needing to push per-second
        updates.  Idempotent: calling again while already in cooldown simply
        resets the deadline.

        Silent no-op when ``sn`` is not tracked.
        """
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            entry.cooldown_until = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=seconds)
        self._mirror_publish(sn)

    def clear_cooldown(self, sn: int) -> None:
        """Remove the cooldown deadline for ``sn``.

        Silent no-op when ``sn`` is not tracked.
        """
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            entry.cooldown_until = None
        self._mirror_publish(sn)

    def _mirror_publish(self, sn: int) -> None:
        if self._mirror is None:
            return
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            snap = dataclasses.replace(entry, _cancel_event=None)
        # Never let a mirror failure kill a download — log nothing, swallow all.
        with contextlib.suppress(Exception):
            self._mirror.publish(sn, snap)

    def _mirror_publish_finish(self, sn: int) -> None:
        if self._mirror is None:
            return
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return
            snap = dataclasses.replace(entry, _cancel_event=None)
        with contextlib.suppress(Exception):
            self._mirror.publish_finish(sn, snap)

    def get_cancel_event(self, sn: int) -> threading.Event | None:
        """Return the cancel ``threading.Event`` for ``sn``, or ``None`` if unknown."""
        with self._lock:
            entry = self._entries.get(sn)
            if entry is None:
                return None
            return entry._cancel_event

    # ------------------------------------------------------------------ readers

    def _prune_stale(self) -> None:
        """Remove finished entries whose TTL has expired.

        Called from ``snapshot()`` so callers always see a clean view without
        needing an external timer.  Entries that have never been finished
        (``finished_at is None``) are never pruned here.
        """
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=_COMPLETION_TTL_SECONDS)
        with self._lock:
            stale = [sn for sn, t in self._entries.items() if t.finished_at is not None and t.finished_at < cutoff]
            for sn in stale:
                del self._entries[sn]

    def snapshot(self) -> dict[int, TaskProgress]:
        """Return a decoupled dict+values copy of the current state.

        Stale completed entries (older than TTL) are pruned before the copy
        is taken.  The ``_cancel_event`` field is intentionally excluded from
        the copy — it is an internal signal and must not leak through the wire
        protocol.
        """
        self._prune_stale()
        with self._lock:
            result: dict[int, TaskProgress] = {}
            for sn, entry in self._entries.items():
                copy = dataclasses.replace(entry)
                copy._cancel_event = None  # strip the internal event from the copy
                result[sn] = copy
            return result


@functools.lru_cache(maxsize=1)
def get_progress_bus() -> ProgressBus:
    """Return the process-wide ``ProgressBus`` instance."""
    return ProgressBus()

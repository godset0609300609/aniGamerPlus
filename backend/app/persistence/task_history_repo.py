"""Repository for the ``task_history`` table.

One row is written per download attempt:

* ``start(sn, ...)``  → INSERT with ``final_status='(in_progress)'``
* ``finish(sn, ...)`` → UPDATE SET final_status, finished_at, retries
* On scheduler boot  → UPDATE stale in-progress rows → ``'中斷'``

Plain dataclass :class:`TaskHistoryEntry` is the public read type; the ORM
:class:`~app.persistence.models.TaskHistoryRow` never escapes the module.
"""

from __future__ import annotations

import dataclasses
import datetime
import typing as T

import sqlalchemy

from .models import TaskHistoryRow

if T.TYPE_CHECKING:
    from .db import Database

# Sentinel stored in ``final_status`` for tasks that are still running.
# Distinct from any user-visible Chinese status string.
_IN_PROGRESS_SENTINEL = '(in_progress)'


@dataclasses.dataclass(slots=True)
class TaskHistoryEntry:
    """Plain-data snapshot of one ``task_history`` row returned by reads."""

    id: int
    sn: int
    owner_id: str | None
    filename: str
    bangumi_name: str | None
    episode: str | None
    resolution: str | None
    final_status: str
    started_at: datetime.datetime | None  # UTC-aware
    finished_at: datetime.datetime | None  # UTC-aware
    retries: int
    source: str | None = None
    external_id: str | None = None


def _parse_iso(value: str | None) -> datetime.datetime | None:
    """Parse an ISO-8601 string back to a UTC-aware datetime, or return None."""
    if value is None:
        return None
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt


def _to_entry(row: TaskHistoryRow) -> TaskHistoryEntry:
    return TaskHistoryEntry(
        id=row.id,
        sn=row.sn,
        owner_id=row.owner_id,
        filename=row.filename,
        bangumi_name=row.bangumi_name,
        episode=row.episode,
        resolution=row.resolution,
        final_status=row.final_status,
        started_at=_parse_iso(row.started_at),
        finished_at=_parse_iso(row.finished_at),
        retries=row.retries,
        source=row.source,
        external_id=row.external_id,
    )


class TaskHistoryRepository:
    """CRUD surface for the ``task_history`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ writes

    def record_start(
        self,
        sn: int,
        filename: str,
        *,
        owner_id: str | None = None,
        bangumi_name: str | None = None,
        episode: str | None = None,
        resolution: str | None = None,
        started_at: datetime.datetime | None = None,
        source: str | None = None,
        external_id: str | None = None,
    ) -> int:
        """INSERT a new in-progress row and return its ``id``.

        Called by :meth:`ProgressBus.start` **outside** the bus lock.
        Returns the auto-generated ``id`` so the caller can pass it back to
        :meth:`record_finish`.
        """
        started_iso = started_at.isoformat() if started_at is not None else None
        row = TaskHistoryRow(
            sn=sn,
            owner_id=owner_id,
            filename=filename,
            bangumi_name=bangumi_name,
            episode=episode,
            resolution=resolution,
            final_status=_IN_PROGRESS_SENTINEL,
            started_at=started_iso,
            finished_at=None,
            retries=0,
            source=source,
            external_id=external_id,
        )
        with self._db.session() as session:
            session.add(row)
            session.flush()  # populate row.id before commit
            row_id: int = row.id
        return row_id

    def record_finish(
        self,
        row_id: int,
        *,
        final_status: str,
        finished_at: datetime.datetime,
        retries: int = 0,
        bangumi_name: str | None = None,
        episode: str | None = None,
        resolution: str | None = None,
        filename: str | None = None,
    ) -> None:
        """UPDATE an existing in-progress row to its terminal state.

        ``row_id`` is the value returned by :meth:`record_start`.
        If the row is not found (edge case: start was never persisted) this
        is a silent no-op.

        Optional metadata fields (``bangumi_name``, ``episode``,
        ``resolution``, ``filename``) are written when provided, allowing
        callers to persist the final resolved values even if they were
        unavailable at ``record_start`` time (e.g. ``bangumi_name`` is only
        known after metadata fetch completes).
        """
        values: dict[str, T.Any] = {
            'final_status': final_status,
            'finished_at': finished_at.isoformat(),
            'retries': retries,
        }
        if bangumi_name is not None:
            values['bangumi_name'] = bangumi_name
        if episode is not None:
            values['episode'] = episode
        if resolution is not None:
            values['resolution'] = resolution
        if filename is not None:
            values['filename'] = filename

        with self._db.session() as session:
            stmt = sqlalchemy.update(TaskHistoryRow).where(TaskHistoryRow.id == row_id).values(**values)
            session.execute(stmt)

    def mark_interrupted_on_boot(self) -> int:
        """Flip any remaining in-progress rows to ``'中斷'``.

        Called once during scheduler startup.  Returns the number of rows
        updated so callers can log the count.
        """
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(TaskHistoryRow)
                .where(TaskHistoryRow.final_status == _IN_PROGRESS_SENTINEL)
                .values(final_status='中斷', finished_at=now_iso)
            )
            cursor = T.cast(
                'sqlalchemy.engine.CursorResult[T.Any]',
                session.execute(stmt),
            )
            return cursor.rowcount

    def normalize_legacy_statuses(self) -> int:
        """One-time cleanup: coerce non-terminal ``final_status`` values to ``'中斷'``.

        Prior to the ``ProgressBus.finish()`` normalisation fix, tasks that
        died mid-flight (exception, kill signal, parse failure) could have
        their transient in-memory status (e.g. ``'正在解析'``) written directly
        into ``final_status``.  This method is idempotent and safe to call on
        every scheduler startup — it only touches rows whose ``final_status``
        is not one of the recognised terminal values.

        Returns the number of rows updated so callers can log the count.
        """
        _known_terminal = (
            '下載完成',
            '任務完成',
            '已取消',
            '中斷',
            '失敗',
            _IN_PROGRESS_SENTINEL,  # still-running sentinel — leave to mark_interrupted_on_boot
        )
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(TaskHistoryRow)
                .where(
                    TaskHistoryRow.final_status.not_in(_known_terminal),
                    # Only touch rows that have already been closed (finished_at IS NOT NULL).
                    # In-progress rows are handled by mark_interrupted_on_boot.
                    TaskHistoryRow.finished_at.is_not(None),
                )
                .values(final_status='中斷')
            )
            cursor = T.cast(
                'sqlalchemy.engine.CursorResult[T.Any]',
                session.execute(stmt),
            )
            return cursor.rowcount

    def delete_older_than(self, days: int) -> int:
        """Delete rows whose ``finished_at`` is older than *days*.

        In-progress rows (``finished_at IS NULL``) are never deleted —
        retention only applies to closed/terminal rows.  Returns the number
        of rows deleted.
        """
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).isoformat()
        with self._db.session() as session:
            stmt = sqlalchemy.delete(TaskHistoryRow).where(
                TaskHistoryRow.finished_at.is_not(None),
                TaskHistoryRow.finished_at < cutoff,
            )
            cursor = T.cast(
                'sqlalchemy.engine.CursorResult[T.Any]',
                session.execute(stmt),
            )
            return cursor.rowcount

    def get_latest_in_progress_by_sn(self, sn: int) -> TaskHistoryEntry | None:
        """Most recent still-open (``final_status == '(in_progress)'``) row for *sn*.

        The BT downloader pipeline has no in-memory ``ProgressBus``-style
        ``sn -> row_id`` map (unlike animad/bilibili) because dispatch and
        landing/failure are handled by separate service instances
        (:class:`~app.services.bt_downloader_service.BtDownloaderService` /
        :class:`~app.services.bt_manual_dispatch_service.BtManualDispatchService`
        write the ``record_start`` row;
        :class:`~app.bt_downloader.landing_worker.LandingWorker` writes the
        matching ``record_finish`` later, possibly after a process restart).
        This re-derives the ``row_id`` to finish by querying for the newest
        open row instead. Ordered by ``id DESC`` so a re-dispatch (e.g. after
        a stale-transfer reset) always resolves to its own row rather than an
        older still-open leftover from a previous dispatch of the same ``sn``.
        """
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(TaskHistoryRow)
                .where(TaskHistoryRow.sn == sn, TaskHistoryRow.final_status == _IN_PROGRESS_SENTINEL)
                .order_by(TaskHistoryRow.id.desc())
                .limit(1)
            )
            row = session.scalars(stmt).first()
            return _to_entry(row) if row is not None else None

    def list_stale_in_progress(self, source: str, cutoff_hours: int = 1) -> list[TaskHistoryEntry]:
        """Still-open rows (``final_status == '(in_progress)'``) for *source*
        whose ``started_at`` predates *cutoff_hours* ago.

        Used by :class:`~app.services.bt_progress_reconciler.BtProgressReconciler`
        at scheduler boot to catch TG downloads whose live ProgressBus/Redis-
        mirror entry is stuck non-terminal because the process that would
        have called ``finish()`` (via
        ``app.tg_downloader.downloader.TgDownloadWatcher._finish_progress``)
        died mid-download.

        Unlike BT (whose ``bt_feed_entry`` row exists the moment a transfer
        is dispatched, well before landing), ``tg_downloaded_media`` only
        ever gains a row *after* a download completes — ``local_path`` is
        non-nullable there, see
        :meth:`~app.persistence.tg_downloaded_media_repo.TgDownloadedMediaRepository.insert_if_new`
        — so it cannot represent an in-flight download at all. This
        still-open ``task_history`` row (written by ``ProgressBus.start()``
        at download-start time, for the same ``sn``) is the only durable
        record of one.
        """
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=cutoff_hours)).isoformat()
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(TaskHistoryRow)
                .where(
                    TaskHistoryRow.source == source,
                    TaskHistoryRow.final_status == _IN_PROGRESS_SENTINEL,
                    TaskHistoryRow.started_at.is_not(None),
                    TaskHistoryRow.started_at < cutoff,
                )
                .order_by(TaskHistoryRow.id.asc())
            )
            return [_to_entry(r) for r in session.scalars(stmt).all()]

    # ------------------------------------------------------------------ reads

    def list_recent(
        self,
        days: int = 7,
        user_id: str | None = None,
    ) -> list[TaskHistoryEntry]:
        """Return rows whose ``finished_at`` is within the last ``days``.

        In-progress rows (``finished_at IS NULL``) are excluded.
        ``user_id`` filters to a single user; ``None`` returns all users.
        Ordered by ``finished_at DESC``.
        """
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).isoformat()

        # SYNC NOTE: When adding a status here, also add it to HIDDEN_FROM_MONITOR
        # in frontend/src/stores/progress.ts.
        _HIDDEN_STATUSES = ('已取消', '中斷')

        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(TaskHistoryRow)
                .where(
                    TaskHistoryRow.finished_at.is_not(None),
                    TaskHistoryRow.finished_at >= cutoff,
                    TaskHistoryRow.final_status.not_in(_HIDDEN_STATUSES),
                )
                .order_by(TaskHistoryRow.finished_at.desc())
            )
            if user_id is not None:
                stmt = stmt.where(TaskHistoryRow.owner_id == user_id)
            rows = session.scalars(stmt).all()
            return [_to_entry(r) for r in rows]

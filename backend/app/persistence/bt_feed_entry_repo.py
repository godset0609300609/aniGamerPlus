"""Repository for the ``bt_feed_entry`` table."""

from __future__ import annotations

import datetime
import typing as T

import sqlalchemy
import sqlalchemy.exc

from ..models import BtFeedEntry
from .models import BtFeedEntryRow

if T.TYPE_CHECKING:
    from .db import Database


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


# Put.io transfer statuses that are terminal but will never produce a
# local_path. Kept in sync with
# ``app.services.bt_progress_reconciler._TERMINAL_UNLANDED_PUTIO_STATUSES``
# — both describe the same "dispatched, ended badly, never landed" set;
# duplicated here (rather than imported) so this persistence-layer module
# does not reach up into the services layer.
_TERMINAL_UNLANDED_PUTIO_STATUSES = ('遠端已清理', '遠端已移除', '失敗', 'ERROR')


def _to_model(row: BtFeedEntryRow) -> BtFeedEntry:
    return BtFeedEntry(
        id=row.id,
        feed_id=row.feed_id,
        guid=row.guid,
        title=row.title,
        link=row.link,
        author=row.author,
        published_at=row.published_at,
        fetched_at=row.fetched_at,
        matched_filter_id=row.matched_filter_id,
        dispatched_at=row.dispatched_at,
        putio_transfer_id=row.putio_transfer_id,
        putio_status=row.putio_status,
        local_path=row.local_path,
        remote_cleared_at=row.remote_cleared_at,
    )


class BtFeedEntryRepository:
    """CRUD surface for the ``bt_feed_entry`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert_if_new(
        self,
        feed_id: int,
        guid: str,
        title: str,
        link: str,
        author: str | None = None,
        published_at: str | None = None,
    ) -> BtFeedEntry | None:
        """INSERT a new entry; return ``None`` if ``(feed_id, guid)`` already exists."""
        row = BtFeedEntryRow(
            feed_id=feed_id,
            guid=guid,
            title=title,
            link=link,
            author=author,
            published_at=published_at,
            fetched_at=_now_iso(),
        )
        try:
            with self._db.session() as session:
                session.add(row)
                session.flush()
        except sqlalchemy.exc.IntegrityError:
            return None
        return _to_model(row)

    def get(self, entry_id: int) -> BtFeedEntry | None:
        with self._db.session() as session:
            row = session.get(BtFeedEntryRow, entry_id)
            return _to_model(row) if row is not None else None

    def list_recent(self, days: int = 7, *, filter_id: int | None = None) -> list[BtFeedEntry]:
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).isoformat()
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(BtFeedEntryRow)
                .where(BtFeedEntryRow.fetched_at >= cutoff)
                .order_by(BtFeedEntryRow.fetched_at.desc())
            )
            if filter_id is not None:
                stmt = stmt.where(BtFeedEntryRow.matched_filter_id == filter_id)
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def list_paginated(
        self,
        days: int = 7,
        *,
        filter_id: int | None = None,
        putio_status: str | None = None,
        unassigned_only: bool = False,
        q: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[BtFeedEntry], int]:
        """Paginated recent entries, optionally scoped by ``filter_id``, ``putio_status``
        and/or a title substring.

        ``unassigned_only`` and ``putio_status`` are mutually exclusive — when
        ``unassigned_only`` is set, entries with ``putio_status IS NULL`` are
        returned and ``putio_status`` is ignored.

        Returns ``(items, total)`` where ``total`` is the full count matching the
        WHERE clause (independent of ``page``/``size``), for building pagination UI.
        """
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).isoformat()
        with self._db.session() as session:
            where_clauses = [BtFeedEntryRow.fetched_at >= cutoff]
            if filter_id is not None:
                where_clauses.append(BtFeedEntryRow.matched_filter_id == filter_id)
            if unassigned_only:
                where_clauses.append(BtFeedEntryRow.putio_status.is_(None))
            elif putio_status is not None:
                where_clauses.append(BtFeedEntryRow.putio_status == putio_status)
            if q is not None:
                where_clauses.append(BtFeedEntryRow.title.ilike(f'%{q}%'))

            total = (
                session.scalar(
                    sqlalchemy.select(sqlalchemy.func.count()).select_from(BtFeedEntryRow).where(*where_clauses)
                )
                or 0
            )

            offset = (page - 1) * size
            stmt = (
                sqlalchemy.select(BtFeedEntryRow)
                .where(*where_clauses)
                .order_by(BtFeedEntryRow.fetched_at.desc())
                .limit(size)
                .offset(offset)
            )
            items = [_to_model(r) for r in session.scalars(stmt).all()]
            return items, total

    def search_by_title(self, q: str, *, limit: int = 20) -> list[BtFeedEntry]:
        """Case-insensitive substring search over ``title``, most recent first."""
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(BtFeedEntryRow)
                .where(BtFeedEntryRow.title.ilike(f'%{q}%'))
                .order_by(BtFeedEntryRow.fetched_at.desc())
                .limit(limit)
            )
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def list_most_recent(self, limit: int) -> list[BtFeedEntry]:
        """The *limit* most-recently-fetched entries, newest first."""
        with self._db.session() as session:
            stmt = sqlalchemy.select(BtFeedEntryRow).order_by(BtFeedEntryRow.fetched_at.desc()).limit(limit)
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def list_pending_landing(self) -> list[BtFeedEntry]:
        """Entries dispatched to Put.io but not yet landed on disk.

        ``putio_transfer_id IS NOT NULL AND local_path IS NULL`` — i.e. the
        set :class:`~app.bt_downloader.landing_worker.LandingWorker` polls.
        """
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(BtFeedEntryRow)
                .where(
                    BtFeedEntryRow.putio_transfer_id.is_not(None),
                    BtFeedEntryRow.local_path.is_(None),
                )
                .order_by(BtFeedEntryRow.id.asc())
            )
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def list_pending_dispatch(self, limit: int) -> list[BtFeedEntry]:
        """Entries matched by a filter but not yet dispatched to Put.io.

        ``matched_filter_id IS NOT NULL AND dispatched_at IS NULL`` — the
        per-tick Put.io dispatch cap (fix #8) defers entries into this state
        via :meth:`mark_matched` once ``_MAX_PUTIO_DISPATCH_PER_TICK`` is hit,
        instead of dispatching immediately. Ordered oldest match first
        (``fetched_at`` ascending, used as a matched-time proxy since
        matching always happens in the same tick as fetch/insert).
        """
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(BtFeedEntryRow)
                .where(
                    BtFeedEntryRow.matched_filter_id.is_not(None),
                    BtFeedEntryRow.dispatched_at.is_(None),
                )
                .order_by(BtFeedEntryRow.fetched_at.asc())
                .limit(limit)
            )
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def mark_matched(self, entry_id: int, filter_id: int) -> None:
        """Record that *entry_id* matched *filter_id* without dispatching yet.

        Used when the per-tick Put.io dispatch cap (fix #8) is hit — the
        match is persisted so :meth:`list_pending_dispatch` can pick the
        entry up first on a later tick, since ``insert_if_new`` only ever
        returns a given ``(feed_id, guid)`` as "new" once.
        """
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(BtFeedEntryRow)
                .where(BtFeedEntryRow.id == entry_id)
                .values(matched_filter_id=filter_id)
            )
            session.execute(stmt)

    def mark_dispatched(self, entry_id: int, filter_id: int, transfer_id: int) -> None:
        """Record that *entry_id* matched *filter_id* and was sent to Put.io as *transfer_id*."""
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(BtFeedEntryRow)
                .where(BtFeedEntryRow.id == entry_id)
                .values(
                    matched_filter_id=filter_id,
                    dispatched_at=_now_iso(),
                    putio_transfer_id=transfer_id,
                    putio_status='IN_QUEUE',
                )
            )
            session.execute(stmt)

    def update_putio_status(self, entry_id: int, status: str) -> None:
        with self._db.session() as session:
            stmt = sqlalchemy.update(BtFeedEntryRow).where(BtFeedEntryRow.id == entry_id).values(putio_status=status)
            session.execute(stmt)

    def update_local_path(self, entry_id: int, path: str) -> None:
        with self._db.session() as session:
            stmt = sqlalchemy.update(BtFeedEntryRow).where(BtFeedEntryRow.id == entry_id).values(local_path=path)
            session.execute(stmt)

    def delete_stale(self, days: int) -> int:
        """Delete entries older than *days* (by ``fetched_at``) that are safe to drop.

        "Safe to drop" means either:

        * unmatched — ``matched_filter_id IS NULL`` (never dispatched to a
          filter, so there is nothing in flight for it), or
        * already landed — ``local_path IS NOT NULL`` (in practice this
          implies ``matched_filter_id IS NOT NULL`` too, since ``local_path``
          is only ever set post-dispatch by
          :class:`~app.bt_downloader.landing_worker.LandingWorker`).

        Entries that matched a filter but have not yet landed
        (``matched_filter_id IS NOT NULL AND local_path IS NULL``) are kept
        regardless of age — deleting them out from under the landing worker
        would orphan an in-flight Put.io transfer.  Returns the number of
        rows deleted.
        """
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).isoformat()
        with self._db.session() as session:
            stmt = sqlalchemy.delete(BtFeedEntryRow).where(
                BtFeedEntryRow.fetched_at < cutoff,
                sqlalchemy.or_(
                    BtFeedEntryRow.matched_filter_id.is_(None),
                    BtFeedEntryRow.local_path.is_not(None),
                ),
            )
            cursor = T.cast('sqlalchemy.engine.CursorResult[T.Any]', session.execute(stmt))
            return cursor.rowcount

    def list_landed(self) -> list[BtFeedEntry]:
        """All entries with ``local_path`` set (landed on disk).

        Used by :class:`~app.services.bt_progress_reconciler.BtProgressReconciler`
        at scheduler boot to find entries whose live ProgressBus/Redis-mirror
        entry may have gone stale (stuck non-terminal) because the process
        that would have called ``finish()`` died mid-flight before doing so.
        """
        with self._db.session() as session:
            stmt = sqlalchemy.select(BtFeedEntryRow).where(BtFeedEntryRow.local_path.is_not(None))
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def list_terminal_unlanded(self, statuses: T.Sequence[str]) -> list[BtFeedEntry]:
        """Entries whose ``putio_status`` is one of *statuses* but never landed.

        Used by :class:`~app.services.bt_progress_reconciler.BtProgressReconciler`
        at scheduler boot: a dispatched transfer that ended in a terminal
        Put.io state (error, or removed remotely) before ever landing on disk
        should not leave a stuck live-progress card behind either.
        """
        with self._db.session() as session:
            stmt = sqlalchemy.select(BtFeedEntryRow).where(
                BtFeedEntryRow.putio_status.in_(statuses),
                BtFeedEntryRow.local_path.is_(None),
            )
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def list_stale_in_flight_ghosts(self, cutoff_hours: int = 1) -> list[BtFeedEntry]:
        """Dispatched entries stuck in limbo: not landed, not terminal, and old.

        Neither :meth:`list_landed` nor :meth:`list_terminal_unlanded` catches
        these — they sit between the two. ``putio_status`` is still something
        ambiguous/in-flight (e.g. ``'IN_QUEUE'``, ``'COMPLETED'``) and
        ``local_path IS NULL``: the transfer was dispatched, but the next
        state transition (landing, or a terminal Put.io failure) never
        happened — the scheduler process that owned it died mid-flight, or
        the transfer just silently stalled on Put.io's side. These are
        exactly the tasks that leave a permanently stuck live-progress card
        ("等待 Put.io 0%", "落地中 X%", …) with no live actor left to ever
        update it.

        Used by :class:`~app.services.bt_progress_reconciler.BtProgressReconciler`
        at scheduler boot to force-finish (as ``'中斷'``) any such row whose
        Redis-mirrored ``ProgressBus`` entry is still stuck non-terminal.
        """
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=cutoff_hours)).isoformat()
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(BtFeedEntryRow)
                .where(BtFeedEntryRow.dispatched_at.is_not(None))
                .where(BtFeedEntryRow.dispatched_at < cutoff)
                .where(BtFeedEntryRow.local_path.is_(None))
                .where(sqlalchemy.not_(BtFeedEntryRow.putio_status.in_(_TERMINAL_UNLANDED_PUTIO_STATUSES)))
            )
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def count_by_feed(self) -> dict[int, int]:
        """Return ``{feed_id: entry_count}`` across all feeds. Feeds with 0 entries are omitted."""
        with self._db.session() as session:
            rows = session.execute(
                sqlalchemy.select(
                    BtFeedEntryRow.feed_id,
                    sqlalchemy.func.count(BtFeedEntryRow.id).label('cnt'),
                ).group_by(BtFeedEntryRow.feed_id)
            ).all()
            return {row.feed_id: row.cnt for row in rows}

    def list_unmatched_within(self, retention_days: int) -> list[BtFeedEntry]:
        """Entries fetched within retention_days whose matched_filter_id IS NULL.

        Used by BtDownloaderService.run_iteration's per-tick rescan pass so
        that filters added AFTER an entry was fetched still get a chance to
        match. Ordered by fetched_at ascending (oldest first) so that when the
        dispatch cap is hit, the freshest orphans stay orphaned only briefly
        (they'll be at the top of the next tick's list too).
        """
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=retention_days)).isoformat()
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(BtFeedEntryRow)
                .where(BtFeedEntryRow.fetched_at >= cutoff)
                .where(BtFeedEntryRow.matched_filter_id.is_(None))
                .order_by(BtFeedEntryRow.fetched_at.asc())
            )
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def mark_dispatched_manual(self, entry_id: int, putio_transfer_id: int) -> None:
        """Manual dispatch: set putio_transfer_id + dispatched_at + putio_status IN_QUEUE.

        Unlike ``mark_dispatched`` this does NOT set ``matched_filter_id`` —
        manual dispatch is orthogonal to filter matching, and preserving the
        existing (possibly NULL) matched_filter_id lets the entry remain
        eligible for future automatic-match rescan if the user later
        creates a matching filter.
        """
        with self._db.session() as session:
            row = session.get(BtFeedEntryRow, entry_id)
            if row is None:
                return
            row.putio_transfer_id = putio_transfer_id
            row.dispatched_at = datetime.datetime.now(datetime.UTC).isoformat()
            row.putio_status = 'IN_QUEUE'
            row.local_path = None
            session.commit()

    def reset_dispatch(self, entry_id: int) -> None:
        """Clear putio_transfer_id / putio_status / dispatched_at / local_path so a stale
        transfer can be re-dispatched fresh (either manually via mark_dispatched_manual or
        automatically via list_pending_dispatch on the next tick). Preserves
        matched_filter_id — the filter match is still valid; only the Put.io side is stale.

        Used by :class:`~app.bt_downloader.landing_worker.LandingWorker` when
        Put.io returns 404 for a previously-dispatched transfer (e.g. the
        transfer was deleted on Put.io's side) — the row falls back into
        ``list_pending_dispatch``'s "matched but not dispatched" set instead
        of being stuck pointing at a transfer id that no longer exists.
        """
        with self._db.session() as session:
            row = session.get(BtFeedEntryRow, entry_id)
            if row is None:
                return
            row.putio_transfer_id = None
            row.putio_status = None
            row.dispatched_at = None
            row.local_path = None
            session.commit()

    # ------------------------------------------------------------------ remote cleanup / refresh

    def mark_remote_cleared(self, entry_id: int) -> None:
        """Set remote_cleared_at + putio_status='遠端已清理' after a successful auto-delete.

        Called by :class:`~app.bt_downloader.landing_worker.LandingWorker`
        right after :meth:`~app.bt_downloader.putio_client.PutioClient.delete_file`
        succeeds for a just-landed entry.
        """
        with self._db.session() as session:
            row = session.get(BtFeedEntryRow, entry_id)
            if row is None:
                return
            row.remote_cleared_at = _now_iso()
            row.putio_status = '遠端已清理'
            session.commit()

    def mark_remote_removed(self, entry_id: int) -> None:
        """Set remote_cleared_at + putio_status='遠端已移除'.

        Called when the periodic remote-status-refresh pass polls a landed
        entry's transfer and Put.io responds 404 — i.e. Put.io (or the user,
        acting directly on Put.io) removed the transfer/file on its own,
        independent of our auto-delete path.
        """
        with self._db.session() as session:
            row = session.get(BtFeedEntryRow, entry_id)
            if row is None:
                return
            row.remote_cleared_at = _now_iso()
            row.putio_status = '遠端已移除'
            session.commit()

    def list_landed_pending_remote_check(self, limit: int = 100) -> list[BtFeedEntry]:
        """Landed entries whose remote hasn't been cleared/removed yet, newest first, capped at *limit*.

        ``local_path IS NOT NULL AND putio_transfer_id IS NOT NULL AND
        remote_cleared_at IS NULL`` — the set
        :meth:`~app.bt_downloader.landing_worker.LandingWorker.run_remote_refresh_iteration`
        polls. Distinct from :meth:`list_pending_landing` (which stops
        returning a row the moment it lands) — this is what catches a
        Put.io-side SEEDING -> COMPLETED transition, or an externally
        deleted transfer, after landing.

        MEDIUM-4 (security audit): previously unbounded — a large backlog of
        landed-but-uncleared entries (e.g. auto-delete-remote-on-landed
        toggled off for a long stretch, then back on) meant one tick could
        fire an unbounded number of Put.io API calls. ``limit`` caps the
        batch; ``fetched_at DESC`` means recently-landed entries get
        freshness checked first (they're the ones most likely to still be
        SEEDING), while older rows still get picked up on later ticks as
        the newer ones clear out of this set.
        """
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(BtFeedEntryRow)
                .where(
                    BtFeedEntryRow.local_path.is_not(None),
                    BtFeedEntryRow.putio_transfer_id.is_not(None),
                    BtFeedEntryRow.remote_cleared_at.is_(None),
                )
                .order_by(BtFeedEntryRow.fetched_at.desc())
                .limit(limit)
            )
            return [_to_model(r) for r in session.scalars(stmt).all()]

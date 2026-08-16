"""Repository for the ``tg_watched_chat`` table.

Operates directly on the top-level Pydantic models (``TgWatchedChat`` /
``TgWatchedChatCreate`` / ``TgWatchedChatUpdate`` from ``app.models``),
mirroring :class:`~app.persistence.bt_feed_repo.BtFeedRepository`'s
"no intermediate DTO" decision — the shape the API layer needs is identical
to the shape this repository reads/writes.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import typing as T

import sqlalchemy
import sqlalchemy.exc

from ..models import TgWatchedChat, TgWatchedChatCreate, TgWatchedChatUpdate
from .models import TgWatchedChatRow

if T.TYPE_CHECKING:
    from .db import Database


@dataclasses.dataclass(frozen=True)
class TgScanCursorState:
    """Internal catch-up-scan bookkeeping for one watched chat.

    Deliberately NOT part of the API-facing ``TgWatchedChat`` pydantic
    model — ``scan_resume_offset_id``/``scan_pending_cursor`` are pure
    implementation detail of :class:`~app.tg_downloader.catchup.TgCatchupService`'s
    multi-tick resumable walk (see that module's docstring) with no
    observability value beyond what ``last_scanned_message_id``/
    ``last_scanned_at`` already surface on the model. Only
    :class:`TgCatchupService` reads this.
    """

    last_scanned_message_id: int | None
    scan_resume_offset_id: int | None
    scan_pending_cursor: int | None


class DuplicateWatchedChatError(Exception):
    """Raised when a create would violate ``UNIQUE(user_id, chat_id)``."""


class TooManyWatchedChatsError(Exception):
    """Raised when a create would push a user past :data:`_MAX_WATCHED_CHATS_PER_USER`.

    Defense-in-depth (HIGH-6 of the security audit) alongside the create/
    update rate limit in ``app.api.tg_api`` — bounds the total number of
    live message handlers + directories one account can register,
    independent of how quickly they're added.
    """


#: Per-user cap on the number of watched chats — a generous ceiling for
#: legitimate use (nobody watches 50 Telegram chats through one account in
#: practice) while bounding the resource a single account can register
#: (each watched chat holds a live hydrogram message-handler filter entry
#: plus, on match, its own download directory).
_MAX_WATCHED_CHATS_PER_USER = 50


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _to_model(row: TgWatchedChatRow) -> TgWatchedChat:
    return TgWatchedChat(
        id=row.id,
        chat_id=row.chat_id,
        chat_title=row.chat_title,
        media_types=json.loads(row.media_types) if row.media_types else ['video'],
        size_min_mb=row.size_min_mb,
        size_max_mb=row.size_max_mb,
        format_whitelist=json.loads(row.format_whitelist) if row.format_whitelist else None,
        save_path=row.save_path,
        enabled=row.enabled,
        created_at=row.created_at,
        backfill_enabled=row.backfill_enabled,
        backfill_days=row.backfill_days,
        backfill_status=row.backfill_status,  # type: ignore[arg-type]
        backfill_scanned_count=row.backfill_scanned_count,
        backfill_matched_count=row.backfill_matched_count,
        backfill_started_at=row.backfill_started_at,
        backfill_finished_at=row.backfill_finished_at,
        last_scanned_message_id=row.last_scanned_message_id,
        last_scanned_at=row.last_scanned_at,
    )


class TgWatchedChatRepository:
    """CRUD surface for the ``tg_watched_chat`` table, scoped per ``user_id``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def list_by_user(self, user_id: str) -> list[TgWatchedChat]:
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(TgWatchedChatRow)
                .where(TgWatchedChatRow.user_id == user_id)
                .order_by(TgWatchedChatRow.id.asc())
            )
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def list_enabled_by_user(self, user_id: str) -> list[TgWatchedChat]:
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(TgWatchedChatRow)
                .where(TgWatchedChatRow.user_id == user_id, TgWatchedChatRow.enabled.is_(True))
                .order_by(TgWatchedChatRow.id.asc())
            )
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def list_all_enabled(self) -> list[tuple[str, TgWatchedChat]]:
        """Every enabled watched chat across every user, paired with its owner.

        Used by :class:`~app.tg_downloader.downloader.TgDownloadWatcher` to
        build the chat_id -> filter-config map when (re)registering message
        handlers for a user's client.
        """
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(TgWatchedChatRow)
                .where(TgWatchedChatRow.enabled.is_(True))
                .order_by(TgWatchedChatRow.user_id.asc(), TgWatchedChatRow.id.asc())
            )
            return [(r.user_id, _to_model(r)) for r in session.scalars(stmt).all()]

    def get(self, user_id: str, chat_id: int) -> TgWatchedChat | None:
        with self._db.session() as session:
            stmt = sqlalchemy.select(TgWatchedChatRow).where(
                TgWatchedChatRow.user_id == user_id, TgWatchedChatRow.chat_id == chat_id
            )
            row = session.scalars(stmt).first()
            return _to_model(row) if row is not None else None

    def get_by_id(self, user_id: str, watched_chat_id: int) -> TgWatchedChat | None:
        with self._db.session() as session:
            stmt = sqlalchemy.select(TgWatchedChatRow).where(
                TgWatchedChatRow.user_id == user_id, TgWatchedChatRow.id == watched_chat_id
            )
            row = session.scalars(stmt).first()
            return _to_model(row) if row is not None else None

    def count_by_user(self, user_id: str) -> int:
        """Total watched-chat rows (enabled or not) owned by *user_id* — see :data:`_MAX_WATCHED_CHATS_PER_USER`."""
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(sqlalchemy.func.count())
                .select_from(TgWatchedChatRow)
                .where(TgWatchedChatRow.user_id == user_id)
            )
            return int(session.scalar(stmt) or 0)

    def insert(self, user_id: str, payload: TgWatchedChatCreate) -> TgWatchedChat:
        if self.count_by_user(user_id) >= _MAX_WATCHED_CHATS_PER_USER:
            raise TooManyWatchedChatsError(
                f'user_id={user_id} already has {_MAX_WATCHED_CHATS_PER_USER} watched chats (the cap)'
            )
        row = TgWatchedChatRow(
            user_id=user_id,
            chat_id=payload.chat_id,
            chat_title=payload.chat_title,
            media_types=json.dumps(payload.media_types, ensure_ascii=False),
            size_min_mb=payload.size_min_mb,
            size_max_mb=payload.size_max_mb,
            format_whitelist=(
                json.dumps(payload.format_whitelist, ensure_ascii=False)
                if payload.format_whitelist is not None
                else None
            ),
            save_path=payload.save_path,
            enabled=payload.enabled,
            created_at=_now_iso(),
            backfill_enabled=payload.backfill_enabled,
            backfill_days=payload.backfill_days,
        )
        try:
            with self._db.session() as session:
                session.add(row)
                session.flush()
        except sqlalchemy.exc.IntegrityError as exc:
            raise DuplicateWatchedChatError(
                f'chat already watched: user_id={user_id} chat_id={payload.chat_id}'
            ) from exc
        return _to_model(row)

    def update(self, user_id: str, watched_chat_id: int, payload: TgWatchedChatUpdate) -> TgWatchedChat | None:
        changes: dict[str, T.Any] = payload.model_dump(exclude_unset=True)
        if not changes:
            return self.get_by_id(user_id, watched_chat_id)
        if 'media_types' in changes and changes['media_types'] is not None:
            changes['media_types'] = json.dumps(changes['media_types'], ensure_ascii=False)
        if 'format_whitelist' in changes:
            fw = changes['format_whitelist']
            changes['format_whitelist'] = json.dumps(fw, ensure_ascii=False) if fw is not None else None
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(TgWatchedChatRow)
                .where(TgWatchedChatRow.user_id == user_id, TgWatchedChatRow.id == watched_chat_id)
                .values(**changes)
            )
            session.execute(stmt)
        return self.get_by_id(user_id, watched_chat_id)

    def delete(self, user_id: str, watched_chat_id: int) -> None:
        with self._db.session() as session:
            stmt = sqlalchemy.delete(TgWatchedChatRow).where(
                TgWatchedChatRow.user_id == user_id, TgWatchedChatRow.id == watched_chat_id
            )
            session.execute(stmt)

    # ------------------------------------------------------------------ historical backfill
    #
    # Written by app.tg_downloader.backfill.TgBackfillService (the dramatiq
    # worker process) and app.services.tg_service.TgService (the API
    # process, when marking a freshly-dispatched scan 'pending'). Every
    # method is scoped by (user_id, watched_chat_id) — same convention as
    # update()/delete() above — rather than trusting the bare chat_id, which
    # is not unique across users.

    def mark_backfill_pending(self, user_id: str, watched_chat_id: int) -> None:
        self._update_backfill_columns(user_id, watched_chat_id, {'backfill_status': 'pending'})

    def mark_backfill_running(self, user_id: str, watched_chat_id: int, *, started_at: str) -> None:
        self._update_backfill_columns(
            user_id,
            watched_chat_id,
            {
                'backfill_status': 'running',
                'backfill_started_at': started_at,
                'backfill_scanned_count': 0,
                'backfill_matched_count': 0,
            },
        )

    def mark_backfill_progress(
        self, user_id: str, watched_chat_id: int, *, scanned_count: int, matched_count: int
    ) -> None:
        self._update_backfill_columns(
            user_id,
            watched_chat_id,
            {'backfill_scanned_count': scanned_count, 'backfill_matched_count': matched_count},
        )

    def mark_backfill_done(self, user_id: str, watched_chat_id: int, *, finished_at: str) -> None:
        self._update_backfill_columns(
            user_id, watched_chat_id, {'backfill_status': 'done', 'backfill_finished_at': finished_at}
        )

    def mark_backfill_failed(self, user_id: str, watched_chat_id: int, *, finished_at: str) -> None:
        self._update_backfill_columns(
            user_id, watched_chat_id, {'backfill_status': 'failed', 'backfill_finished_at': finished_at}
        )

    # ------------------------------------------------------------------ periodic catch-up scan cursor
    #
    # Read/written by app.tg_downloader.catchup.TgCatchupService (the
    # dramatiq worker process, via app.tasks.tg_poll_tick's periodic actor).
    # Same (user_id, watched_chat_id) scoping convention as the backfill
    # methods above.

    def get_scan_cursor_state(self, user_id: str, watched_chat_id: int) -> TgScanCursorState | None:
        """Read the full internal catch-up-scan cursor triple for one chat.

        Separate from :meth:`get`/:meth:`get_by_id` because
        ``scan_resume_offset_id``/``scan_pending_cursor`` are not part of
        the ``TgWatchedChat`` pydantic model those return (see
        :class:`TgScanCursorState`'s docstring) — ``TgCatchupService`` needs
        both the public model (for filters, ``watched.id``, ...) and this.
        Returns ``None`` if the chat no longer exists.
        """
        with self._db.session() as session:
            stmt = sqlalchemy.select(TgWatchedChatRow).where(
                TgWatchedChatRow.user_id == user_id, TgWatchedChatRow.id == watched_chat_id
            )
            row = session.scalars(stmt).first()
            if row is None:
                return None
            return TgScanCursorState(
                last_scanned_message_id=row.last_scanned_message_id,
                scan_resume_offset_id=row.scan_resume_offset_id,
                scan_pending_cursor=row.scan_pending_cursor,
            )

    def update_scan_cursor_state(
        self,
        user_id: str,
        watched_chat_id: int,
        *,
        last_scanned_message_id: int | None,
        scan_resume_offset_id: int | None,
        scan_pending_cursor: int | None,
        scanned_at: str,
    ) -> None:
        """Persist the full catch-up-scan cursor state in one write.

        Replaced the old single-field ``mark_scan_cursor`` once a scalar
        cursor alone proved unable to express "handled a contiguous range at
        the top, but there's still an unprocessed gap below it" safely under
        ``TgCatchupService``'s per-run scan cap — see that module's
        docstring. Every call writes all three columns plus
        ``last_scanned_at`` explicitly (the caller passes back an unchanged
        value for whichever fields shouldn't move this call) rather than
        supporting a partial update, since ``TgCatchupService.run_one``
        always knows the full state after every attempt.

        ``last_scanned_message_id`` accepts ``None`` because a chat's
        very-first sweep can still be mid-flight (capped, resuming) when
        this is called — passing the caller's already-``None`` cursor
        through unchanged is deliberate, not an oversight; see
        ``TgCatchupService.run_one``'s "Cursor selection" comment on its
        cap-hit branch for why coalescing it to a placeholder there would
        reintroduce the bug this whole mechanism exists to fix.
        """
        self._update_backfill_columns(
            user_id,
            watched_chat_id,
            {
                'last_scanned_message_id': last_scanned_message_id,
                'scan_resume_offset_id': scan_resume_offset_id,
                'scan_pending_cursor': scan_pending_cursor,
                'last_scanned_at': scanned_at,
            },
        )

    def _update_backfill_columns(self, user_id: str, watched_chat_id: int, values: dict[str, T.Any]) -> None:
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(TgWatchedChatRow)
                .where(TgWatchedChatRow.user_id == user_id, TgWatchedChatRow.id == watched_chat_id)
                .values(**values)
            )
            session.execute(stmt)

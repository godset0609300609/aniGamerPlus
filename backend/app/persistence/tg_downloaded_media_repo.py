"""Repository for the ``tg_downloaded_media`` table — the dedup ledger for
Telegram User API downloads.

``UNIQUE(user_id, chat_id, message_id)`` is the dedup key:
:meth:`TgDownloadedMediaRepository.insert_if_new` returns ``None`` on a
constraint hit instead of raising, since the caller
(``app.tg_downloader.downloader``) treats "already downloaded" as an
expected, silent skip rather than an error.
"""

from __future__ import annotations

import dataclasses
import datetime
import typing as T

import sqlalchemy
import sqlalchemy.exc

from .models import TgDownloadedMediaRow

if T.TYPE_CHECKING:
    from .db import Database


@dataclasses.dataclass(slots=True)
class TgDownloadedMediaEntry:
    id: int
    user_id: str
    chat_id: int
    chat_title: str | None
    message_id: int
    file_id: str
    file_name: str
    file_size: int
    downloaded_at: str
    local_path: str
    progress_sn: int | None


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _to_entry(row: TgDownloadedMediaRow, chat_title: str | None = None) -> TgDownloadedMediaEntry:
    return TgDownloadedMediaEntry(
        id=row.id,
        user_id=row.user_id,
        chat_id=row.chat_id,
        chat_title=chat_title,
        message_id=row.message_id,
        file_id=row.file_id,
        file_name=row.file_name,
        file_size=row.file_size,
        downloaded_at=row.downloaded_at,
        local_path=row.local_path,
        progress_sn=row.progress_sn,
    )


class TgDownloadedMediaRepository:
    """CRUD surface for the ``tg_downloaded_media`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def exists(self, user_id: str, chat_id: int, message_id: int) -> bool:
        """Cheap pre-download dedup check — avoids downloading a file we're about to reject on INSERT."""
        with self._db.session() as session:
            stmt = sqlalchemy.select(TgDownloadedMediaRow.id).where(
                TgDownloadedMediaRow.user_id == user_id,
                TgDownloadedMediaRow.chat_id == chat_id,
                TgDownloadedMediaRow.message_id == message_id,
            )
            return session.scalars(stmt).first() is not None

    def insert_if_new(
        self,
        user_id: str,
        *,
        chat_id: int,
        message_id: int,
        file_id: str,
        file_name: str,
        file_size: int,
        local_path: str,
        progress_sn: int | None = None,
    ) -> TgDownloadedMediaEntry | None:
        """INSERT a new row, or return ``None`` if ``(user_id, chat_id, message_id)`` already exists."""
        row = TgDownloadedMediaRow(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            file_id=file_id,
            file_name=file_name,
            file_size=file_size,
            downloaded_at=_now_iso(),
            local_path=local_path,
            progress_sn=progress_sn,
        )
        try:
            with self._db.session() as session:
                session.add(row)
                session.flush()
        except sqlalchemy.exc.IntegrityError:
            return None
        return _to_entry(row)

    def mark_landed(self, entry_id: int, local_path: str) -> None:
        """Update ``local_path`` for a row created before the download finished
        (e.g. if a future caller pre-registers the row before the transfer completes)."""
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(TgDownloadedMediaRow)
                .where(TgDownloadedMediaRow.id == entry_id)
                .values(local_path=local_path)
            )
            session.execute(stmt)

    def list_landed_with_progress_sn(self) -> list[TgDownloadedMediaEntry]:
        """All rows that carry a known ``progress_sn``.

        Every row in this table is, by construction, already landed —
        :meth:`insert_if_new` is only ever called once the download has
        finished, so ``local_path`` is non-nullable. Rows written before the
        ``progress_sn`` column existed have ``progress_sn IS NULL`` and are
        excluded here since there is no sn to reconcile against.

        Used by :class:`~app.services.bt_progress_reconciler.BtProgressReconciler`
        at scheduler boot to find TG downloads whose live ProgressBus/Redis-mirror
        entry may have gone stale because the process that would have called
        ``finish()`` died mid-download before doing so.
        """
        with self._db.session() as session:
            stmt = sqlalchemy.select(TgDownloadedMediaRow).where(TgDownloadedMediaRow.progress_sn.is_not(None))
            return [_to_entry(r) for r in session.scalars(stmt).all()]

    def list_by_user(
        self,
        user_id: str,
        *,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[TgDownloadedMediaEntry], int]:
        """Paginated, newest-first. Returns ``(items, total)``."""
        with self._db.session() as session:
            count_stmt = sqlalchemy.select(sqlalchemy.func.count()).select_from(TgDownloadedMediaRow).where(
                TgDownloadedMediaRow.user_id == user_id
            )
            total = session.scalar(count_stmt) or 0

            stmt = (
                sqlalchemy.select(TgDownloadedMediaRow)
                .where(TgDownloadedMediaRow.user_id == user_id)
                .order_by(TgDownloadedMediaRow.downloaded_at.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
            rows = session.scalars(stmt).all()
            return [_to_entry(r) for r in rows], total

    def count_by_user_since(self, user_id: str, since: datetime.datetime) -> int:
        cutoff = since.isoformat()
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(sqlalchemy.func.count())
                .select_from(TgDownloadedMediaRow)
                .where(TgDownloadedMediaRow.user_id == user_id, TgDownloadedMediaRow.downloaded_at >= cutoff)
            )
            return session.scalar(stmt) or 0

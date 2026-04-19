"""Repository for the ``anime_list_entries`` table.

Callers work with :class:`AnimeListEntryDTO` dataclasses — no ORM objects
escape the repository boundary.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import typing as T

import sqlalchemy

from .models import AnimeListEntryRow

if T.TYPE_CHECKING:
    from .db import Database


@dataclasses.dataclass(slots=True)
class AnimeListEntryDTO:
    """Read/write representation of one ``anime_list_entries`` row.

    ``user_id`` is populated on read (from the DB) and is used by the
    service layer to associate each entry with its owner.  It is not
    persisted directly on write — the repository's ``replace_all_for_user``
    method always sets ``user_id`` from the caller-supplied argument.
    """

    sn: int
    enabled: bool = True
    mode: str | None = None  # "single"|"latest"|"all"|"largest-sn" or None
    tag: str = ''
    season: int = 1
    anime_name: str | None = None  # cached series name (populated by UpdateLoop)
    comment: str = ''
    sort_order: int = 0
    user_id: str | None = None  # populated on read; ignored on write


def _to_dto(orm: AnimeListEntryRow) -> AnimeListEntryDTO:
    return AnimeListEntryDTO(
        sn=orm.sn,
        enabled=orm.enabled,
        mode=orm.mode,
        tag=orm.tag,
        season=orm.season,
        anime_name=orm.anime_name,
        comment=orm.comment,
        sort_order=orm.sort_order,
        user_id=orm.user_id,
    )


class AnimeListEntryRepository:
    """CRUD surface for the ``anime_list_entries`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def list_for_user(self, user_id: str) -> list[AnimeListEntryDTO]:
        """Return all entries for ``user_id``, ordered by ``sort_order``."""
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(AnimeListEntryRow)
                .where(AnimeListEntryRow.user_id == user_id)
                .order_by(AnimeListEntryRow.sort_order.asc())
            )
            return [_to_dto(orm) for orm in session.scalars(stmt).all()]

    def list_all(self) -> list[AnimeListEntryDTO]:
        """Return all entries across all users.

        Ordered by ``user_id`` then ``sort_order`` so the output is
        deterministic across callers (e.g. the UpdateLoop consumer).
        """
        with self._db.session() as session:
            stmt = sqlalchemy.select(AnimeListEntryRow).order_by(
                AnimeListEntryRow.user_id.asc(),
                AnimeListEntryRow.sort_order.asc(),
            )
            return [_to_dto(orm) for orm in session.scalars(stmt).all()]

    def replace_all_for_user(self, user_id: str, entries: collections.abc.Sequence[AnimeListEntryDTO]) -> None:
        """Atomically replace all entries for ``user_id``.

        Deletes every existing row for the user then inserts the new ones
        within a single session commit.
        """
        with self._db.session() as session:
            del_stmt = sqlalchemy.delete(AnimeListEntryRow).where(AnimeListEntryRow.user_id == user_id)
            session.execute(del_stmt)
            for entry in entries:
                orm = AnimeListEntryRow(
                    user_id=user_id,
                    sn=entry.sn,
                    enabled=entry.enabled,
                    mode=entry.mode,
                    tag=entry.tag,
                    season=entry.season,
                    anime_name=entry.anime_name,
                    comment=entry.comment,
                    sort_order=entry.sort_order,
                )
                session.add(orm)

    def get_owner_of(self, sn: int) -> str | None:
        """Return the ``user_id`` of the first entry whose ``sn`` matches.

        Returns ``None`` if no entry with that ``sn`` exists.
        """
        with self._db.session() as session:
            stmt = sqlalchemy.select(AnimeListEntryRow.user_id).where(AnimeListEntryRow.sn == sn).limit(1)
            result = session.execute(stmt).scalar_one_or_none()
            return result

    def list_all_owner_ids(self) -> list[str]:
        """Return distinct user_ids currently present in the anime_list_entries table."""
        with self._db.session() as session:
            rows = session.scalars(sqlalchemy.select(AnimeListEntryRow.user_id).distinct()).all()
            return list(rows)

    def update_anime_name(self, sn: int, user_id: str, anime_name: str | None) -> None:
        """Cache the series name on the list entry.

        Called by :class:`~app.scheduler.update_loop.UpdateLoop` after a
        successful metadata fetch so the UI can display the title before
        any episode finishes downloading.

        If no entry with ``(sn, user_id)`` exists, this is a no-op.
        """
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(AnimeListEntryRow)
                .where(
                    AnimeListEntryRow.sn == sn,
                    AnimeListEntryRow.user_id == user_id,
                )
                .values(anime_name=anime_name)
            )
            session.execute(stmt)

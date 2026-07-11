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
    custom_name: str | None = None  # user override for the name used in filenames
    bilingual: bool = False  # opt-in: download both 日文/中文配音 variants; dub gets [中] suffix
    comment: str = ''
    sort_order: int = 0
    user_id: str | None = None  # populated on read; ignored on write
    # Populated on read; None means this entry is not a duplicate.
    duplicate_of_entry_id: int | None = None
    # Row primary-key — populated on read; 0 / None means not yet persisted.
    id: int | None = None


def _to_dto(orm: AnimeListEntryRow) -> AnimeListEntryDTO:
    return AnimeListEntryDTO(
        sn=orm.sn,
        enabled=orm.enabled,
        mode=orm.mode,
        tag=orm.tag,
        season=orm.season,
        anime_name=orm.anime_name,
        custom_name=orm.custom_name,
        bilingual=orm.bilingual,
        comment=orm.comment,
        sort_order=orm.sort_order,
        user_id=orm.user_id,
        duplicate_of_entry_id=orm.duplicate_of_entry_id,
        id=orm.id,
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
                    custom_name=entry.custom_name,
                    bilingual=entry.bilingual,
                    comment=entry.comment,
                    sort_order=entry.sort_order,
                    duplicate_of_entry_id=entry.duplicate_of_entry_id,
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

    # ------------------------------------------------------------------
    # Duplicate detection helpers (Feature B)
    # ------------------------------------------------------------------

    def find_duplicate_source(
        self,
        anime_name: str,
        exclude_id: int | None = None,
    ) -> AnimeListEntryDTO | None:
        """Return the earliest entry (lowest id) whose ``anime_name`` matches.

        Matching is case-insensitive, trimmed.  Returns ``None`` if no match
        exists (or the only match is the entry being excluded).

        ``exclude_id`` should be the row's own ``id`` when checking an update
        so an entry doesn't detect itself as its own duplicate.
        """
        normalised = anime_name.strip().lower()
        if not normalised:
            return None

        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(AnimeListEntryRow)
                .where(
                    sqlalchemy.func.lower(sqlalchemy.func.trim(AnimeListEntryRow.anime_name)) == normalised,
                )
                .order_by(AnimeListEntryRow.id.asc())
            )
            for orm in session.scalars(stmt).all():
                if orm.id != exclude_id:
                    return _to_dto(orm)
        return None

    def reevaluate_duplicates_after_delete(
        self,
        deleted_row: AnimeListEntryDTO,
    ) -> list[int]:
        """After deleting ``deleted_row``, re-link duplicates that pointed at it.

        Finds all entries where ``duplicate_of_entry_id == deleted_row.id``.
        The earliest such entry (by id) becomes the new "first" occurrence —
        its ``duplicate_of_entry_id`` is cleared (but it stays disabled, so
        the user must manually re-enable it after reviewing).
        The remaining duplicates have their pointer updated to the new first.

        Returns the list of row ids that were modified.
        """
        if deleted_row.id is None:
            return []

        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(AnimeListEntryRow)
                .where(AnimeListEntryRow.duplicate_of_entry_id == deleted_row.id)
                .order_by(AnimeListEntryRow.id.asc())
            )
            affected = list(session.scalars(stmt).all())
            if not affected:
                return []

            # The first entry becomes the new "source" — clear its duplicate pointer.
            new_first = affected[0]
            new_first.duplicate_of_entry_id = None
            # Leave enabled=False; user must re-enable manually.

            # All others now point at the new first.
            new_first_id = new_first.id
            for orm in affected[1:]:
                orm.duplicate_of_entry_id = new_first_id

            session.flush()
            return [orm.id for orm in affected if orm.id is not None]

    def get_by_id(self, entry_id: int) -> AnimeListEntryDTO | None:
        """Fetch a single entry by its primary key."""
        with self._db.session() as session:
            orm = session.get(AnimeListEntryRow, entry_id)
            return _to_dto(orm) if orm is not None else None

    def get_by_user_sn(self, user_id: str, sn: int) -> AnimeListEntryDTO | None:
        """Fetch the entry for ``(user_id, sn)``, or ``None`` if absent."""
        with self._db.session() as session:
            stmt = (
                sqlalchemy.select(AnimeListEntryRow)
                .where(
                    AnimeListEntryRow.user_id == user_id,
                    AnimeListEntryRow.sn == sn,
                )
                .limit(1)
            )
            orm = session.scalars(stmt).first()
            return _to_dto(orm) if orm is not None else None

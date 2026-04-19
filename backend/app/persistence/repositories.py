"""Typed repository (DAO) layer for the ``anime`` table.

Callers get plain ``AnimeRow`` dataclasses back — no ORM objects escape the
repository so the session scope is predictable. Every method opens a fresh
session via ``Database.session()``.
"""

from __future__ import annotations

import dataclasses
import datetime
import typing as T

import sqlalchemy

from .models import Anime

if T.TYPE_CHECKING:
    from .db import Database


@dataclasses.dataclass(slots=True)
class AnimeRow:
    """Plain dataclass snapshot of a single ``anime`` row."""

    sn: int
    title: str
    anime_name: str
    episode: str
    status: int
    remote_status: int
    resolution: int
    file_size: int
    local_file_path: str | None
    created_time: datetime.datetime


def _to_row(orm: Anime) -> AnimeRow:
    return AnimeRow(
        sn=orm.sn,
        title=orm.title,
        anime_name=orm.anime_name,
        episode=orm.episode,
        status=orm.status,
        remote_status=orm.remote_status,
        resolution=orm.resolution,
        file_size=orm.file_size,
        local_file_path=orm.local_file_path,
        created_time=orm.created_time,
    )


class AnimeRepository:
    """CRUD surface for the ``anime`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def read_all(self) -> list[AnimeRow]:
        """Return every row, ordered by primary key ascending."""
        with self._db.session() as session:
            stmt = sqlalchemy.select(Anime).order_by(Anime.sn.asc())
            return [_to_row(row) for row in session.scalars(stmt).all()]

    def read(self, sn: int) -> AnimeRow | None:
        with self._db.session() as session:
            orm = session.get(Anime, sn)
            if orm is None:
                return None
            return _to_row(orm)

    def insert(
        self,
        *,
        sn: int,
        title: str,
        anime_name: str,
        episode: str,
        resolution: int,
        file_size: int,
        local_file_path: str | None = None,
    ) -> None:
        with self._db.session() as session:
            session.add(
                Anime(
                    sn=sn,
                    title=title,
                    anime_name=anime_name,
                    episode=episode,
                    resolution=resolution,
                    file_size=file_size,
                    local_file_path=local_file_path,
                )
            )

    def update(
        self,
        sn: int,
        *,
        status: int | None = None,
        remote_status: int | None = None,
        resolution: int | None = None,
        file_size: int | None = None,
        local_file_path: str | None = None,
        title: str | None = None,
        anime_name: str | None = None,
        episode: str | None = None,
    ) -> None:
        """Apply a partial update; unset kwargs leave their columns untouched."""
        # Build a dict of only the fields the caller explicitly passed.
        fields: dict[str, object] = {}
        if status is not None:
            fields['status'] = status
        if remote_status is not None:
            fields['remote_status'] = remote_status
        if resolution is not None:
            fields['resolution'] = resolution
        if file_size is not None:
            fields['file_size'] = file_size
        if local_file_path is not None:
            fields['local_file_path'] = local_file_path
        if title is not None:
            fields['title'] = title
        if anime_name is not None:
            fields['anime_name'] = anime_name
        if episode is not None:
            fields['episode'] = episode

        if not fields:
            return

        with self._db.session() as session:
            stmt = sqlalchemy.update(Anime).where(Anime.sn == sn).values(**fields)
            session.execute(stmt)

    def count_by_anime_name(self, anime_name: str) -> tuple[int, int]:
        """Return ``(known_episodes, downloaded_episodes)`` for a series.

        ``known`` = every row with the given ``anime_name``;
        ``downloaded`` = rows whose ``status == 1``.
        """
        with self._db.session() as session:
            known_stmt = sqlalchemy.select(sqlalchemy.func.count()).where(Anime.anime_name == anime_name)
            downloaded_stmt = sqlalchemy.select(sqlalchemy.func.count()).where(
                Anime.anime_name == anime_name, Anime.status == 1
            )
            known = int(session.execute(known_stmt).scalar_one())
            downloaded = int(session.execute(downloaded_stmt).scalar_one())
            return known, downloaded

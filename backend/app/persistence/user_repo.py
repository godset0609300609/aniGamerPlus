"""Repository for the ``users`` table.

Callers get plain :class:`UserRow` dataclasses back — no ORM objects escape
the repository so the session scope is predictable.
"""

from __future__ import annotations

import dataclasses
import datetime
import typing as T

import sqlalchemy

from .models import User

if T.TYPE_CHECKING:
    from .db import Database


@dataclasses.dataclass(slots=True)
class UserRow:
    """Plain dataclass snapshot of a single ``users`` row."""

    id: str
    username: str
    avatar_url: str | None
    role: str
    created_at: datetime.datetime
    last_login_at: datetime.datetime | None


def _to_row(orm: User) -> UserRow:
    return UserRow(
        id=orm.id,
        username=orm.username,
        avatar_url=orm.avatar_url,
        role=orm.role,
        created_at=orm.created_at,
        last_login_at=orm.last_login_at,
    )


class UserRepository:
    """CRUD surface for the ``users`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(
        self,
        *,
        id: str,
        username: str,
        avatar_url: str | None,
        role: str | None = None,
    ) -> UserRow:
        """Insert-or-update a user row.

        On insert the ``role`` is set to ``role`` if provided, else
        ``"downloader"``. On update the ``role`` is preserved unless
        ``role`` is explicitly supplied.
        """
        with self._db.session() as session:
            existing = session.get(User, id)
            if existing is None:
                now = datetime.datetime.now(datetime.UTC)
                orm = User(
                    id=id,
                    username=username,
                    avatar_url=avatar_url,
                    role=role if role is not None else 'downloader',
                    created_at=now,
                    last_login_at=now,
                )
                session.add(orm)
                session.flush()
                return _to_row(orm)
            else:
                existing.username = username
                existing.avatar_url = avatar_url
                existing.last_login_at = datetime.datetime.now(datetime.UTC)
                if role is not None:
                    existing.role = role
                session.flush()
                return _to_row(existing)

    def get(self, id: str) -> UserRow | None:
        """Return the user with the given ``id``, or ``None``."""
        with self._db.session() as session:
            orm = session.get(User, id)
            if orm is None:
                return None
            return _to_row(orm)

    def set_role(self, id: str, role: str) -> None:
        """Change the role for the given user id."""
        with self._db.session() as session:
            stmt = sqlalchemy.update(User).where(User.id == id).values(role=role)
            session.execute(stmt)

    def list_all(self) -> list[UserRow]:
        """Return every user ordered by ``created_at`` ascending."""
        with self._db.session() as session:
            stmt = sqlalchemy.select(User).order_by(User.created_at.asc())
            return [_to_row(orm) for orm in session.scalars(stmt).all()]

    def count_admins(self) -> int:
        """Return the number of users whose ``role == 'admin'``."""
        with self._db.session() as session:
            stmt = sqlalchemy.select(sqlalchemy.func.count()).where(User.role == 'admin')
            return int(session.execute(stmt).scalar_one())

    def first_admin(self) -> UserRow | None:
        """Return the earliest-created admin user, or ``None``."""
        with self._db.session() as session:
            stmt = sqlalchemy.select(User).where(User.role == 'admin').order_by(User.created_at.asc()).limit(1)
            orm = session.scalars(stmt).first()
            if orm is None:
                return None
            return _to_row(orm)

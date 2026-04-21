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
    telegram_chat_id: int | None = None
    telegram_link_token: str | None = None
    telegram_notify_enabled: bool = True


def _to_row(orm: User) -> UserRow:
    return UserRow(
        id=orm.id,
        username=orm.username,
        avatar_url=orm.avatar_url,
        role=orm.role,
        created_at=orm.created_at,
        last_login_at=orm.last_login_at,
        telegram_chat_id=orm.telegram_chat_id,
        telegram_link_token=orm.telegram_link_token,
        telegram_notify_enabled=orm.telegram_notify_enabled,
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

    # ------------------------------------------------------------------
    # Telegram binding helpers
    # ------------------------------------------------------------------

    def set_telegram_link_token(self, user_id: str, token: str | None) -> None:
        """Set (or clear) the ephemeral link token for *user_id*."""
        with self._db.session() as session:
            stmt = sqlalchemy.update(User).where(User.id == user_id).values(telegram_link_token=token)
            session.execute(stmt)

    def finalize_telegram_binding(self, user_id: str, chat_id: int) -> None:
        """Write *chat_id* and clear the link token atomically."""
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(User)
                .where(User.id == user_id)
                .values(telegram_chat_id=chat_id, telegram_link_token=None)
            )
            session.execute(stmt)

    def clear_telegram_binding(self, user_id: str) -> None:
        """Clear both *telegram_chat_id* and *telegram_link_token*."""
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(User)
                .where(User.id == user_id)
                .values(telegram_chat_id=None, telegram_link_token=None)
            )
            session.execute(stmt)

    def find_by_telegram_chat_id(self, chat_id: int) -> UserRow | None:
        """Return the user whose ``telegram_chat_id`` matches, or ``None``."""
        with self._db.session() as session:
            stmt = sqlalchemy.select(User).where(User.telegram_chat_id == chat_id).limit(1)
            orm = session.scalars(stmt).first()
            if orm is None:
                return None
            return _to_row(orm)

    def find_by_telegram_link_token(self, token: str) -> UserRow | None:
        """Return the user whose ``telegram_link_token`` matches, or ``None``."""
        with self._db.session() as session:
            stmt = sqlalchemy.select(User).where(User.telegram_link_token == token).limit(1)
            orm = session.scalars(stmt).first()
            if orm is None:
                return None
            return _to_row(orm)

    def set_telegram_notify_enabled(self, user_id: str, enabled: bool) -> None:
        """Update the per-user Telegram notification opt-in flag."""
        with self._db.session() as session:
            stmt = sqlalchemy.update(User).where(User.id == user_id).values(telegram_notify_enabled=enabled)
            session.execute(stmt)

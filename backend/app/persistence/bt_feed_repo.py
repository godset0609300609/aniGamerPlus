"""Repository for the ``bt_feed`` table.

Operates directly on the top-level Pydantic models (``BtFeed`` /
``BtFeedCreate`` / ``BtFeedUpdate`` from :mod:`app.models`) rather than an
intermediate persistence DTO — the shape the API layer needs is identical
to the shape this repository reads/writes, so an extra translation layer
would be pure ceremony.
"""

from __future__ import annotations

import datetime
import typing as T

import sqlalchemy
import sqlalchemy.exc

from ..models import BtFeed, BtFeedCreate, BtFeedUpdate
from ..security.url_guard import is_safe_public_url
from .models import BtFeedRow

if T.TYPE_CHECKING:
    from .db import Database


class DuplicateFeedError(Exception):
    """Raised when a create/update would violate the ``UNIQUE(url)`` constraint."""


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _to_model(row: BtFeedRow) -> BtFeed:
    return BtFeed(
        id=row.id,
        name=row.name,
        url=row.url,
        title_key=row.title_key,
        link_key=row.link_key,
        guid_key=row.guid_key,
        author_key=row.author_key,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class BtFeedRepository:
    """CRUD surface for the ``bt_feed`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def list_all(self) -> list[BtFeed]:
        with self._db.session() as session:
            stmt = sqlalchemy.select(BtFeedRow).order_by(BtFeedRow.id.asc())
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def list_enabled(self) -> list[BtFeed]:
        with self._db.session() as session:
            stmt = sqlalchemy.select(BtFeedRow).where(BtFeedRow.enabled.is_(True)).order_by(BtFeedRow.id.asc())
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def get(self, feed_id: int) -> BtFeed | None:
        with self._db.session() as session:
            row = session.get(BtFeedRow, feed_id)
            return _to_model(row) if row is not None else None

    def create(self, payload: BtFeedCreate) -> BtFeed:
        ok, reason = is_safe_public_url(payload.url)
        if not ok:
            raise ValueError(f'feed URL rejected by SSRF guard: {reason}')

        now_iso = _now_iso()
        row = BtFeedRow(
            name=payload.name,
            url=payload.url,
            title_key=payload.title_key,
            link_key=payload.link_key,
            guid_key=payload.guid_key,
            author_key=payload.author_key,
            enabled=payload.enabled,
            created_at=now_iso,
            updated_at=now_iso,
        )
        try:
            with self._db.session() as session:
                session.add(row)
                session.flush()
        except sqlalchemy.exc.IntegrityError as exc:
            raise DuplicateFeedError(f'feed url already exists: {payload.url}') from exc
        return _to_model(row)

    def update(self, feed_id: int, payload: BtFeedUpdate) -> BtFeed | None:
        changes: dict[str, T.Any] = payload.model_dump(exclude_unset=True)
        if not changes:
            return self.get(feed_id)
        changes['updated_at'] = _now_iso()
        try:
            with self._db.session() as session:
                stmt = sqlalchemy.update(BtFeedRow).where(BtFeedRow.id == feed_id).values(**changes)
                session.execute(stmt)
        except sqlalchemy.exc.IntegrityError as exc:
            raise DuplicateFeedError(f'feed url already exists: {changes.get("url")}') from exc
        return self.get(feed_id)

    def delete(self, feed_id: int) -> None:
        with self._db.session() as session:
            stmt = sqlalchemy.delete(BtFeedRow).where(BtFeedRow.id == feed_id)
            session.execute(stmt)

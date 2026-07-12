"""Repository for the ``bt_filter`` table.

Filters are global / admin-managed — a single shared list, not scoped per
user. ``replace_all`` mirrors
:meth:`~app.persistence.anime_list_repo.AnimeListEntryRepository.replace_all_for_user`'s
transactional delete-then-insert swap, but over the whole table.
"""

from __future__ import annotations

import collections.abc
import datetime
import json
import typing as T

import sqlalchemy

from ..models import BtFilter
from .models import BtFilterRow

if T.TYPE_CHECKING:
    from .db import Database


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _to_model(row: BtFilterRow) -> BtFilter:
    return BtFilter(
        id=row.id,
        name=row.name,
        keywords=json.loads(row.keywords_json),
        enabled=row.enabled,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class BtFilterRepository:
    """Read + transactional-replace surface for the ``bt_filter`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def list_all(self) -> list[BtFilter]:
        with self._db.session() as session:
            stmt = sqlalchemy.select(BtFilterRow).order_by(BtFilterRow.sort_order.asc())
            return [_to_model(r) for r in session.scalars(stmt).all()]

    def get(self, filter_id: int) -> BtFilter | None:
        with self._db.session() as session:
            row = session.get(BtFilterRow, filter_id)
            return _to_model(row) if row is not None else None

    def replace_all(self, filters: collections.abc.Sequence[BtFilter]) -> None:
        """Atomically replace every row: delete all, then insert *filters*.

        A fresh ``created_at`` is stamped for filters that don't already
        carry one (i.e. brand-new rows from the UI); an existing
        ``created_at`` is preserved so editing a filter doesn't reset it.
        """
        now_iso = _now_iso()
        with self._db.session() as session:
            session.execute(sqlalchemy.delete(BtFilterRow))
            for f in filters:
                session.add(
                    BtFilterRow(
                        name=f.name,
                        keywords_json=json.dumps(f.keywords, ensure_ascii=False),
                        enabled=f.enabled,
                        sort_order=f.sort_order,
                        created_at=f.created_at or now_iso,
                        updated_at=now_iso,
                    )
                )

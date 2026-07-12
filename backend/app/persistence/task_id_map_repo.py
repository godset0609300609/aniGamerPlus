"""Repository for the ``task_id_map`` table.

Maps (source, external_id) pairs to stable integer task SNs.
The actual task_sn returned to callers is ``BASE_OFFSET + row_id``
so that non-animad tasks never collide with animad sn values.
"""

from __future__ import annotations

import datetime
import typing as T

import sqlalchemy

from .models import TaskIdMapRow

if T.TYPE_CHECKING:
    from .db import Database

BASE_OFFSET: int = 2**31


class TaskIdMapRepository:
    """Allocates stable task_sn values for non-animad sources."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def allocate(self, source: str, external_id: str) -> int:
        """Return a stable task_sn for (source, external_id).

        Uses INSERT ... ON CONFLICT DO UPDATE so that repeated calls for the
        same pair return the same row id (and thus the same task_sn).
        Returns BASE_OFFSET + row_id.
        """
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        with self._db.session() as session:
            # SQLAlchemy's stub does not expose `dialects.sqlite` as an attribute;
            # it's a lazy submodule available at runtime.
            stmt = (
                sqlalchemy.dialects.sqlite.insert(TaskIdMapRow)  # type: ignore[attr-defined]
                .values(source=source, external_id=external_id, created_at=now_iso)
                .on_conflict_do_update(
                    index_elements=['source', 'external_id'],
                    set_={'source': source},
                )
            )
            result = session.execute(stmt)
            # `inserted_primary_key` is only defined on Result[InsertResult]; the
            # stub types the return of session.execute(insert) as Result[Any].
            row_id: int = result.inserted_primary_key[0]  # type: ignore[attr-defined]
        return BASE_OFFSET + row_id

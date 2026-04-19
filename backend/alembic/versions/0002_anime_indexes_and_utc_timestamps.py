"""Add indexes and UTC timestamp column.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-18

Two forward-only changes:

1. Index on ``anime_name`` and composite index on ``(anime_name, status)``.
   The animelist service hits both patterns (one row-per-series and
   downloaded-vs-known counts), so an index avoids full-table scans as the
   table grows.

2. New ``created_at_utc TIMESTAMP NULL`` column populated by
   ``CURRENT_TIMESTAMP`` on insert. The legacy ``CreatedTime`` column is
   kept in place for backward compatibility (the v24.6 downloader still
   reads it during database upgrades). New rows populate BOTH columns —
   the ORM fills ``CreatedTime`` via its existing server default
   (``datetime('now','localtime')``) and ``created_at_utc`` via the new
   default; downstream consumers should prefer the UTC column.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("anime")}
    if "ix_anime_anime_name" not in existing_indexes:
        op.create_index(
            "ix_anime_anime_name", "anime", ["anime_name"], unique=False
        )
    if "ix_anime_anime_name_status" not in existing_indexes:
        op.create_index(
            "ix_anime_anime_name_status",
            "anime",
            ["anime_name", "status"],
            unique=False,
        )

    existing_cols = {col["name"] for col in inspector.get_columns("anime")}
    if "created_at_utc" not in existing_cols:
        # batch_alter_table keeps SQLite happy when adding columns with
        # server defaults.
        with op.batch_alter_table("anime") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "created_at_utc",
                    sa.TIMESTAMP,
                    nullable=True,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("anime") as batch_op:
        batch_op.drop_column("created_at_utc")
    op.drop_index("ix_anime_anime_name_status", table_name="anime")
    op.drop_index("ix_anime_anime_name", table_name="anime")

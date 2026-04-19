"""Drop rename column; add season and anime_name columns to anime_list_entries.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-19

Breaking change: the ``rename`` column is dropped with no data migration.
Users who relied on custom rename values will need to re-configure season
numbers (default 1) via the updated frontend.

Two new columns:
- ``season`` INTEGER NOT NULL DEFAULT 1 — drives S{season:02d}E{ep:02d} filename
- ``anime_name`` VARCHAR(256) NULL — cached series name, populated by
  UpdateLoop.check_tasks so the UI shows the title before any download.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite does not support DROP COLUMN directly in older versions, but the
    # alembic batch mode transparently rewrites the table.
    with op.batch_alter_table("anime_list_entries") as batch_op:
        batch_op.drop_column("rename")
        batch_op.add_column(
            sa.Column("season", sa.Integer, nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("anime_name", sa.String(256), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("anime_list_entries") as batch_op:
        batch_op.drop_column("anime_name")
        batch_op.drop_column("season")
        batch_op.add_column(
            sa.Column(
                "rename",
                sa.String,
                nullable=False,
                server_default="''",
            )
        )

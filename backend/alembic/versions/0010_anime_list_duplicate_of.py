"""Add duplicate_of_entry_id column to anime_list_entries.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-21

New column:
- ``duplicate_of_entry_id`` Integer NULL FK → anime_list_entries.id ON DELETE SET NULL.

Data migration: for any pair of entries sharing the same anime_name (case-insensitive
trim), the entry with the higher id is marked as a duplicate pointing at the
earliest matching entry.  Those rows also have enabled forced to 0.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("anime_list_entries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "duplicate_of_entry_id",
                sa.Integer(),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            "fk_anime_list_entries_duplicate_of",
            "anime_list_entries",
            ["duplicate_of_entry_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Data migration: find existing duplicates by anime_name.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, anime_name FROM anime_list_entries"
            " WHERE anime_name IS NOT NULL AND trim(anime_name) != ''"
            " ORDER BY id ASC"
        )
    ).fetchall()

    # Group by normalised name; second+ entries by id ascending are duplicates.
    seen: dict[str, int] = {}  # normalised_name -> earliest id
    for row_id, anime_name in rows:
        key = anime_name.strip().lower()
        if key not in seen:
            seen[key] = row_id
        else:
            # This entry is a duplicate of seen[key].
            bind.execute(
                sa.text(
                    "UPDATE anime_list_entries"
                    " SET duplicate_of_entry_id = :source_id, enabled = 0"
                    " WHERE id = :row_id"
                ),
                {"source_id": seen[key], "row_id": row_id},
            )


def downgrade() -> None:
    with op.batch_alter_table("anime_list_entries", schema=None) as batch_op:
        batch_op.drop_constraint("fk_anime_list_entries_duplicate_of", type_="foreignkey")
        batch_op.drop_column("duplicate_of_entry_id")

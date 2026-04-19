"""Add custom_name column to anime_list_entries.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-19

New column:
- ``custom_name`` VARCHAR(256) NULL — user-supplied override for the anime
  name used in filenames only.  When non-empty, the filename builder uses
  this value instead of the auto-detected ``metadata.bangumi_name``.
  Distinct from the ``anime_name`` cache column (which reflects what the
  scraper detected).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("anime_list_entries", schema=None) as batch_op:
        batch_op.add_column(sa.Column("custom_name", sa.String(length=256), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("anime_list_entries", schema=None) as batch_op:
        batch_op.drop_column("custom_name")

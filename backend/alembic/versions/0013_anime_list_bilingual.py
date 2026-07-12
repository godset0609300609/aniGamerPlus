"""Add bilingual column to anime_list_entries.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-10

New column:
- ``bilingual`` Boolean NOT NULL DEFAULT false — per-entry opt-in toggle for
  animad series that expose 日文原音 and 中文配音 as separate SNs. When False
  (default), 中文配音-labeled episodes are dropped from every download mode.
  When True, both variants download and the 中文配音 filename gets a "[中]"
  suffix appended after the episode number to avoid colliding with the
  日文 file.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("anime_list_entries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("bilingual", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("anime_list_entries", schema=None) as batch_op:
        batch_op.drop_column("bilingual")

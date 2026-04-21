"""Add telegram_link_token_expires_at column to users table.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-21

New column:
- ``telegram_link_token_expires_at`` DateTime NULL — expiry timestamp for the
  ephemeral link token. NULL means the token has not been set yet or the
  column was added to an existing bound user (safe to treat as expired/absent).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("telegram_link_token_expires_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("telegram_link_token_expires_at")

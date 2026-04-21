"""Add Telegram columns to users table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-21

New columns:
- ``telegram_chat_id`` BigInteger NULL — Telegram chat ID after binding.
- ``telegram_link_token`` VARCHAR(64) NULL — ephemeral link token before binding.
- ``telegram_notify_enabled`` Boolean NOT NULL DEFAULT true — per-user notification opt-in.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(
            sa.Column("telegram_link_token", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "telegram_notify_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("telegram_notify_enabled")
        batch_op.drop_column("telegram_link_token")
        batch_op.drop_column("telegram_chat_id")

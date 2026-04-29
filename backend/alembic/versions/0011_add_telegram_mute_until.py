"""Add telegram_mute_until column to users for /silence feature.

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-28

NULL means "not muted".  When non-null, ``TelegramNotifier`` and the
progress publisher both skip DMs to that user until ``utcnow() >= mute_until``.
The column is nullable timezone-aware timestamp; readers use the row
unchanged (no separate booleans needed).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('telegram_mute_until', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('telegram_mute_until')

"""Add source/external_id to task_history and create task_id_map table.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-13

Pure additive migration — no backfill, no data migration.
Existing task_history rows retain NULL for both new columns;
NULL source is treated as 'animad' by application code.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('task_history', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('external_id', sa.Text(), nullable=True))

    op.create_table(
        'task_id_map',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('external_id', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.Text(),
            nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'),
        ),
        sa.UniqueConstraint('source', 'external_id', name='uq_task_id_map_source_external'),
    )


def downgrade() -> None:
    op.drop_table('task_id_map')

    with op.batch_alter_table('task_history', schema=None) as batch_op:
        batch_op.drop_column('external_id')
        batch_op.drop_column('source')

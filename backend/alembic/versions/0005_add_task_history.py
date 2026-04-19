"""Add task_history table for persisting completed/interrupted download records.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-18

The table stores one row per ``ProgressBus.start()`` call.  Rows are
INSERTed with ``final_status='(in_progress)'`` immediately on start and
then UPDATEd when ``ProgressBus.finish()`` fires.  On scheduler restart,
any rows still in the in-progress sentinel state are flipped to ``'中斷'``
so the UI never shows stale "正在下載" history.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Sentinel value stored in ``final_status`` for rows whose task has not yet
# completed.  Distinct from any user-visible status string.
_IN_PROGRESS_SENTINEL = "(in_progress)"


def upgrade() -> None:
    op.create_table(
        "task_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sn", sa.Integer, nullable=False),
        sa.Column("owner_id", sa.Text, nullable=True),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("bangumi_name", sa.Text, nullable=True),
        sa.Column("episode", sa.Text, nullable=True),
        sa.Column("resolution", sa.Text, nullable=True),
        sa.Column("final_status", sa.Text, nullable=False, server_default=_IN_PROGRESS_SENTINEL),
        sa.Column("started_at", sa.Text, nullable=True),
        sa.Column("finished_at", sa.Text, nullable=True),
        sa.Column("retries", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_task_history_sn", "task_history", ["sn"])
    op.create_index("ix_task_history_finished_at", "task_history", ["finished_at"])


def downgrade() -> None:
    op.drop_index("ix_task_history_finished_at", table_name="task_history")
    op.drop_index("ix_task_history_sn", table_name="task_history")
    op.drop_table("task_history")

"""Add users, anime_list_entries, and manual_tasks tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String, primary_key=True, nullable=False),
            sa.Column("username", sa.String, nullable=False),
            sa.Column("avatar_url", sa.String, nullable=True),
            sa.Column("role", sa.String, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("last_login_at", sa.DateTime, nullable=True),
        )

    if "anime_list_entries" not in existing_tables:
        op.create_table(
            "anime_list_entries",
            sa.Column(
                "id", sa.Integer, primary_key=True, autoincrement=True, nullable=False
            ),
            sa.Column(
                "user_id",
                sa.String,
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("sn", sa.Integer, nullable=False),
            sa.Column(
                "enabled",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("1"),
            ),
            sa.Column("mode", sa.String, nullable=True),
            sa.Column("tag", sa.String, nullable=False, server_default=sa.text("''")),
            sa.Column(
                "rename", sa.String, nullable=False, server_default=sa.text("''")
            ),
            sa.Column(
                "comment", sa.String, nullable=False, server_default=sa.text("''")
            ),
            sa.Column(
                "sort_order",
                sa.Integer,
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.UniqueConstraint("user_id", "sn", name="uq_anime_list_user_sn"),
        )
        op.create_index(
            "ix_anime_list_entries_user_id",
            "anime_list_entries",
            ["user_id"],
            unique=False,
        )
        op.create_index(
            "ix_anime_list_entries_user_id_sort_order",
            "anime_list_entries",
            ["user_id", "sort_order"],
            unique=False,
        )

    if "manual_tasks" not in existing_tables:
        op.create_table(
            "manual_tasks",
            sa.Column(
                "id", sa.Integer, primary_key=True, autoincrement=True, nullable=False
            ),
            sa.Column(
                "user_id",
                sa.String,
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("sn", sa.Integer, nullable=False),
            sa.Column("resolution", sa.String, nullable=False),
            sa.Column("mode", sa.String, nullable=False),
            sa.Column("thread_limit", sa.Integer, nullable=False),
            sa.Column("classify", sa.Boolean, nullable=False),
            sa.Column("danmu", sa.Boolean, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "status",
                sa.String,
                nullable=False,
                server_default=sa.text("'queued'"),
            ),
        )
        op.create_index(
            "ix_manual_tasks_user_id",
            "manual_tasks",
            ["user_id"],
            unique=False,
        )
        op.create_index(
            "ix_manual_tasks_status",
            "manual_tasks",
            ["status"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_manual_tasks_status", table_name="manual_tasks")
    op.drop_index("ix_manual_tasks_user_id", table_name="manual_tasks")
    op.drop_table("manual_tasks")
    op.drop_index(
        "ix_anime_list_entries_user_id_sort_order",
        table_name="anime_list_entries",
    )
    op.drop_index(
        "ix_anime_list_entries_user_id", table_name="anime_list_entries"
    )
    op.drop_table("anime_list_entries")
    op.drop_table("users")

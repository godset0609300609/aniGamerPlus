"""Baseline: adopt the legacy anime-schema.

Revision ID: 0001
Revises:
Create Date: 2026-04-18

Mirror of the legacy ``CREATE TABLE anime (...)`` in ``aniGamerPlus.py``
(L.839-849). This revision is a no-op against a database that already has
the table (we use ``create_table`` with ``checkfirst`` semantics via
batch mode); on fresh installs it creates the table.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "anime" in inspector.get_table_names():
        # Legacy database: the v24.6 downloader already ran and created the
        # table with ``CREATE TABLE IF NOT EXISTS``. Leave the data alone.
        return

    op.create_table(
        "anime",
        sa.Column("sn", sa.Integer, primary_key=True, nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("anime_name", sa.String(100), nullable=False),
        sa.Column("episode", sa.String(10), nullable=False),
        sa.Column(
            "status", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "remote_status", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "resolution", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "file_size", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column("local_file_path", sa.String(500), nullable=True),
        sa.Column(
            "CreatedTime",
            sa.TIMESTAMP,
            nullable=False,
            server_default=sa.text("(datetime('now','localtime'))"),
        ),
    )


def downgrade() -> None:
    op.drop_table("anime")

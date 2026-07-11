"""Add bt_feed, bt_filter, bt_feed_entry tables for the BT downloader pipeline.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-10

New tables:
- ``bt_feed`` — RSS/Atom feed sources, with per-feed field-mapping keys
  (``title_key`` / ``link_key`` / ``guid_key`` / ``author_key``).
- ``bt_filter`` — global (admin-managed) AND-keyword filter rules;
  ``keywords_json`` stores a JSON array of AND-ed keyword strings.
- ``bt_feed_entry`` — RSS entries ingested from a feed, deduped on
  ``(feed_id, guid)``, tracking filter-match + Put.io dispatch state.

All timestamp columns are TEXT (ISO-8601), written by the application layer
rather than a SQLite server default.

Note: these are brand-new tables, so plain ``op.create_table`` /
``op.create_index`` are used rather than ``batch_alter_table`` — SQLite's
ALTER-TABLE limitations (the reason ``batch_alter_table`` exists in this
migration series) only apply when modifying an *existing* table; a fresh
``CREATE TABLE`` needs no batching.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bt_feed",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title_key", sa.Text, nullable=False, server_default=sa.text("'title'")),
        sa.Column("link_key", sa.Text, nullable=False, server_default=sa.text("'link'")),
        sa.Column("guid_key", sa.Text, nullable=True),
        sa.Column("author_key", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.UniqueConstraint("url", name="uq_bt_feed_url"),
    )

    op.create_table(
        "bt_filter",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("keywords_json", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "bt_feed_entry",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("feed_id", sa.Integer, sa.ForeignKey("bt_feed.id"), nullable=False),
        sa.Column("guid", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("link", sa.Text, nullable=False),
        sa.Column("author", sa.Text, nullable=True),
        sa.Column("published_at", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.Text, nullable=False),
        sa.Column("matched_filter_id", sa.Integer, sa.ForeignKey("bt_filter.id"), nullable=True),
        sa.Column("dispatched_at", sa.Text, nullable=True),
        sa.Column("putio_transfer_id", sa.Integer, nullable=True),
        sa.Column("putio_status", sa.Text, nullable=True),
        sa.Column("local_path", sa.Text, nullable=True),
        sa.UniqueConstraint("feed_id", "guid", name="uq_bt_feed_entry_feed_guid"),
    )
    op.create_index("ix_bt_feed_entry_feed_id", "bt_feed_entry", ["feed_id"], unique=False)
    op.create_index("ix_bt_feed_entry_fetched_at", "bt_feed_entry", ["fetched_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_bt_feed_entry_fetched_at", table_name="bt_feed_entry")
    op.drop_index("ix_bt_feed_entry_feed_id", table_name="bt_feed_entry")
    op.drop_table("bt_feed_entry")
    op.drop_table("bt_filter")
    op.drop_table("bt_feed")

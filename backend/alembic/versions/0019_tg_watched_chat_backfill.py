"""Add historical-backfill columns to tg_watched_chat.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-11

Backs the "backfill last N days on add" feature: since
``app.tg_downloader.downloader.TgDownloadWatcher`` only reacts to *new*
messages via ``pyrogram.Client.add_handler(MessageHandler(...))``, a chat
just added to a user's watch list never gets its pre-existing media
downloaded without an explicit historical scan
(``app.tg_downloader.backfill.TgBackfillService``, dispatched via
``app.tasks.tg_backfill_tick.tg_backfill_actor``).

* ``backfill_enabled`` — opt-in flag set at chat-add (or edit) time.
* ``backfill_days`` — how many days of history to scan, 1-90 (validated at
  the Pydantic layer, not here).
* ``backfill_status`` — one of ``'pending'`` / ``'running'`` / ``'done'`` /
  ``'failed'``, or NULL if a backfill has never been requested for this
  chat.
* ``backfill_scanned_count`` / ``backfill_matched_count`` — live progress
  counters, updated periodically while a scan is ``'running'``.
* ``backfill_started_at`` / ``backfill_finished_at`` — ISO-8601 UTC,
  written by the application layer (same convention as every other
  timestamp column in this table).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tg_watched_chat", schema=None) as batch_op:
        batch_op.add_column(sa.Column("backfill_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("backfill_days", sa.Integer(), nullable=False, server_default=sa.text("7")))
        batch_op.add_column(sa.Column("backfill_status", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("backfill_scanned_count", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column("backfill_matched_count", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(sa.Column("backfill_started_at", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("backfill_finished_at", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tg_watched_chat", schema=None) as batch_op:
        batch_op.drop_column("backfill_finished_at")
        batch_op.drop_column("backfill_started_at")
        batch_op.drop_column("backfill_matched_count")
        batch_op.drop_column("backfill_scanned_count")
        batch_op.drop_column("backfill_status")
        batch_op.drop_column("backfill_days")
        batch_op.drop_column("backfill_enabled")

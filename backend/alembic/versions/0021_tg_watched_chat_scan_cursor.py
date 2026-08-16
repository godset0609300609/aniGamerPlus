"""Add periodic catch-up scan cursor columns to tg_watched_chat.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-16

Backs the cursor-based periodic catch-up scan
(``app.tg_downloader.catchup.TgCatchupService``, dispatched every
``ANIGAMERPLUS_TG_POLL_SECONDS`` seconds by
``app.tasks.tg_poll_tick.tg_poll_tick``). Unlike the on-demand historical
backfill (``app.tg_downloader.backfill.TgBackfillService``, revision 0019),
this scan runs automatically and needs a durable per-chat bookmark:
``app.tg_downloader.downloader.TgDownloadWatcher`` only reacts to *live*
messages pushed while its hydrogram handler is registered, so a process
restart, a disconnected client, or a handler that simply hasn't
(re)registered yet would otherwise silently drop every message that arrived
in that gap forever, with no automatic recovery.

* ``last_scanned_message_id`` — the LOW-WATER MARK: every message with
  ``id <= last_scanned_message_id`` is fully handled (matched-or-filtered
  and, on a match, downloaded-or-attempted). The scan walks
  ``Client.get_chat_history`` newest-first and stops as soon as it reaches
  this id. NULL means this chat has never had a catch-up scan; its first
  run instead falls back to a time cutoff (``ANIGAMERPLUS_TG_CATCHUP_HOURS``)
  and that run's newest message establishes the cursor baseline for every
  run after it.
* ``last_scanned_at`` — ISO-8601 UTC, written by the application layer
  (same convention as every other timestamp column in this table) when a
  catch-up scan for this chat last completed. Purely for observability/UI
  — the scan logic itself only ever reads ``last_scanned_message_id``.
* ``scan_resume_offset_id`` — set when a single run hits
  ``TgCatchupService``'s hard per-run message cap before reaching
  ``last_scanned_message_id`` (or, on a first scan, the time cutoff): holds
  the lowest ``message.id`` that run actually processed, so the *next* run
  resumes the downward walk from exactly there (via
  ``Client.get_chat_history``'s ``offset_id`` — see that method's docstring)
  instead of re-walking from the top and re-hitting the same cap boundary
  forever. NULL when no multi-run sweep is currently in progress for this
  chat.
* ``scan_pending_cursor`` — the ``newest_seen`` message id captured by the
  *first* run of a still-in-progress capped sweep. Preserved unchanged
  across every capped run of that sweep and only committed into
  ``last_scanned_message_id`` once the sweep finally reaches its true stop
  condition (old cursor or time cutoff) without hitting the cap again. NULL
  when no sweep is in progress.

``scan_resume_offset_id``/``scan_pending_cursor`` exist because a single
scalar high-water-mark cursor cannot express "a contiguous range at the top
of the chat has been handled, but there is still an unprocessed gap below
it" — see ``TgCatchupService``'s module docstring for the full reasoning
(and the livelock bug this pair replaces).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tg_watched_chat", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_scanned_message_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("last_scanned_at", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("scan_resume_offset_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("scan_pending_cursor", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tg_watched_chat", schema=None) as batch_op:
        batch_op.drop_column("scan_pending_cursor")
        batch_op.drop_column("scan_resume_offset_id")
        batch_op.drop_column("last_scanned_at")
        batch_op.drop_column("last_scanned_message_id")

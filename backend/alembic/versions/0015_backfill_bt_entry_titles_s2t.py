"""Backfill bt_feed_entry.title to Traditional Chinese (s2t).

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-11

One-shot data migration: convert every existing ``bt_feed_entry.title`` to
Traditional Chinese via OpenCC's ``s2t`` config, unconditionally — this runs
regardless of the current ``hanzi_convert`` setting value, since the user
wants the whole table normalized to 繁體 once and for all.

Going forward, ``BtDownloaderService.run_iteration`` converts new entry
titles at insert time (before ``insert_if_new``) whenever
``BtDownloaderSettings.hanzi_convert`` is true, so the DB stays consistent
without needing another backfill.
"""

from __future__ import annotations

from typing import Sequence, Union

import opencc
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    converter = opencc.OpenCC("s2t")

    rows = bind.execute(sa.text("SELECT id, title FROM bt_feed_entry")).fetchall()
    for row_id, title in rows:
        if title is None:
            continue
        converted = converter.convert(title)
        if converted == title:
            continue  # already Traditional (or has no Han characters) — skip the write
        bind.execute(
            sa.text("UPDATE bt_feed_entry SET title = :title WHERE id = :id"),
            {"title": converted, "id": row_id},
        )


def downgrade() -> None:
    # One-shot data conversion, s2t is not reversible without loss. Downgrade is a no-op.
    pass

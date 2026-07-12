"""Add remote_cleared_at column to bt_feed_entry.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-11

Supports two features:

* Auto-delete of the Put.io remote copy once a file has landed locally
  (``LandingWorker._maybe_auto_delete_remote`` / ``mark_remote_cleared``).
* Periodic post-landing remote-status refresh, which detects a transfer
  that Put.io itself removed (404 on poll) via ``mark_remote_removed``.

NULL means the remote copy hasn't been cleared/removed yet (or the entry
predates this feature). Non-NULL (ISO-8601 UTC, application-layer
timestamp — same convention as every other ``bt_feed_entry`` timestamp
column) means the row is no longer a target for the remote-refresh pass —
see ``BtFeedEntryRepository.list_landed_pending_remote_check``.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("bt_feed_entry", schema=None) as batch_op:
        batch_op.add_column(sa.Column("remote_cleared_at", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bt_feed_entry", schema=None) as batch_op:
        batch_op.drop_column("remote_cleared_at")

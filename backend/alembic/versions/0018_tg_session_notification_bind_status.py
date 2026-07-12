"""Add notification_bind_status / notification_bind_error to tg_session.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-11

Supports surfacing *why* the "one-bind-binds-all" notification bind
(``NotificationBinder.bind()``, fired automatically after a QR/phone login
success — see ``app.tg_downloader._login_common.persist_login_success``)
failed, instead of a generic "通知綁定失敗" in the Settings UI, and backs the
``POST /api/tg/session/rebind-notification`` retry endpoint.

* ``notification_bind_status`` — one of
  ``app.tg_downloader.notification_binder.NotificationBindResult``'s values
  (``'success'`` / ``'bot_username_not_configured'`` / ... ), or NULL if no
  bind has ever been attempted for this session (pre-migration row, or the
  notification binder wasn't configured at construction time).
* ``notification_bind_error`` — optional human-readable detail (e.g. the
  pyrogram exception message). Never the session string.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tg_session", schema=None) as batch_op:
        batch_op.add_column(sa.Column("notification_bind_status", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("notification_bind_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tg_session", schema=None) as batch_op:
        batch_op.drop_column("notification_bind_error")
        batch_op.drop_column("notification_bind_status")

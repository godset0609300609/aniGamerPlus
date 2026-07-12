"""Add tg_session, tg_watched_chat, tg_downloaded_media tables for the
Telegram User API downloader pipeline.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-11

New tables:
- ``tg_session`` — one pyrogram (MTProto) session per Discord user,
  Fernet-encrypted at rest. ``UNIQUE(user_id)`` — a Discord user binds at
  most one Telegram account.
- ``tg_watched_chat`` — per-user list of Telegram chats to monitor for new
  media, with per-chat media-type/size/format filters.
  ``UNIQUE(user_id, chat_id)``.
- ``tg_downloaded_media`` — dedup ledger of downloaded media,
  ``UNIQUE(user_id, chat_id, message_id)`` so a re-delivered/edited message
  never triggers a second download.

All timestamp columns are TEXT (ISO-8601), written by the application layer
— same convention as the ``bt_*`` tables from revision 0014. Brand-new
tables, so plain ``op.create_table`` / ``op.create_index`` (no
``batch_alter_table`` needed — that's only required when altering an
*existing* SQLite table).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tg_session",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Text, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_string_encrypted", sa.Text, nullable=False),
        sa.Column("phone_tail4", sa.Text, nullable=True),
        sa.Column("telegram_user_id", sa.BigInteger, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'active'")),
        sa.Column("added_at", sa.Text, nullable=False),
        sa.Column("last_active_at", sa.Text, nullable=True),
        sa.UniqueConstraint("user_id", name="uq_tg_session_user_id"),
    )

    op.create_table(
        "tg_watched_chat",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Text, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        sa.Column("chat_title", sa.Text, nullable=False),
        sa.Column("media_types", sa.Text, nullable=False, server_default=sa.text("'[\"video\"]'")),
        sa.Column("size_min_mb", sa.Integer, nullable=True),
        sa.Column("size_max_mb", sa.Integer, nullable=True),
        sa.Column("format_whitelist", sa.Text, nullable=True),
        sa.Column("save_path", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.UniqueConstraint("user_id", "chat_id", name="uq_tg_watched_chat_user_chat"),
    )
    op.create_index("ix_tg_watched_chat_user_id", "tg_watched_chat", ["user_id"], unique=False)

    op.create_table(
        "tg_downloaded_media",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Text, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.BigInteger, nullable=False),
        sa.Column("file_id", sa.Text, nullable=False),
        sa.Column("file_name", sa.Text, nullable=False),
        sa.Column("file_size", sa.BigInteger, nullable=False),
        sa.Column("downloaded_at", sa.Text, nullable=False),
        sa.Column("local_path", sa.Text, nullable=False),
        sa.Column("progress_sn", sa.Integer, nullable=True),
        sa.UniqueConstraint("user_id", "chat_id", "message_id", name="uq_tg_downloaded_media_user_chat_message"),
    )
    op.create_index("ix_tg_downloaded_media_user_id", "tg_downloaded_media", ["user_id"], unique=False)
    op.create_index("ix_tg_downloaded_media_downloaded_at", "tg_downloaded_media", ["downloaded_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tg_downloaded_media_downloaded_at", table_name="tg_downloaded_media")
    op.drop_index("ix_tg_downloaded_media_user_id", table_name="tg_downloaded_media")
    op.drop_table("tg_downloaded_media")
    op.drop_index("ix_tg_watched_chat_user_id", table_name="tg_watched_chat")
    op.drop_table("tg_watched_chat")
    op.drop_table("tg_session")

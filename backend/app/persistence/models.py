"""SQLAlchemy ORM models.

The ``anime`` table is mapped directly to the legacy CREATE TABLE statement
from ``aniGamerPlus.py`` (roughly L.839-849). A second revision will add a
UTC-aware ``created_at_utc`` column and a couple of indexes; the mapping
reflects both columns.

Revision 0003 adds ``users``, ``anime_list_entries``, and ``manual_tasks``.
"""

from __future__ import annotations

import datetime

import sqlalchemy
import sqlalchemy.orm

from .db import Base


class Anime(Base):
    """Mapping for the legacy ``anime`` table.

    Column names mirror the legacy schema exactly — in particular
    ``CreatedTime`` keeps its legacy PascalCase spelling so that existing
    databases remain readable with no data migration. New rows also populate
    ``created_at_utc`` (added in revision 0002) for analytics consumers that
    want proper UTC timestamps.
    """

    __tablename__ = 'anime'

    sn: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(sqlalchemy.Integer, primary_key=True, nullable=False)
    title: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.String(100), nullable=False)
    anime_name: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.String(100), nullable=False)
    episode: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.String(10), nullable=False)
    status: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default=sqlalchemy.text('0')
    )
    remote_status: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default=sqlalchemy.text('0')
    )
    resolution: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default=sqlalchemy.text('0')
    )
    file_size: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default=sqlalchemy.text('0')
    )
    local_file_path: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.String(500), nullable=True
    )
    # Legacy ``CreatedTime TIMESTAMP NOT NULL DEFAULT (datetime('now','localtime'))``.
    # Keep the PascalCase column name — do NOT rename the attribute either, the
    # repository layer writes/reads via this exact identifier.
    created_time: sqlalchemy.orm.Mapped[datetime.datetime] = sqlalchemy.orm.mapped_column(
        'CreatedTime',
        sqlalchemy.DateTime,
        nullable=False,
        server_default=sqlalchemy.text("(datetime('now','localtime'))"),
    )
    # Added by revision 0002. Nullable to stay backward-compatible with rows
    # inserted before the upgrade; ``server_default=CURRENT_TIMESTAMP`` means
    # new rows always populate it without the repo having to think about it.
    created_at_utc: sqlalchemy.orm.Mapped[datetime.datetime | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.DateTime,
        nullable=True,
        server_default=sqlalchemy.text('CURRENT_TIMESTAMP'),
    )


# ---------------------------------------------------------------------------
# Revision 0003 — user-scoped tables
# ---------------------------------------------------------------------------


class User(Base):
    """Discord user record.

    ``id`` is the Discord snowflake (string). ``role`` is one of
    ``"admin"`` or ``"downloader"``.
    """

    __tablename__ = 'users'

    id: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.String, primary_key=True)
    username: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.String, nullable=False)
    avatar_url: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.String, nullable=True)
    role: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.String, nullable=False)
    created_at: sqlalchemy.orm.Mapped[datetime.datetime] = sqlalchemy.orm.mapped_column(
        sqlalchemy.DateTime,
        nullable=False,
        server_default=sqlalchemy.text('CURRENT_TIMESTAMP'),
    )
    last_login_at: sqlalchemy.orm.Mapped[datetime.datetime | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.DateTime, nullable=True
    )
    # Added by revision 0008 — Telegram integration
    telegram_chat_id: sqlalchemy.orm.Mapped[int | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.BigInteger, nullable=True
    )
    telegram_link_token: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.String(64), nullable=True
    )
    telegram_notify_enabled: sqlalchemy.orm.Mapped[bool] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Boolean,
        nullable=False,
        default=True,
        server_default=sqlalchemy.true(),
    )
    # Added by revision 0009 — token TTL
    telegram_link_token_expires_at: sqlalchemy.orm.Mapped[datetime.datetime | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.DateTime, nullable=True
    )
    # Added by revision 0011 — /silence feature mute deadline
    telegram_mute_until: sqlalchemy.orm.Mapped[datetime.datetime | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=True,
    )


class AnimeListEntryRow(Base):
    """One entry in a user's anime watch list."""

    __tablename__ = 'anime_list_entries'

    id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True
    )
    user_id: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.String, sqlalchemy.ForeignKey('users.id'), nullable=False
    )
    sn: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(sqlalchemy.Integer, nullable=False)
    enabled: sqlalchemy.orm.Mapped[bool] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Boolean, nullable=False, default=True, server_default=sqlalchemy.text('1')
    )
    mode: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.String, nullable=True)
    tag: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.String, nullable=False, default='', server_default=sqlalchemy.text("''")
    )
    season: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=1, server_default=sqlalchemy.text('1')
    )
    anime_name: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.String(256), nullable=True, default=None
    )
    custom_name: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.String(256), nullable=True, default=None
    )
    # Added by revision 0013 — per-SN bilingual (中文配音) opt-in.
    bilingual: sqlalchemy.orm.Mapped[bool] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Boolean, nullable=False, default=False, server_default=sqlalchemy.false()
    )
    comment: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.String, nullable=False, default='', server_default=sqlalchemy.text("''")
    )
    sort_order: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default=sqlalchemy.text('0')
    )
    # Added by revision 0010 — duplicate detection.
    # Points to the earliest entry sharing the same anime_name (case-insensitive).
    # NULL means this entry is not a duplicate.  ON DELETE SET NULL so the FK
    # clears automatically when the original is deleted.
    duplicate_of_entry_id: sqlalchemy.orm.Mapped[int | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer,
        sqlalchemy.ForeignKey(
            'anime_list_entries.id',
            ondelete='SET NULL',
            name='fk_anime_list_entries_duplicate_of',
        ),
        nullable=True,
        default=None,
    )

    __table_args__ = (sqlalchemy.UniqueConstraint('user_id', 'sn', name='uq_anime_list_user_sn'),)


# ---------------------------------------------------------------------------
# Revision 0005 — task history
# ---------------------------------------------------------------------------


class TaskHistoryRow(Base):
    """One row per download attempt, written by :class:`TaskHistoryRepository`.

    ``final_status`` starts as the sentinel ``'(in_progress)'`` when the task
    begins and is UPDATE'd to the real terminal status when
    :meth:`~app.downloader.progress.ProgressBus.finish` fires.  On scheduler
    restart the repo's :meth:`~app.persistence.task_history_repo.TaskHistoryRepository.mark_interrupted_on_boot`
    flips any remaining in-progress rows to ``'中斷'``.

    ``started_at`` and ``finished_at`` are stored as ISO-8601 UTC strings so
    the table is readable without an ORM and avoids SQLite timezone confusion.
    """

    __tablename__ = 'task_history'

    id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True
    )
    sn: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(sqlalchemy.Integer, nullable=False, index=True)
    owner_id: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    filename: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    bangumi_name: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    episode: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    resolution: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    final_status: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=False, server_default='(in_progress)'
    )
    started_at: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    finished_at: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=True, index=True
    )
    retries: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, server_default='0'
    )
    # Added by revision 0012 — multi-source support
    source: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    external_id: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)


class TaskIdMapRow(Base):
    """Source-agnostic task id allocation table.

    Maps (source, external_id) pairs to stable integer row ids.  The actual
    ``task_sn`` used in the progress bus and history table is
    ``BASE_OFFSET + id`` where BASE_OFFSET = 2**31.
    """

    __tablename__ = 'task_id_map'

    id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True
    )
    source: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    external_id: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    created_at: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text,
        nullable=False,
        server_default=sqlalchemy.text('CURRENT_TIMESTAMP'),
    )

    __table_args__ = (sqlalchemy.UniqueConstraint('source', 'external_id', name='uq_task_id_map_source_external'),)


class ManualTaskRow(Base):
    """A manually-triggered download task."""

    __tablename__ = 'manual_tasks'

    id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True
    )
    user_id: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.String, sqlalchemy.ForeignKey('users.id'), nullable=False
    )
    sn: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(sqlalchemy.Integer, nullable=False)
    resolution: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.String, nullable=False)
    mode: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.String, nullable=False)
    thread_limit: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(sqlalchemy.Integer, nullable=False)
    classify: sqlalchemy.orm.Mapped[bool] = sqlalchemy.orm.mapped_column(sqlalchemy.Boolean, nullable=False)
    danmu: sqlalchemy.orm.Mapped[bool] = sqlalchemy.orm.mapped_column(sqlalchemy.Boolean, nullable=False)
    created_at: sqlalchemy.orm.Mapped[datetime.datetime] = sqlalchemy.orm.mapped_column(
        sqlalchemy.DateTime,
        nullable=False,
        server_default=sqlalchemy.text('CURRENT_TIMESTAMP'),
    )
    status: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.String,
        nullable=False,
        default='queued',
        server_default=sqlalchemy.text("'queued'"),
    )


# ---------------------------------------------------------------------------
# Revision 0014 — BT downloader (RSS -> keyword filter -> Put.io -> bangumi_dir)
# ---------------------------------------------------------------------------


class BtFeedRow(Base):
    """One RSS/Atom feed source polled by the BT downloader pipeline."""

    __tablename__ = 'bt_feed'

    id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True
    )
    name: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    url: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False, unique=True)
    title_key: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=False, default='title', server_default=sqlalchemy.text("'title'")
    )
    link_key: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=False, default='link', server_default=sqlalchemy.text("'link'")
    )
    # NULL -> the mapped ``link`` value is reused as the guid (see FeedFetcher).
    guid_key: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    author_key: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    enabled: sqlalchemy.orm.Mapped[bool] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Boolean, nullable=False, default=True, server_default=sqlalchemy.text('1')
    )
    created_at: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    updated_at: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)


class BtFilterRow(Base):
    """One AND-keyword filter rule. Global / admin-managed, shared across all users."""

    __tablename__ = 'bt_filter'

    id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True
    )
    name: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    # JSON array of AND-ed keyword strings — see BtFilterRepository for the (de)serialisation.
    keywords_json: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    enabled: sqlalchemy.orm.Mapped[bool] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Boolean, nullable=False, default=True, server_default=sqlalchemy.text('1')
    )
    sort_order: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default=sqlalchemy.text('0')
    )
    created_at: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    updated_at: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)


class BtFeedEntryRow(Base):
    """One RSS entry ingested from a :class:`BtFeedRow`, deduped on ``(feed_id, guid)``."""

    __tablename__ = 'bt_feed_entry'

    id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True
    )
    feed_id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, sqlalchemy.ForeignKey('bt_feed.id'), nullable=False
    )
    guid: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    title: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    link: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    author: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    published_at: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    fetched_at: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    # NULL = not matched by any filter yet.
    matched_filter_id: sqlalchemy.orm.Mapped[int | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, sqlalchemy.ForeignKey('bt_filter.id'), nullable=True
    )
    dispatched_at: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    putio_transfer_id: sqlalchemy.orm.Mapped[int | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=True
    )
    putio_status: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    local_path: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    # ISO-8601 UTC, set once the remote Put.io file has been cleaned up
    # (auto-deleted after landing, or detected as externally removed) — see
    # revision 0017.
    remote_cleared_at: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=True
    )

    __table_args__ = (sqlalchemy.UniqueConstraint('feed_id', 'guid', name='uq_bt_feed_entry_feed_guid'),)


# ---------------------------------------------------------------------------
# Revision 0016 — Telegram User API downloader (per-Discord-user MTProto session)
# ---------------------------------------------------------------------------


class TgSessionRow(Base):
    """One hydrogram (MTProto) session bound to a Discord user.

    ``session_string_encrypted`` is Fernet-encrypted (see
    ``app.security.crypto``) — never read/written in plaintext outside
    :class:`~app.persistence.tg_session_repo.TgSessionRepository`.
    """

    __tablename__ = 'tg_session'

    id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True
    )
    user_id: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, sqlalchemy.ForeignKey('users.id', ondelete='CASCADE'), nullable=False
    )
    session_string_encrypted: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=False
    )
    phone_tail4: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    telegram_user_id: sqlalchemy.orm.Mapped[int | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.BigInteger, nullable=True
    )
    status: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=False, default='active', server_default=sqlalchemy.text("'active'")
    )
    added_at: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    last_active_at: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    #: Outcome of the most recent ``NotificationBinder.bind()`` attempt for
    #: this session — one of ``NotificationBindResult``'s values (e.g.
    #: ``'success'`` / ``'bot_username_not_configured'`` / ...), or ``NULL``
    #: if no bind has ever been attempted (notifications not configured at
    #: bind time, or a pre-migration row). See
    #: ``app.tg_downloader.notification_binder.NotificationBindResult``.
    notification_bind_status: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=True
    )
    #: Human-readable detail for ``notification_bind_status`` (e.g. the
    #: hydrogram exception message) — never the session string itself.
    notification_bind_error: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=True
    )

    __table_args__ = (sqlalchemy.UniqueConstraint('user_id', name='uq_tg_session_user_id'),)


class TgWatchedChatRow(Base):
    """One Telegram chat a user has opted into monitoring for new media."""

    __tablename__ = 'tg_watched_chat'

    id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True
    )
    user_id: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, sqlalchemy.ForeignKey('users.id', ondelete='CASCADE'), nullable=False
    )
    chat_id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(sqlalchemy.BigInteger, nullable=False)
    chat_title: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    # JSON array of media type strings, e.g. '["video", "document"]'.
    media_types: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=False, default='["video"]', server_default=sqlalchemy.text('\'["video"]\'')
    )
    size_min_mb: sqlalchemy.orm.Mapped[int | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Integer, nullable=True)
    size_max_mb: sqlalchemy.orm.Mapped[int | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Integer, nullable=True)
    # JSON array of extension strings, or NULL for "no format restriction".
    format_whitelist: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=True
    )
    save_path: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=True)
    enabled: sqlalchemy.orm.Mapped[bool] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Boolean, nullable=False, default=True, server_default=sqlalchemy.text('1')
    )
    created_at: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    # ---- historical backfill (see app.tg_downloader.backfill.TgBackfillService) ----
    backfill_enabled: sqlalchemy.orm.Mapped[bool] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Boolean, nullable=False, default=False, server_default=sqlalchemy.text('0')
    )
    backfill_days: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=7, server_default=sqlalchemy.text('7')
    )
    # One of 'pending' / 'running' / 'done' / 'failed', or NULL if a backfill
    # has never been requested for this chat.
    backfill_status: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=True
    )
    backfill_scanned_count: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default=sqlalchemy.text('0')
    )
    backfill_matched_count: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default=sqlalchemy.text('0')
    )
    backfill_started_at: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=True
    )
    backfill_finished_at: sqlalchemy.orm.Mapped[str | None] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, nullable=True
    )

    __table_args__ = (sqlalchemy.UniqueConstraint('user_id', 'chat_id', name='uq_tg_watched_chat_user_chat'),)


class TgDownloadedMediaRow(Base):
    """Dedup ledger of Telegram media downloaded by
    :mod:`app.tg_downloader.downloader`.

    ``UNIQUE(user_id, chat_id, message_id)`` means a re-delivered or edited
    message is never downloaded twice — see
    :meth:`~app.persistence.tg_downloaded_media_repo.TgDownloadedMediaRepository.insert_if_new`.
    """

    __tablename__ = 'tg_downloaded_media'

    id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, primary_key=True, autoincrement=True
    )
    user_id: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Text, sqlalchemy.ForeignKey('users.id', ondelete='CASCADE'), nullable=False
    )
    chat_id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(sqlalchemy.BigInteger, nullable=False)
    message_id: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(sqlalchemy.BigInteger, nullable=False)
    file_id: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    file_name: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    file_size: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(sqlalchemy.BigInteger, nullable=False)
    downloaded_at: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    local_path: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(sqlalchemy.Text, nullable=False)
    progress_sn: sqlalchemy.orm.Mapped[int | None] = sqlalchemy.orm.mapped_column(sqlalchemy.Integer, nullable=True)

    __table_args__ = (
        sqlalchemy.UniqueConstraint(
            'user_id', 'chat_id', 'message_id', name='uq_tg_downloaded_media_user_chat_message'
        ),
    )

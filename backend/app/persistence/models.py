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
    comment: sqlalchemy.orm.Mapped[str] = sqlalchemy.orm.mapped_column(
        sqlalchemy.String, nullable=False, default='', server_default=sqlalchemy.text("''")
    )
    sort_order: sqlalchemy.orm.Mapped[int] = sqlalchemy.orm.mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default=sqlalchemy.text('0')
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

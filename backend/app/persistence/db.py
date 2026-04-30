"""SQLAlchemy engine + session wrapper and the Alembic runner entry point.

The rest of the persistence layer (repositories) takes a :class:`Database`
instance via constructor injection. This module owns the engine lifetime and
knows how to drive Alembic programmatically — nothing else should construct
``sqlalchemy`` engines directly.
"""

from __future__ import annotations

import collections.abc
import contextlib
import pathlib
import sqlite3
import typing as T

import alembic.command
import alembic.config
import sqlalchemy
import sqlalchemy.event
import sqlalchemy.orm

if T.TYPE_CHECKING:
    from ..logging_ import Logger


class Base(sqlalchemy.orm.DeclarativeBase):
    """Declarative base for every ORM model in :mod:`app.persistence.models`."""


# Location of ``alembic.ini`` and the ``alembic/`` directory at the backend
# root. ``paths.py/__file__`` -> persistence -> app -> backend.
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _BACKEND_ROOT / 'alembic.ini'
_ALEMBIC_DIR = _BACKEND_ROOT / 'alembic'


class Database:
    """Thin wrapper around a sync SQLAlchemy engine.

    Parameters
    ----------
    url:
        SQLAlchemy URL, e.g. ``sqlite:///./aniGamer.db``.
    logger:
        Project logger. The database logs connection-level events through it.
    echo:
        Pass-through for SQLAlchemy's ``echo=`` parameter. Useful in tests.
    """

    def __init__(self, url: str, logger: Logger, *, echo: bool = False) -> None:
        self._url = url
        self._logger = logger
        # ``check_same_thread=False`` matches the legacy sqlite3 usage: the
        # downloader writes from worker threads while the FastAPI thread reads.
        connect_args: dict[str, object] = {}
        if url.startswith('sqlite'):
            connect_args['check_same_thread'] = False
            # 30 s busy_timeout at the Python sqlite3 driver level (belt-and-suspenders
            # alongside the PRAGMA below — the PRAGMA wins once a connection is open).
            connect_args['timeout'] = 30
        self._engine: sqlalchemy.Engine = sqlalchemy.create_engine(
            url, echo=echo, future=True, connect_args=connect_args
        )

        # Register WAL + busy_timeout PRAGMAs for every SQLite connection so
        # that concurrent writers (api, scheduler, worker) wait up to 30 s
        # instead of raising "database is locked" immediately.
        if self._engine.url.get_backend_name() == 'sqlite':

            @sqlalchemy.event.listens_for(self._engine, 'connect')
            def _set_sqlite_pragmas(dbapi_conn: sqlite3.Connection, _connection_record: object) -> None:
                cursor = dbapi_conn.cursor()
                try:
                    cursor.execute('PRAGMA journal_mode=WAL')
                    cursor.execute('PRAGMA synchronous=NORMAL')
                    cursor.execute('PRAGMA busy_timeout=30000')  # 30 s — matches connect_args timeout
                finally:
                    cursor.close()

        self._session_factory = sqlalchemy.orm.sessionmaker(bind=self._engine, expire_on_commit=False, future=True)

    @property
    def engine(self) -> sqlalchemy.Engine:
        return self._engine

    @property
    def url(self) -> str:
        return self._url

    @contextlib.contextmanager
    def session(self) -> collections.abc.Iterator[sqlalchemy.orm.Session]:
        """Context-manager yielding a session; commits on success, rolls back on error."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """Tear the connection pool down. Safe to call more than once."""
        self._engine.dispose()

    # ------------------------------------------------------------------ migrations

    def run_baseline_migrations(self) -> None:
        """Apply Alembic migrations up to ``head``.

        Safe to call repeatedly. On a pre-existing SQLite file that already
        carries the legacy ``anime`` table (from the v24.6 downloader),
        Alembic's version table is simply stamped to the baseline and the
        subsequent revisions apply normally (index + timestamp augment are
        idempotent).
        """
        cfg = alembic.config.Config(str(_ALEMBIC_INI))
        cfg.set_main_option('script_location', str(_ALEMBIC_DIR))
        cfg.set_main_option('sqlalchemy.url', self._url)
        # Skip alembic's fileConfig() call so it cannot overwrite the
        # dictConfig we already applied (fileConfig defaults to
        # disable_existing_loggers=True which would silence app.* loggers).
        cfg.attributes['skip_log_config'] = True
        # Alembic writes to stdout by default; keep that quiet unless the
        # caller opted into ``echo``. The logger still captures explicit events.
        alembic.command.upgrade(cfg, 'head')

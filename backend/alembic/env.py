"""Alembic environment for aniGamerPlus.

Uses the sync engine — matches :class:`app.persistence.db.Database`.
``target_metadata`` pulls from :class:`app.persistence.db.Base` so that
``alembic revision --autogenerate`` (if ever used) picks up our models.
"""

from __future__ import annotations

import pathlib
import sys

import os
import re
import alembic.context
import sqlalchemy
import sqlalchemy.pool
import logging.config

# Ensure ``backend/`` is on sys.path so ``from app...`` imports resolve when
# Alembic is launched from its own working directory.
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Import AFTER sys.path is prepared — the real target metadata lives here.
from app.persistence.db import Base  # noqa: E402
from app.persistence import models as _models  # noqa: F401,E402  (register mappings)


config = alembic.context.config

if config.config_file_name is not None:
    logging.config.fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_env_placeholders(url: str) -> str:
    """Expand ``${VAR:-fallback}`` placeholders in an alembic.ini URL.

    Programmatic callers (``Database.run_baseline_migrations``) override the
    URL via ``cfg.set_main_option`` before Alembic ever reads it, so this
    branch is only hit when someone runs ``alembic upgrade head`` from the
    shell.
    """

    def _sub(match: "re.Match[str]") -> str:
        var_name = match.group(1)
        fallback = match.group(2) or ""
        return os.environ.get(var_name, fallback)

    return re.sub(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}", _sub, url)


_raw_url = config.get_main_option("sqlalchemy.url")
if _raw_url and "${" in _raw_url:
    config.set_main_option("sqlalchemy.url", _resolve_env_placeholders(_raw_url))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL to the script output; no engine is created.
    """
    url = config.get_main_option("sqlalchemy.url")
    alembic.context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with alembic.context.begin_transaction():
        alembic.context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode via a real Engine."""
    connectable = sqlalchemy.engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=sqlalchemy.pool.NullPool,
    )

    with connectable.connect() as connection:
        alembic.context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with alembic.context.begin_transaction():
            alembic.context.run_migrations()


if alembic.context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

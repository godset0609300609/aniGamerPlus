"""API-layer dependency helpers that layer on top of :mod:`app.auth.deps`.

This module adds the sentinel-admin behaviour for single-user (auth disabled)
mode without touching :mod:`app.auth.deps` itself.

Sentinel admin
--------------
When ``auth.enabled`` is ``False`` (the default), every request is treated as
an admin named ``__anonymous_admin__``.  The sentinel ``UserRow`` is not
persisted to the DB; it is created in memory on every request and carries
``role="admin"`` so all permission gates pass.

The three public dependency wrappers here shadow the ones in
:mod:`app.auth.deps` for use in route handlers:

- :func:`current_user_opt`   — ``UserRow | None`` (None only when auth enabled + no session)
- :func:`require_any_user`   — ``UserRow`` (sentinel or real user, raises 401 otherwise)
- :func:`require_admin_user` — ``UserRow`` with role==admin (raises 403 otherwise)
"""

from __future__ import annotations

import collections.abc
import datetime
import typing as T

import fastapi
import starlette.requests

from ..auth.deps import current_user
from ..models import AppSettings
from ..persistence.user_repo import UserRow

# ---------------------------------------------------------------------------
# Settings dependency (reuse pattern from auth_api.py)
# ---------------------------------------------------------------------------

_get_settings_cached: list[collections.abc.Callable[[], AppSettings]] = []


def _get_settings() -> AppSettings:
    if not _get_settings_cached:
        from ..core import build_container

        _get_settings_cached.append(build_container().settings_repo.load)
    return _get_settings_cached[0]()


# Sentinel user returned when auth is disabled.
_SENTINEL_ADMIN = UserRow(
    id='__anonymous_admin__',
    username='本機使用者',
    avatar_url=None,
    role='admin',
    created_at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC),
    last_login_at=None,
)

# Expose as a FastAPI dependency so tests can override it.
get_settings = _get_settings
"""FastAPI dependency: resolves current :class:`AppSettings`."""


# ---------------------------------------------------------------------------
# Sentinel-aware dependency chain
# ---------------------------------------------------------------------------


async def current_user_opt(
    connection: starlette.requests.HTTPConnection,
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
    base_user: T.Annotated[UserRow | None, fastapi.Depends(current_user)],
) -> UserRow | None:
    """Return the current user, or sentinel admin when auth is disabled.

    When ``auth.enabled`` is ``False`` every call returns
    :data:`_SENTINEL_ADMIN`.  When ``auth.enabled`` is ``True`` this
    returns whatever :func:`~app.auth.deps.current_user` resolves (which
    may be ``None`` if there is no session).
    """
    if not settings.auth.enabled:
        return _SENTINEL_ADMIN
    return base_user


async def require_any_user(
    user: T.Annotated[UserRow | None, fastapi.Depends(current_user_opt)],
) -> UserRow:
    """Raise ``HTTP 401`` if there is no authenticated user.

    In single-user mode (auth disabled) this always returns the sentinel
    admin so it never raises.
    """
    if user is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail='Authentication required',
        )
    return user


async def require_admin_user(
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
) -> UserRow:
    """Raise ``HTTP 403`` if the authenticated user is not an admin."""
    if user.role != 'admin':
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail='Admin role required',
        )
    return user

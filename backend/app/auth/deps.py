"""FastAPI dependency functions for Discord-OAuth session auth + RBAC.

The three public dependencies follow a chain::

    current_user  →  require_user  →  require_admin

``current_user`` is the only one that touches the session / database; the
two ``require_*`` variants just guard on the result.
"""

from __future__ import annotations

import typing as T

import anyio.to_thread
import fastapi
import starlette.requests

from ..persistence.user_repo import UserRepository, UserRow
from ..services._factory import container_bound

# ---------------------------------------------------------------------------
# Repository factory (zero-arg, lru_cache-style via container_bound)
# ---------------------------------------------------------------------------

get_user_repo = container_bound(lambda c: c.user_repo)
"""FastAPI dependency resolver for :class:`UserRepository`."""


# ---------------------------------------------------------------------------
# Dependency chain
# ---------------------------------------------------------------------------


async def current_user(
    connection: starlette.requests.HTTPConnection,
    user_repo: T.Annotated[UserRepository, fastapi.Depends(get_user_repo)],
) -> UserRow | None:
    """Read ``user_id`` from the session and look up the database.

    Accepts ``HTTPConnection`` (the shared base of ``Request`` and
    ``WebSocket``) so the same dependency works for both HTTP routes and
    WebSocket handshakes.

    Returns ``None`` when no session is present or the user ID is not found.
    This dependency never raises — callers that need a mandatory user should
    depend on :func:`require_user` instead.
    """
    user_id: str | None = connection.session.get('user_id')
    if not user_id:
        return None
    uid = user_id
    return await anyio.to_thread.run_sync(lambda: user_repo.get(uid))


async def require_user(
    user: T.Annotated[UserRow | None, fastapi.Depends(current_user)],
) -> UserRow:
    """Raise ``HTTP 401`` if the request carries no valid session."""
    if user is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail='Authentication required',
        )
    return user


async def require_admin(
    user: T.Annotated[UserRow, fastapi.Depends(require_user)],
) -> UserRow:
    """Raise ``HTTP 403`` if the authenticated user is not an admin."""
    if user.role != 'admin':
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail='Admin role required',
        )
    return user

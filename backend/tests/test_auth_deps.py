"""Tests for :mod:`app.auth.deps` dependency functions.

We test the three dependency functions in isolation using fake/stub objects
— no real database or HTTP server involved.
"""

from __future__ import annotations

import dataclasses
import datetime

import fastapi
import pytest

from app.auth.deps import current_user, require_admin, require_user
from app.persistence.user_repo import UserRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(role: str = 'downloader') -> UserRow:
    return UserRow(
        id='42',
        username='alice',
        avatar_url=None,
        role=role,
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )


class FakeUserRepo:
    """Minimal stand-in for :class:`UserRepository`."""

    def __init__(self, user: UserRow | None) -> None:
        self._user = user

    def get(self, id: str) -> UserRow | None:  # noqa: A002
        return self._user if (self._user and self._user.id == id) else None


class FakeRequest:
    """Minimal stand-in for :class:`starlette.requests.Request`.

    Only provides a ``session`` dict — nothing else needed by
    :func:`current_user`.
    """

    def __init__(self, session: dict | None = None) -> None:
        self.session: dict = session or {}


# ---------------------------------------------------------------------------
# current_user
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_current_user_returns_none_with_no_session() -> None:
    request = FakeRequest(session={})
    result = await current_user(request, FakeUserRepo(None))  # type: ignore[arg-type]
    assert result is None


@pytest.mark.anyio
async def test_current_user_returns_none_when_user_not_in_db() -> None:
    request = FakeRequest(session={'user_id': 'unknown_id'})
    repo = FakeUserRepo(None)
    result = await current_user(request, repo)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.anyio
async def test_current_user_returns_user_when_session_valid() -> None:
    user = _make_user()
    request = FakeRequest(session={'user_id': '42'})
    repo = FakeUserRepo(user)
    result = await current_user(request, repo)  # type: ignore[arg-type]
    assert result is not None
    assert result.id == '42'
    assert result.username == 'alice'


# ---------------------------------------------------------------------------
# require_user
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_require_user_raises_401_when_none() -> None:
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await require_user(None)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_require_user_passes_through_user() -> None:
    user = _make_user()
    result = await require_user(user)
    assert result is user


# ---------------------------------------------------------------------------
# require_admin
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_require_admin_raises_403_for_downloader() -> None:
    user = _make_user(role='downloader')
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await require_admin(user)
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_require_admin_passes_through_admin() -> None:
    user = _make_user(role='admin')
    result = await require_admin(user)
    assert result is user

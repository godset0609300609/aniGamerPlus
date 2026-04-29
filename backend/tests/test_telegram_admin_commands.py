"""Tests for admin Telegram commands in TelegramCommandDispatcher.

Since /admin_stats, /admin_users, /admin_cancel are now simplified to redirect
to /menu, these tests verify the redirect behaviour. The actual admin UI is
exercised through test_telegram_menu.py via MenuRenderer.
"""

from __future__ import annotations

import datetime
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import TaskProgressSnapshot
from app.persistence.user_repo import UserRow
from app.services.telegram_commands import TelegramCommandDispatcher
from app.services.telegram_rate_limiter import TelegramRateLimiter

# ---------------------------------------------------------------------------
# Helpers (copied/shared pattern from test_telegram_commands.py)
# ---------------------------------------------------------------------------


def _make_user(role: str = 'downloader', uid: str = 'user-1', chat_id: int = 111) -> UserRow:
    return UserRow(
        id=uid,
        username='Alice',
        avatar_url=None,
        role=role,
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        last_login_at=None,
        telegram_chat_id=chat_id,
        telegram_notify_enabled=True,
    )


def _make_dispatcher(
    *,
    client: MagicMock | None = None,
    animelist_service: MagicMock | None = None,
    task_service: MagicMock | None = None,
    progress_service: MagicMock | None = None,
    user_repo: MagicMock | None = None,
    rate_limiter: TelegramRateLimiter | None = None,
) -> tuple[TelegramCommandDispatcher, MagicMock]:
    if client is None:
        client = MagicMock()
        client.send_message = AsyncMock(return_value={})

    if animelist_service is None:
        animelist_service = MagicMock()
        animelist_service.list_entries = AsyncMock(return_value=[])
        animelist_service.replace_entries = AsyncMock(return_value=None)

    if task_service is None:
        task_service = MagicMock()
        task_service.enqueue = AsyncMock(return_value=None)
        task_service.cancel_task = AsyncMock(return_value=None)

    if progress_service is None:
        progress_service = MagicMock()
        progress_service.snapshot = AsyncMock(return_value=TaskProgressSnapshot(tasks={}))

    if user_repo is None:
        user_repo = MagicMock()
        user_repo.list_all = MagicMock(return_value=[])

    if rate_limiter is None:
        rate_limiter = TelegramRateLimiter(max_provider=lambda: 100)

    logger = logging.getLogger('test_telegram_admin_commands')

    dispatcher = TelegramCommandDispatcher(
        client_provider=lambda: client,
        user_repo=user_repo,
        animelist_service=animelist_service,
        task_service=task_service,
        progress_service=progress_service,
        rate_limiter=rate_limiter,
        logger=logger,  # type: ignore[arg-type]
    )
    return dispatcher, client


def _last_message(client: MagicMock) -> str:
    assert client.send_message.called, 'send_message was not called'
    return client.send_message.call_args[0][1]


# ---------------------------------------------------------------------------
# /admin_stats — simplified to /menu redirect
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_admin_stats_as_admin() -> None:
    """Simplified: /admin_stats now redirects admins to /menu."""
    admin = _make_user(role='admin', uid='admin-1')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=admin, text='/admin_stats')
    msg = _last_message(client)

    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_admin_stats_as_downloader_forbidden() -> None:
    """Simplified: /admin_stats redirects everyone to /menu."""
    downloader = _make_user(role='downloader')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=downloader, text='/admin_stats')
    msg = _last_message(client)

    assert '/menu' in msg or '控制台' in msg


# ---------------------------------------------------------------------------
# /admin_users — simplified to /menu redirect
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_admin_users_pagination() -> None:
    """Simplified: /admin_users now redirects to /menu."""
    admin = _make_user(role='admin', uid='admin-1')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=admin, text='/admin_users 1')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_admin_users_page2_no_next() -> None:
    """Simplified: /admin_users 2 also redirects to /menu."""
    admin = _make_user(role='admin', uid='admin-1')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=admin, text='/admin_users 2')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_admin_users_as_downloader_forbidden() -> None:
    """Simplified: /admin_users redirects everyone to /menu."""
    downloader = _make_user(role='downloader')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=downloader, text='/admin_users')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


# ---------------------------------------------------------------------------
# /admin_cancel — simplified to /menu redirect
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_admin_cancel_mentions_original_owner() -> None:
    """Simplified: /admin_cancel now redirects to /menu."""
    admin = _make_user(role='admin', uid='admin-1')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=admin, text='/admin_cancel 48430')
    msg = _last_message(client)

    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_admin_cancel_task_not_found() -> None:
    """Simplified: /admin_cancel redirects to /menu regardless."""
    admin = _make_user(role='admin', uid='admin-1')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=admin, text='/admin_cancel 99999')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_admin_cancel_as_downloader_forbidden() -> None:
    """Simplified: /admin_cancel redirects everyone to /menu."""
    downloader = _make_user(role='downloader')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=downloader, text='/admin_cancel 48430')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg

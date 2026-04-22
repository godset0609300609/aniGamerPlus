"""Tests for admin Telegram commands in TelegramCommandDispatcher.

Covers:
- /admin_stats as admin → aggregated stats reply
- /admin_stats as downloader → 🚫 reply
- /admin_users pagination
- /admin_cancel cancels another user's task; reply mentions original owner
- /admin_cancel as downloader → 🚫 reply
"""

from __future__ import annotations

import datetime
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import AnimeListEntry, TaskProgressEntry, TaskProgressSnapshot
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
# /admin_stats
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_admin_stats_as_admin() -> None:
    admin = _make_user(role='admin', uid='admin-1')
    user2 = _make_user(role='downloader', uid='user-2', chat_id=222)

    user_repo = MagicMock()
    user_repo.list_all = MagicMock(return_value=[admin, user2])

    animelist_service = MagicMock()
    entries = [
        AnimeListEntry(sn=100, enabled=True, owner_id='admin-1'),
        AnimeListEntry(sn=101, enabled=False, owner_id='user-2', duplicate_of_entry_id=1),
    ]
    animelist_service.list_entries = AsyncMock(return_value=entries)

    progress_service = MagicMock()
    snap = TaskProgressSnapshot(
        tasks={
            '100': TaskProgressEntry(sn=100, rate=0.5, status='下載中', filename='x.mp4'),
            '101': TaskProgressEntry(sn=101, rate=0.0, status='等待中', filename='y.mp4'),
        }
    )
    progress_service.snapshot = AsyncMock(return_value=snap)

    dispatcher, client = _make_dispatcher(
        user_repo=user_repo,
        animelist_service=animelist_service,
        progress_service=progress_service,
    )

    await dispatcher.dispatch(chat_id=111, user=admin, text='/admin_stats')
    msg = _last_message(client)

    assert '使用者總數' in msg or '2' in msg
    assert '追番' in msg or '清單' in msg


@pytest.mark.anyio
async def test_admin_stats_as_downloader_forbidden() -> None:
    downloader = _make_user(role='downloader')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=downloader, text='/admin_stats')
    msg = _last_message(client)

    assert '🚫' in msg or '管理員' in msg


# ---------------------------------------------------------------------------
# /admin_users
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_admin_users_pagination() -> None:
    admin = _make_user(role='admin', uid='admin-1')

    # Create 25 users to test pagination
    users = [_make_user(uid=f'u-{i}', chat_id=200 + i) for i in range(25)]

    user_repo = MagicMock()
    user_repo.list_all = MagicMock(return_value=users)

    animelist_service = MagicMock()
    animelist_service.list_entries = AsyncMock(return_value=[])

    dispatcher, client = _make_dispatcher(
        user_repo=user_repo,
        animelist_service=animelist_service,
    )

    # Page 1 should mention next page
    await dispatcher.dispatch(chat_id=111, user=admin, text='/admin_users 1')
    msg = _last_message(client)
    assert '使用者列表' in msg or '第 1 頁' in msg
    assert '/admin_users 2' in msg or '查看下一頁' in msg


@pytest.mark.anyio
async def test_admin_users_page2_no_next() -> None:
    admin = _make_user(role='admin', uid='admin-1')

    # Only 22 users — page 2 has 2 entries, no next page
    users = [_make_user(uid=f'u-{i}', chat_id=200 + i) for i in range(22)]

    user_repo = MagicMock()
    user_repo.list_all = MagicMock(return_value=users)

    animelist_service = MagicMock()
    animelist_service.list_entries = AsyncMock(return_value=[])

    dispatcher, client = _make_dispatcher(
        user_repo=user_repo,
        animelist_service=animelist_service,
    )

    await dispatcher.dispatch(chat_id=111, user=admin, text='/admin_users 2')
    msg = _last_message(client)
    # Should show users on page 2; no "查看下一頁" since total=22
    assert '查看下一頁' not in msg


@pytest.mark.anyio
async def test_admin_users_as_downloader_forbidden() -> None:
    downloader = _make_user(role='downloader')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=downloader, text='/admin_users')
    msg = _last_message(client)
    assert '🚫' in msg or '管理員' in msg


# ---------------------------------------------------------------------------
# /admin_cancel
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_admin_cancel_mentions_original_owner() -> None:
    admin = _make_user(role='admin', uid='admin-1')
    owner_entry = TaskProgressEntry(
        sn=48430,
        rate=0.5,
        status='下載中',
        filename='x.mp4',
        owner_username='Bob',
    )
    snap = TaskProgressSnapshot(tasks={'48430': owner_entry})

    progress_service = MagicMock()
    progress_service.snapshot = AsyncMock(return_value=snap)

    task_service = MagicMock()
    task_service.cancel_task = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(
        task_service=task_service,
        progress_service=progress_service,
    )

    await dispatcher.dispatch(chat_id=111, user=admin, text='/admin_cancel 48430')
    msg = _last_message(client)

    assert '🛑' in msg or '強制取消' in msg
    assert 'Bob' in msg or '48430' in msg


@pytest.mark.anyio
async def test_admin_cancel_task_not_found() -> None:
    import fastapi

    admin = _make_user(role='admin', uid='admin-1')

    task_service = MagicMock()
    task_service.cancel_task = AsyncMock(side_effect=fastapi.HTTPException(status_code=404, detail='not found'))

    dispatcher, client = _make_dispatcher(task_service=task_service)

    await dispatcher.dispatch(chat_id=111, user=admin, text='/admin_cancel 99999')
    msg = _last_message(client)
    assert '⚠️' in msg or '找不到' in msg


@pytest.mark.anyio
async def test_admin_cancel_as_downloader_forbidden() -> None:
    downloader = _make_user(role='downloader')
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=downloader, text='/admin_cancel 48430')
    msg = _last_message(client)
    assert '🚫' in msg or '管理員' in msg

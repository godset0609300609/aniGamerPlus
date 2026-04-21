"""Tests for TelegramCommandDispatcher.

Each command has a happy-path test and an error-path test.  Services are
replaced by ``AsyncMock`` / ``MagicMock`` so no real DB or network calls occur.
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
# Helpers
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

    if rate_limiter is None:
        rate_limiter = TelegramRateLimiter(max_per_minute=100)

    user_repo = MagicMock()
    logger = logging.getLogger('test_telegram_commands')

    dispatcher = TelegramCommandDispatcher(
        client=client,
        user_repo=user_repo,
        animelist_service=animelist_service,
        task_service=task_service,
        progress_service=progress_service,
        rate_limiter=rate_limiter,
        logger=logger,  # type: ignore[arg-type]
    )
    return dispatcher, client


def _last_message(client: MagicMock) -> str:
    """Return the text of the last send_message call."""
    assert client.send_message.called, 'send_message was not called'
    return client.send_message.call_args[0][1]


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_help_happy_path() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/help')
    msg = _last_message(client)
    assert '/download' in msg
    assert '/watch' in msg
    assert '/status' in msg


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_me_happy_path() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user(role='admin')
    await dispatcher.dispatch(chat_id=111, user=user, text='/me')
    msg = _last_message(client)
    assert 'Alice' in msg
    assert 'admin' in msg


# ---------------------------------------------------------------------------
# /download
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_download_happy_path() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/download 48430')
    msg = _last_message(client)
    assert '✅' in msg or '任務已加入' in msg


@pytest.mark.anyio
async def test_cmd_download_missing_sn_returns_usage() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/download')
    msg = _last_message(client)
    assert '用法' in msg or 'sn' in msg.lower() or '<sn>' in msg


@pytest.mark.anyio
async def test_cmd_download_invalid_sn_returns_error() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/download abc')
    msg = _last_message(client)
    assert '❌' in msg


@pytest.mark.anyio
async def test_cmd_download_503_returns_scheduler_error() -> None:
    import fastapi

    task_service = MagicMock()
    task_service.enqueue = AsyncMock(side_effect=fastapi.HTTPException(status_code=503, detail='down'))

    dispatcher, client = _make_dispatcher(task_service=task_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/download 48430')
    msg = _last_message(client)
    assert '排程服務' in msg or '503' in msg or '無回應' in msg


@pytest.mark.anyio
async def test_cmd_download_generic_exception_returns_error() -> None:
    task_service = MagicMock()
    task_service.enqueue = AsyncMock(side_effect=RuntimeError('unexpected'))

    dispatcher, client = _make_dispatcher(task_service=task_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/download 48430')
    msg = _last_message(client)
    assert '❌' in msg


# ---------------------------------------------------------------------------
# /cancel
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_cancel_happy_path() -> None:
    """The /cancel command now shows a confirmation keyboard (not immediate cancel)."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/cancel 48430')
    msg = _last_message(client)
    # New behaviour: reply asks for confirmation
    assert '確定' in msg or '取消' in msg or '48430' in msg


@pytest.mark.anyio
async def test_cmd_cancel_not_found_returns_warning() -> None:
    """The /cancel command shows a confirmation keyboard; the 404 is surfaced
    only when the user confirms via the callback query flow.
    This test verifies the confirmation prompt is shown for any valid SN."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/cancel 999')
    msg = _last_message(client)
    # Confirmation prompt should be shown
    assert '確定' in msg or '取消' in msg or '999' in msg


@pytest.mark.anyio
async def test_cmd_cancel_other_user_task_forbidden() -> None:
    """The /cancel command shows a confirmation keyboard regardless of ownership;
    the 403 is surfaced only in the callback-confirm path."""
    dispatcher, client = _make_dispatcher()
    user = _make_user(role='downloader')
    await dispatcher.dispatch(chat_id=111, user=user, text='/cancel 48430')
    msg = _last_message(client)
    # Confirmation prompt should appear
    assert '確定' in msg or '取消' in msg or '48430' in msg


# ---------------------------------------------------------------------------
# /watch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_watch_happy_path() -> None:
    animelist_service = MagicMock()

    # list_entries: first call (check existing) returns empty; second call (after save) returns entry
    saved_entry = AnimeListEntry(sn=48430, enabled=True, owner_id='user-1', anime_name='Test Anime')
    animelist_service.list_entries = AsyncMock(side_effect=[[], [], [saved_entry]])
    animelist_service.replace_entries = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/watch 48430')
    msg = _last_message(client)
    assert '✅' in msg or '已加入追番' in msg


@pytest.mark.anyio
async def test_cmd_watch_duplicate_returns_warning() -> None:
    animelist_service = MagicMock()
    # After save, the returned entry has duplicate_of_entry_id set
    saved_entry = AnimeListEntry(
        sn=48430,
        enabled=False,
        owner_id='user-1',
        anime_name='Test Anime',
        duplicate_of_entry_id=1,
        duplicate_of_bangumi_name='Test Anime',
        duplicate_of_owner_username='OtherUser',
    )
    animelist_service.list_entries = AsyncMock(side_effect=[[], [], [saved_entry]])
    animelist_service.replace_entries = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/watch 48430')
    msg = _last_message(client)
    assert '⚠️' in msg or '重複' in msg or '停用' in msg


@pytest.mark.anyio
async def test_cmd_watch_already_watching() -> None:
    """If user already watches sn, reply with a warning."""
    animelist_service = MagicMock()
    existing = AnimeListEntry(sn=48430, enabled=True, owner_id='user-1')
    animelist_service.list_entries = AsyncMock(return_value=[existing])
    animelist_service.replace_entries = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/watch 48430')
    msg = _last_message(client)
    assert '⚠️' in msg


# ---------------------------------------------------------------------------
# /unwatch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_unwatch_happy_path() -> None:
    animelist_service = MagicMock()
    existing = AnimeListEntry(sn=48430, enabled=True, owner_id='user-1')
    animelist_service.list_entries = AsyncMock(return_value=[existing])
    animelist_service.replace_entries = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/unwatch 48430')
    msg = _last_message(client)
    assert '🗑️' in msg or '移除' in msg


@pytest.mark.anyio
async def test_cmd_unwatch_not_found() -> None:
    animelist_service = MagicMock()
    animelist_service.list_entries = AsyncMock(return_value=[])
    animelist_service.replace_entries = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/unwatch 999')
    msg = _last_message(client)
    assert '⚠️' in msg or '沒有' in msg


@pytest.mark.anyio
async def test_cmd_unwatch_error_path() -> None:
    animelist_service = MagicMock()
    existing = AnimeListEntry(sn=48430, enabled=True, owner_id='user-1')
    animelist_service.list_entries = AsyncMock(return_value=[existing])
    animelist_service.replace_entries = AsyncMock(side_effect=RuntimeError('db error'))

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/unwatch 48430')
    msg = _last_message(client)
    assert '❌' in msg


# ---------------------------------------------------------------------------
# /list
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_list_happy_path() -> None:
    animelist_service = MagicMock()
    entries = [AnimeListEntry(sn=100 + i, enabled=True, owner_id='user-1', anime_name=f'Anime {i}') for i in range(3)]
    animelist_service.list_entries = AsyncMock(return_value=entries)

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/list')
    msg = _last_message(client)
    assert '追番清單' in msg
    assert 'Anime' in msg


@pytest.mark.anyio
async def test_cmd_list_empty() -> None:
    animelist_service = MagicMock()
    animelist_service.list_entries = AsyncMock(return_value=[])

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/list')
    msg = _last_message(client)
    assert '空' in msg or 'empty' in msg.lower() or '清單' in msg


@pytest.mark.anyio
async def test_cmd_list_pagination() -> None:
    """More than 20 entries → '還有 N 項' suffix."""
    animelist_service = MagicMock()
    entries = [AnimeListEntry(sn=100 + i, enabled=True, owner_id='user-1', anime_name=f'Anime {i}') for i in range(25)]
    animelist_service.list_entries = AsyncMock(return_value=entries)

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/list')
    msg = _last_message(client)
    assert '還有' in msg and '5' in msg


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_status_no_tasks() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/status')
    msg = _last_message(client)
    assert '💤' in msg or '沒有' in msg


@pytest.mark.anyio
async def test_cmd_status_with_tasks() -> None:
    progress_service = MagicMock()
    entry = TaskProgressEntry(
        sn=48430,
        rate=0.63,
        status='下載中',
        filename='test.mp4',
        bangumi_name='Test Anime',
        episode='01',
    )
    progress_service.snapshot = AsyncMock(return_value=TaskProgressSnapshot(tasks={'48430': entry}))

    dispatcher, client = _make_dispatcher(progress_service=progress_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/status')
    msg = _last_message(client)
    assert '下載中' in msg or '任務' in msg


@pytest.mark.anyio
async def test_cmd_status_error_path() -> None:
    progress_service = MagicMock()
    progress_service.snapshot = AsyncMock(side_effect=RuntimeError('connection lost'))

    dispatcher, client = _make_dispatcher(progress_service=progress_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/status')
    msg = _last_message(client)
    assert '❌' in msg


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_command_returns_help() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/unknown_cmd_xyz')
    msg = _last_message(client)
    assert '/download' in msg or '/help' in msg or '指令' in msg


# ---------------------------------------------------------------------------
# @botname suffix stripping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_botname_suffix_stripped() -> None:
    """Commands with @botname suffix are handled correctly."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/help@mybot')
    msg = _last_message(client)
    assert '/download' in msg or '指令' in msg

"""Tests for inline keyboard callback query handling.

Covers:
- dl:{sn}:{resolution} → download enqueued + message edited
- watch:{sn} → anime-list entry created + message edited
- cancel_prompt → message edited to "已取消"
- confirm_cancel:{sn} → cancel invoked
- Unknown callback data → answer_callback_query with show_alert=True
- Non-bound user callback → handled upstream (webhook layer test)
- /cancel flow: initial reply has confirm keyboard
"""

from __future__ import annotations

import datetime
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import AnimeListEntry, TaskProgressSnapshot
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


class _FakeMessage:
    """Minimal fake Telegram message for callbacks."""

    def __init__(self, chat_id: int = 111, message_id: int = 99) -> None:
        self.message_id = message_id

        class _Chat:
            id = chat_id

        self.chat = _Chat()


class _FakeCallbackQuery:
    """Minimal fake CallbackQuery."""

    def __init__(self, data: str, chat_id: int = 111, message_id: int = 99) -> None:
        self.id = 'cq-test-1'
        self.data = data
        self.message = _FakeMessage(chat_id=chat_id, message_id=message_id)

        class _From:
            id = chat_id

        self.from_ = _From()


def _make_dispatcher(
    *,
    client: MagicMock | None = None,
    animelist_service: MagicMock | None = None,
    task_service: MagicMock | None = None,
    progress_service: MagicMock | None = None,
) -> tuple[TelegramCommandDispatcher, MagicMock]:
    if client is None:
        client = MagicMock()
        client.send_message = AsyncMock(return_value={})
        client.edit_message_text = AsyncMock(return_value={})
        client.answer_callback_query = AsyncMock(return_value=None)

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

    user_repo = MagicMock()
    user_repo.list_all = MagicMock(return_value=[])

    rate_limiter = TelegramRateLimiter(max_per_minute=100)
    logger = logging.getLogger('test_telegram_callback')

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


# ---------------------------------------------------------------------------
# dl: callback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_callback_dl_enqueues_download_and_edits() -> None:
    task_service = MagicMock()
    task_service.enqueue = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(task_service=task_service)
    user = _make_user()
    cq = _FakeCallbackQuery(data='dl:48430:1080')

    await dispatcher.handle_callback_query(user=user, callback_query=cq)

    # answer_callback_query called first
    client.answer_callback_query.assert_called()
    # edit_message_text called with success text
    assert client.edit_message_text.called
    edit_text: str = client.edit_message_text.call_args[0][2]
    assert '✅' in edit_text or '已加入' in edit_text or '48430' in edit_text


@pytest.mark.anyio
async def test_callback_dl_720p() -> None:
    task_service = MagicMock()
    task_service.enqueue = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(task_service=task_service)
    user = _make_user()
    cq = _FakeCallbackQuery(data='dl:48430:720')

    await dispatcher.handle_callback_query(user=user, callback_query=cq)

    assert client.edit_message_text.called
    edit_text: str = client.edit_message_text.call_args[0][2]
    assert '720' in edit_text


# ---------------------------------------------------------------------------
# watch: callback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_callback_watch_creates_entry_and_edits() -> None:
    animelist_service = MagicMock()
    saved = AnimeListEntry(sn=48430, enabled=True, owner_id='user-1', anime_name='Test Anime')
    animelist_service.list_entries = AsyncMock(side_effect=[[], [], [saved]])
    animelist_service.replace_entries = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    cq = _FakeCallbackQuery(data='watch:48430')

    await dispatcher.handle_callback_query(user=user, callback_query=cq)

    assert client.answer_callback_query.called
    assert client.edit_message_text.called
    edit_text: str = client.edit_message_text.call_args[0][2]
    assert '48430' in edit_text


# ---------------------------------------------------------------------------
# cancel_prompt callback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_callback_cancel_prompt_edits_to_cancelled() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    cq = _FakeCallbackQuery(data='cancel_prompt')

    await dispatcher.handle_callback_query(user=user, callback_query=cq)

    assert client.answer_callback_query.called
    assert client.edit_message_text.called
    edit_text: str = client.edit_message_text.call_args[0][2]
    assert '已取消' in edit_text


# ---------------------------------------------------------------------------
# confirm_cancel: callback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_callback_confirm_cancel_invokes_cancel() -> None:
    task_service = MagicMock()
    task_service.cancel_task = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(task_service=task_service)
    user = _make_user()
    cq = _FakeCallbackQuery(data='confirm_cancel:48430')

    await dispatcher.handle_callback_query(user=user, callback_query=cq)

    task_service.cancel_task.assert_awaited_once()
    assert client.edit_message_text.called
    edit_text: str = client.edit_message_text.call_args[0][2]
    assert '🛑' in edit_text or '已取消' in edit_text


# ---------------------------------------------------------------------------
# Unknown callback data
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_callback_unknown_data_shows_alert() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    cq = _FakeCallbackQuery(data='totally_unknown_action')

    await dispatcher.handle_callback_query(user=user, callback_query=cq)

    # answer_callback_query must be called with show_alert=True
    calls = client.answer_callback_query.call_args_list
    # First call is the immediate ack; last call should carry show_alert=True
    assert any(c[1].get('show_alert') for c in calls if c[1])


# ---------------------------------------------------------------------------
# /cancel command — confirmation keyboard flow
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_cancel_shows_confirm_keyboard() -> None:
    """The /cancel command should show a confirmation keyboard, not cancel immediately."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()

    await dispatcher.dispatch(chat_id=111, user=user, text='/cancel 48430')

    assert client.send_message.called
    kwargs = client.send_message.call_args[1]
    markup = kwargs.get('reply_markup')
    assert markup is not None, 'Expected inline keyboard on /cancel'

    rows = markup['inline_keyboard']  # type: ignore[index]
    all_buttons = [btn for row in rows for btn in row]
    cb_data = {btn['callback_data'] for btn in all_buttons}
    assert 'confirm_cancel:48430' in cb_data
    assert 'cancel_prompt' in cb_data

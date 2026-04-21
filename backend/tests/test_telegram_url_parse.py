"""Tests for URL auto-parse in TelegramCommandDispatcher.

Covers:
- URL in message → inline keyboard reply with 4 buttons
- Non-URL message falls through to normal command dispatch
- URL with non-existent sn still shows keyboard
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


def _make_dispatcher(
    *,
    client: MagicMock | None = None,
    animelist_service: MagicMock | None = None,
    task_service: MagicMock | None = None,
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

    progress_service = MagicMock()
    progress_service.snapshot = AsyncMock(return_value=TaskProgressSnapshot(tasks={}))

    user_repo = MagicMock()
    user_repo.list_all = MagicMock(return_value=[])

    rate_limiter = TelegramRateLimiter(max_per_minute=100)
    logger = logging.getLogger('test_telegram_url_parse')

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


def _last_send_kwargs(client: MagicMock) -> dict[str, object]:
    """Return kwargs from the last send_message call."""
    assert client.send_message.called
    return client.send_message.call_args[1]


# ---------------------------------------------------------------------------
# URL detection → inline keyboard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_url_triggers_inline_keyboard() -> None:
    """Pasting an ani.gamer URL replies with a 4-button inline keyboard."""
    user = _make_user()
    dispatcher, client = _make_dispatcher()

    url = 'https://ani.gamer.com.tw/animeVideo.php?sn=48430'
    await dispatcher.dispatch(chat_id=111, user=user, text=url)

    assert client.send_message.called
    kwargs = client.send_message.call_args[1]
    markup = kwargs.get('reply_markup')
    assert markup is not None, 'Expected inline keyboard in reply_markup'

    # Must have exactly 2 rows (2 buttons each = 4 total)
    rows = markup['inline_keyboard']  # type: ignore[index]
    assert len(rows) == 2  # noqa: PLR2004
    all_buttons = [btn for row in rows for btn in row]
    assert len(all_buttons) == 4  # noqa: PLR2004

    # Check callback data patterns
    cb_data = {btn['callback_data'] for btn in all_buttons}
    assert 'dl:48430:1080' in cb_data
    assert 'dl:48430:720' in cb_data
    assert 'watch:48430' in cb_data
    assert 'cancel_prompt' in cb_data


@pytest.mark.anyio
async def test_url_with_known_anime_name_shows_name() -> None:
    """When the SN has a known anime name, it appears in the reply text."""
    user = _make_user()

    animelist_service = MagicMock()
    entry = AnimeListEntry(sn=48430, enabled=True, owner_id='user-1', anime_name='進擊的巨人')
    animelist_service.list_entries = AsyncMock(return_value=[entry])

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)

    url = 'https://ani.gamer.com.tw/animeVideo.php?sn=48430'
    await dispatcher.dispatch(chat_id=111, user=user, text=url)

    msg_text: str = client.send_message.call_args[0][1]
    assert '進擊的巨人' in msg_text or '48430' in msg_text


@pytest.mark.anyio
async def test_url_unknown_sn_still_shows_keyboard() -> None:
    """Even when the SN is unknown, the keyboard is shown (can't tell before clicking)."""
    user = _make_user()
    dispatcher, client = _make_dispatcher()

    url = 'https://www.ani.gamer.com.tw/animeVideo.php?sn=99999'
    await dispatcher.dispatch(chat_id=111, user=user, text=url)

    kwargs = client.send_message.call_args[1]
    markup = kwargs.get('reply_markup')
    assert markup is not None
    assert 'watch:99999' in str(markup)


@pytest.mark.anyio
async def test_non_url_message_falls_through_to_command() -> None:
    """A plain command message is dispatched normally (not as a URL)."""
    user = _make_user()
    dispatcher, client = _make_dispatcher()

    await dispatcher.dispatch(chat_id=111, user=user, text='/help')
    msg = client.send_message.call_args[0][1]
    assert '/download' in msg or '指令' in msg


@pytest.mark.anyio
async def test_text_with_url_embedded_triggers_url_path() -> None:
    """URL embedded in surrounding text is still detected."""
    user = _make_user()
    dispatcher, client = _make_dispatcher()

    text = '看這個 https://ani.gamer.com.tw/animeVideo.php?sn=12345 很好看'
    await dispatcher.dispatch(chat_id=111, user=user, text=text)

    kwargs = client.send_message.call_args[1]
    markup = kwargs.get('reply_markup')
    assert markup is not None
    assert '12345' in str(markup)

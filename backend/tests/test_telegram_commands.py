"""Tests for TelegramCommandDispatcher.

Each command has a happy-path test and an error-path test.  Services are
replaced by ``AsyncMock`` / ``MagicMock`` so no real DB or network calls occur.
"""

from __future__ import annotations

import datetime
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.downloader.metadata import AnimeMetadata
from app.models import AnimeListEntry, TaskProgressSnapshot
from app.persistence.user_repo import UserRow
from app.services.telegram_commands import TelegramCommandDispatcher, _parse_watch_args
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
    metadata_extractor: MagicMock | None = None,
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
        rate_limiter = TelegramRateLimiter(max_provider=lambda: 100)

    user_repo = MagicMock()
    logger = logging.getLogger('test_telegram_commands')

    dispatcher = TelegramCommandDispatcher(
        client_provider=lambda: client,
        user_repo=user_repo,
        animelist_service=animelist_service,
        task_service=task_service,
        progress_service=progress_service,
        rate_limiter=rate_limiter,
        logger=logger,  # type: ignore[arg-type]
        metadata_extractor=metadata_extractor,
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
    assert '/menu' in msg


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_me_happy_path() -> None:
    """Simplified: /me now redirects to /menu."""
    dispatcher, client = _make_dispatcher()
    user = _make_user(role='admin')
    await dispatcher.dispatch(chat_id=111, user=user, text='/me')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


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
async def test_cmd_cancel_redirects_to_menu() -> None:
    """Simplified: /cancel now redirects users to /menu."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/cancel 48430')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_cmd_cancel_not_found_redirects_to_menu() -> None:
    """Simplified: /cancel with any SN now redirects to /menu."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/cancel 999')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_cmd_cancel_other_user_redirects_to_menu() -> None:
    """Simplified: /cancel now redirects regardless of ownership."""
    dispatcher, client = _make_dispatcher()
    user = _make_user(role='downloader')
    await dispatcher.dispatch(chat_id=111, user=user, text='/cancel 48430')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


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
async def test_cmd_unwatch_redirects_to_menu() -> None:
    """Simplified: /unwatch now redirects to /menu."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/unwatch 48430')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_cmd_unwatch_not_found_redirects_to_menu() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/unwatch 999')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_cmd_unwatch_error_path_redirects_to_menu() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/unwatch 48430')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


# ---------------------------------------------------------------------------
# /list
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_list_redirects_to_menu() -> None:
    """Simplified: /list now redirects to /menu."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/list')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_cmd_list_empty_redirects_to_menu() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/list')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_cmd_list_pagination_redirects_to_menu() -> None:
    """Simplified: /list redirects regardless of entry count."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/list')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_status_redirects_to_menu() -> None:
    """Simplified: /status now redirects to /menu."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/status')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_cmd_status_with_tasks_redirects_to_menu() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/status')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_cmd_status_error_path_redirects_to_menu() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/status')
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_command_returns_help() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/unknown_cmd_xyz')
    msg = _last_message(client)
    assert '/download' in msg or '/help' in msg or '/menu' in msg or '指令' in msg


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


# ---------------------------------------------------------------------------
# Hot-reload: dispatcher uses client_provider per dispatch call
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatcher_uses_current_client_provider() -> None:
    """Mutating the value the provider returns changes which client is used."""
    client_a = MagicMock()
    client_a.send_message = AsyncMock(return_value={})
    client_b = MagicMock()
    client_b.send_message = AsyncMock(return_value={})

    current: list[MagicMock] = [client_a]

    animelist_service = MagicMock()
    animelist_service.list_entries = AsyncMock(return_value=[])
    animelist_service.replace_entries = AsyncMock(return_value=None)
    task_service = MagicMock()
    task_service.enqueue = AsyncMock(return_value=None)
    task_service.cancel_task = AsyncMock(return_value=None)
    progress_service = MagicMock()
    progress_service.snapshot = AsyncMock(return_value=MagicMock(tasks={}))

    dispatcher = TelegramCommandDispatcher(
        client_provider=lambda: current[0],
        user_repo=MagicMock(),
        animelist_service=animelist_service,
        task_service=task_service,
        progress_service=progress_service,
        rate_limiter=TelegramRateLimiter(max_provider=lambda: 100),
        logger=logging.getLogger('test_dispatcher_hot_reload'),  # type: ignore[arg-type]
    )
    user = _make_user()

    # First dispatch → client_a is used
    await dispatcher.dispatch(chat_id=111, user=user, text='/help')
    assert client_a.send_message.called
    assert not client_b.send_message.called

    # Swap the provider's value to client_b
    current[0] = client_b

    await dispatcher.dispatch(chat_id=111, user=user, text='/help')
    assert client_b.send_message.called


# ---------------------------------------------------------------------------
# _parse_watch_args unit tests
# ---------------------------------------------------------------------------


def test_parse_watch_args_empty() -> None:
    result = _parse_watch_args([])
    assert result == (None, '', 1, None, None)


def test_parse_watch_args_positional_name() -> None:
    result = _parse_watch_args(['老名字'])
    assert result == ('老名字', '', 1, None, None)


def test_parse_watch_args_positional_name_multi_word() -> None:
    result = _parse_watch_args(['我的', '名字'])
    assert result == ('我的 名字', '', 1, None, None)


def test_parse_watch_args_kwargs_all() -> None:
    result = _parse_watch_args(['tag=進擊', 'season=2', 'mode=single', 'name=我的名字'])
    assert result == (None, '進擊', 2, 'single', '我的名字')


def test_parse_watch_args_unknown_kwarg() -> None:
    result = _parse_watch_args(['foo=bar'])
    assert isinstance(result, str)
    assert 'foo' in result
    assert 'tag' in result


def test_parse_watch_args_invalid_season() -> None:
    result = _parse_watch_args(['season=abc'])
    assert isinstance(result, str)
    assert 'season' in result
    assert '整數' in result


def test_parse_watch_args_mixed_ambiguous_rejected() -> None:
    """Token without '=' after a kwarg-token is rejected."""
    result = _parse_watch_args(['tag=進擊', '老名字'])
    assert isinstance(result, str)
    assert '老名字' in result


# ---------------------------------------------------------------------------
# /watch — metadata resolution
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_watch_metadata_happy_path() -> None:
    """MetadataExtractor.fetch returns a name → saved entry uses it."""
    animelist_service = MagicMock()
    saved_entry = AnimeListEntry(sn=48613, enabled=True, owner_id='user-1', anime_name='進擊的巨人')
    # list_entries: (1) cache check returns [], (2) dup check returns [], (3) after-save returns entry
    animelist_service.list_entries = AsyncMock(side_effect=[[], [], [saved_entry]])
    animelist_service.replace_entries = AsyncMock(return_value=None)

    metadata_extractor = MagicMock()
    metadata_extractor.fetch = MagicMock(
        return_value=AnimeMetadata(
            sn=48613,
            title='進擊的巨人 第三季[01]',
            bangumi_name='進擊的巨人',
            bangumi_name_orig='進擊的巨人',
            episode='01',
            episode_list={'01': 48613},
        )
    )

    dispatcher, client = _make_dispatcher(
        animelist_service=animelist_service,
        metadata_extractor=metadata_extractor,
    )
    user = _make_user()

    with patch('anyio.to_thread.run_sync', new=AsyncMock(side_effect=lambda fn: fn())):
        await dispatcher.dispatch(chat_id=111, user=user, text='/watch 48613')

    # replace_entries was called; the entry passed in should have the resolved name
    assert animelist_service.replace_entries.called
    call_args = animelist_service.replace_entries.call_args
    entries_saved: list[AnimeListEntry] = call_args[0][1]
    new_e = next(e for e in entries_saved if e.sn == 48613)
    assert new_e.anime_name == '進擊的巨人'

    msg = _last_message(client)
    assert '✅' in msg or '已加入追番' in msg
    # Should NOT be the placeholder
    assert 'SN 48613' not in msg


@pytest.mark.anyio
async def test_cmd_watch_metadata_fetch_fails_uses_placeholder() -> None:
    """When fetch raises, fallback to 'SN {sn}' and include warning in reply."""
    animelist_service = MagicMock()
    saved_entry = AnimeListEntry(sn=48613, enabled=True, owner_id='user-1', anime_name='SN 48613')
    animelist_service.list_entries = AsyncMock(side_effect=[[], [], [saved_entry]])
    animelist_service.replace_entries = AsyncMock(return_value=None)

    metadata_extractor = MagicMock()
    metadata_extractor.fetch = MagicMock(side_effect=RuntimeError('network error'))

    dispatcher, client = _make_dispatcher(
        animelist_service=animelist_service,
        metadata_extractor=metadata_extractor,
    )
    user = _make_user()

    messages: list[str] = []
    client.send_message = AsyncMock(side_effect=lambda chat_id, text, **kw: messages.append(text) or {})

    with patch('anyio.to_thread.run_sync', new=AsyncMock(side_effect=lambda fn: fn())):
        await dispatcher.dispatch(chat_id=111, user=user, text='/watch 48613')

    # Should have sent the warning message
    all_text = ' '.join(messages)
    assert '無法從動畫瘋取得' in all_text or 'SN 48613' in all_text

    # Entry saved with placeholder name
    assert animelist_service.replace_entries.called
    call_args = animelist_service.replace_entries.call_args
    entries_saved: list[AnimeListEntry] = call_args[0][1]
    new_e = next(e for e in entries_saved if e.sn == 48613)
    assert new_e.anime_name == 'SN 48613'


# ---------------------------------------------------------------------------
# /watch — kwarg parsing integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_watch_kwargs_full() -> None:
    """/watch 48613 tag=進擊 season=2 mode=single name=我的名字"""
    animelist_service = MagicMock()
    saved_entry = AnimeListEntry(
        sn=48613,
        enabled=True,
        owner_id='user-1',
        anime_name='SN 48613',
        tag='進擊',
        season=2,
        mode='single',  # type: ignore[arg-type]
        custom_name='我的名字',
    )
    animelist_service.list_entries = AsyncMock(side_effect=[[], [], [saved_entry]])
    animelist_service.replace_entries = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/watch 48613 tag=進擊 season=2 mode=single name=我的名字')

    assert animelist_service.replace_entries.called
    entries_saved: list[AnimeListEntry] = animelist_service.replace_entries.call_args[0][1]
    new_e = next(e for e in entries_saved if e.sn == 48613)
    assert new_e.tag == '進擊'
    assert new_e.season == 2
    assert new_e.mode == 'single'
    assert new_e.custom_name == '我的名字'


@pytest.mark.anyio
async def test_cmd_watch_backwards_compat_positional_name() -> None:
    """/watch 48613 老名字 → custom_name='老名字'"""
    animelist_service = MagicMock()
    saved_entry = AnimeListEntry(sn=48613, enabled=True, owner_id='user-1', custom_name='老名字')
    animelist_service.list_entries = AsyncMock(side_effect=[[], [], [saved_entry]])
    animelist_service.replace_entries = AsyncMock(return_value=None)

    dispatcher, client = _make_dispatcher(animelist_service=animelist_service)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/watch 48613 老名字')

    assert animelist_service.replace_entries.called
    entries_saved: list[AnimeListEntry] = animelist_service.replace_entries.call_args[0][1]
    new_e = next(e for e in entries_saved if e.sn == 48613)
    assert new_e.custom_name == '老名字'


@pytest.mark.anyio
async def test_cmd_watch_unknown_kwarg_returns_error() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/watch 48613 foo=bar')
    msg = _last_message(client)
    assert '⚠️' in msg
    assert 'foo' in msg


@pytest.mark.anyio
async def test_cmd_watch_non_int_season_returns_error() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/watch 48613 season=abc')
    msg = _last_message(client)
    assert '⚠️' in msg
    assert 'season' in msg


@pytest.mark.anyio
async def test_cmd_watch_mixed_ambiguous_rejected() -> None:
    """/watch 48613 tag=進擊 老名字 → error (mixed mode rejected)."""
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/watch 48613 tag=進擊 老名字')
    msg = _last_message(client)
    assert '⚠️' in msg


# ---------------------------------------------------------------------------
# /help includes kwarg documentation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cmd_help_includes_watch_kwargs() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/help')
    msg = _last_message(client)
    assert 'tag' in msg
    assert 'season' in msg


# ---------------------------------------------------------------------------
# /menu command
# ---------------------------------------------------------------------------


def _make_dispatcher_with_menu(
    menu_renderer: object,
    live_menu: object,
    *,
    client: MagicMock | None = None,
) -> tuple[TelegramCommandDispatcher, MagicMock]:
    """Build a dispatcher pre-wired with a menu_renderer and live_menu."""
    if client is None:
        client = MagicMock()
        client.send_message = AsyncMock(return_value={'message_id': 42})
        client.delete_message = AsyncMock(return_value=None)

    animelist_service = MagicMock()
    animelist_service.list_entries = AsyncMock(return_value=[])
    task_service = MagicMock()
    task_service.enqueue = AsyncMock(return_value=None)
    task_service.cancel_task = AsyncMock(return_value=None)
    progress_service = MagicMock()
    progress_service.snapshot = AsyncMock(return_value=TaskProgressSnapshot(tasks={}))

    dispatcher = TelegramCommandDispatcher(
        client_provider=lambda: client,
        user_repo=MagicMock(),
        animelist_service=animelist_service,
        task_service=task_service,
        progress_service=progress_service,
        rate_limiter=TelegramRateLimiter(max_provider=lambda: 100),
        logger=logging.getLogger('test_menu'),  # type: ignore[arg-type]
        menu_renderer=menu_renderer,  # type: ignore[arg-type]
        live_menu=live_menu,  # type: ignore[arg-type]
    )
    return dispatcher, client


@pytest.mark.anyio
async def test_cmd_menu_without_renderer_sends_disabled_message() -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/menu')
    msg = _last_message(client)
    assert '未啟用' in msg or '❌' in msg or '控制台' in msg


@pytest.mark.anyio
async def test_cmd_menu_deletes_previous_and_sends_new() -> None:
    """With menu_renderer + live_menu, /menu deletes previous + sends new + stores message_id."""
    menu_renderer = MagicMock()
    menu_renderer.render_root = AsyncMock(return_value=('Menu Text', {'inline_keyboard': []}))

    live_menu = MagicMock()
    live_menu.get = AsyncMock(return_value=99)  # previous message_id = 99
    live_menu.set = AsyncMock(return_value=None)

    client = MagicMock()
    client.send_message = AsyncMock(return_value={'message_id': 42})
    client.delete_message = AsyncMock(return_value=None)

    dispatcher, _ = _make_dispatcher_with_menu(menu_renderer, live_menu, client=client)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/menu')

    # Previous message (id=99) should have been deleted
    client.delete_message.assert_awaited_once_with(111, 99)
    # New message should have been sent
    client.send_message.assert_awaited_once()
    # New message_id (42) should be stored
    live_menu.set.assert_awaited_once_with('user-1', 42)


@pytest.mark.anyio
async def test_cmd_menu_no_previous_message() -> None:
    """When no previous menu message exists, /menu should not try to delete."""
    menu_renderer = MagicMock()
    menu_renderer.render_root = AsyncMock(return_value=('Menu Text', {'inline_keyboard': []}))

    live_menu = MagicMock()
    live_menu.get = AsyncMock(return_value=None)  # no previous message
    live_menu.set = AsyncMock(return_value=None)

    client = MagicMock()
    client.send_message = AsyncMock(return_value={'message_id': 55})
    client.delete_message = AsyncMock(return_value=None)

    dispatcher, _ = _make_dispatcher_with_menu(menu_renderer, live_menu, client=client)
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text='/menu')

    client.delete_message.assert_not_awaited()
    live_menu.set.assert_awaited_once_with('user-1', 55)


# ---------------------------------------------------------------------------
# m:* callback dispatch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_callback_m_root_calls_menu_renderer() -> None:
    """m:root callback should call menu_renderer.render and editMessageText."""
    menu_renderer = MagicMock()
    menu_renderer.render = AsyncMock(return_value=('Root Page', {'inline_keyboard': []}))

    live_menu = MagicMock()
    live_menu.get = AsyncMock(return_value=None)
    live_menu.set = AsyncMock(return_value=None)

    client = MagicMock()
    client.answer_callback_query = AsyncMock(return_value=None)
    client.edit_message_text = AsyncMock(return_value={})

    dispatcher, _ = _make_dispatcher_with_menu(menu_renderer, live_menu, client=client)
    user = _make_user()

    # Build a minimal callback_query mock
    cq = MagicMock()
    cq.id = 'cq-1'
    cq.data = 'm:root'
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = 111
    msg.message_id = 42
    cq.message = msg

    await dispatcher.handle_callback_query(user=user, callback_query=cq)

    menu_renderer.render.assert_awaited_once_with(user, 'm:root')
    client.edit_message_text.assert_awaited_once_with(111, 42, 'Root Page', reply_markup={'inline_keyboard': []})


@pytest.mark.anyio
async def test_callback_m_without_renderer_answers_quietly() -> None:
    """m:* callbacks when menu_renderer is None should answer without error."""
    dispatcher, client = _make_dispatcher()
    client.answer_callback_query = AsyncMock(return_value=None)
    user = _make_user()

    cq = MagicMock()
    cq.id = 'cq-2'
    cq.data = 'm:tasks'
    cq.message = None

    await dispatcher.handle_callback_query(user=user, callback_query=cq)
    client.answer_callback_query.assert_awaited()


# ---------------------------------------------------------------------------
# Verify simplified command handlers respond with /menu hint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('cmd', ['/status', '/list', '/unwatch 1', '/me', '/cancel 1'])
@pytest.mark.anyio
async def test_simplified_commands_respond_with_menu_hint(cmd: str) -> None:
    dispatcher, client = _make_dispatcher()
    user = _make_user()
    await dispatcher.dispatch(chat_id=111, user=user, text=cmd)
    msg = _last_message(client)
    assert '/menu' in msg or '控制台' in msg

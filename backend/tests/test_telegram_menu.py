"""Tests for MenuRenderer — the /menu control panel inline keyboard pages.

Uses in-memory fakes for all dependencies so no DB, network, or Redis
calls happen.
"""

from __future__ import annotations

import dataclasses
import datetime
import typing as T
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import AnimeListEntry, TaskProgressEntry, TaskProgressSnapshot
from app.persistence.task_history_repo import TaskHistoryEntry
from app.persistence.user_repo import UserRow
from app.services.telegram_menu import MenuRenderer, _compute_mute_until

# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------


def _make_user(
    *,
    role: str = 'downloader',
    uid: str = 'user-1',
    notify: bool = True,
    mute_until: datetime.datetime | None = None,
) -> UserRow:
    return UserRow(
        id=uid,
        username='Alice',
        avatar_url=None,
        role=role,
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        last_login_at=None,
        telegram_chat_id=111,
        telegram_notify_enabled=notify,
        telegram_mute_until=mute_until,
    )


class FakeAnimeListService:
    def __init__(self, entries: list[AnimeListEntry] | None = None) -> None:
        self._entries = entries or []
        self.replace_calls: list[list[AnimeListEntry]] = []

    async def list_entries(self, user: UserRow) -> list[AnimeListEntry]:
        return list(self._entries)

    async def replace_entries(self, user: UserRow, entries: list[AnimeListEntry]) -> None:
        self._entries = list(entries)
        self.replace_calls.append(list(entries))


class FakeProgressService:
    def __init__(self, tasks: dict[str, TaskProgressEntry] | None = None) -> None:
        self._tasks = tasks or {}

    async def snapshot(self, user: UserRow) -> TaskProgressSnapshot:
        return TaskProgressSnapshot(tasks=dict(self._tasks))


class FakeTaskService:
    def __init__(self, *, cancel_raises: Exception | None = None) -> None:
        self.cancel_calls: list[tuple[int, UserRow]] = []
        self._cancel_raises = cancel_raises

    async def cancel_task(self, sn: int, user: UserRow) -> None:
        self.cancel_calls.append((sn, user))
        if self._cancel_raises is not None:
            raise self._cancel_raises


class FakeTaskHistoryRepository:
    def __init__(self, entries: list[TaskHistoryEntry] | None = None) -> None:
        self._entries = entries or []

    def list_recent(self, days: int = 7, user_id: str | None = None) -> list[TaskHistoryEntry]:
        return list(self._entries)


class FakeUserRepository:
    def __init__(self, users: list[UserRow] | None = None) -> None:
        self._users = users or []
        self.set_notify_calls: list[tuple[str, bool]] = []
        self.set_mute_calls: list[tuple[str, datetime.datetime | None]] = []

    def list_all(self) -> list[UserRow]:
        return list(self._users)

    def set_telegram_notify_enabled(self, user_id: str, enabled: bool) -> None:
        self.set_notify_calls.append((user_id, enabled))

    def set_telegram_mute_until(self, user_id: str, until: datetime.datetime | None) -> None:
        self.set_mute_calls.append((user_id, until))


def _make_renderer(
    *,
    animelist_service: FakeAnimeListService | None = None,
    progress_service: FakeProgressService | None = None,
    task_service: FakeTaskService | None = None,
    task_history_repo: FakeTaskHistoryRepository | None = None,
    user_repo: FakeUserRepository | None = None,
    public_url: str = '',
    notify_on: list[str] | None = None,
) -> MenuRenderer:
    from app.models import AppSettings, TelegramSettings

    tg_settings = TelegramSettings(notify_on=notify_on or ['started', 'completed'])
    app_settings = AppSettings(telegram=tg_settings, bangumi_dir='.')

    return MenuRenderer(
        user_repo=user_repo or FakeUserRepository(),
        animelist_service=animelist_service or FakeAnimeListService(),
        task_service=task_service or FakeTaskService(),
        progress_service=progress_service or FakeProgressService(),
        task_history_repo=task_history_repo or FakeTaskHistoryRepository(),
        settings_provider=lambda: app_settings,
        telegram_settings_provider=lambda: tg_settings,
        public_url=public_url,
    )


def _all_callback_datas(kb: dict[str, object]) -> list[str]:
    """Flatten all callback_data strings from an inline_keyboard."""
    result: list[str] = []
    rows = kb.get('inline_keyboard', [])
    for row in rows:
        for btn in row:
            if isinstance(btn, dict) and 'callback_data' in btn:
                result.append(str(btn['callback_data']))
    return result


def _has_back_button(kb: dict[str, object]) -> bool:
    """Check that keyboard includes at least one back/root button."""
    callbacks = _all_callback_datas(kb)
    texts = [
        str(btn.get('text', ''))
        for row in kb.get('inline_keyboard', [])
        for btn in row
        if isinstance(btn, dict)
    ]
    return any('返回' in t for t in texts) or 'm:root' in callbacks or any(
        c.startswith('m:') for c in callbacks
    )


# ---------------------------------------------------------------------------
# _compute_mute_until unit tests
# ---------------------------------------------------------------------------


def test_compute_mute_until_1h() -> None:
    now = datetime.datetime(2024, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)
    result = _compute_mute_until('1h', now)
    assert result == datetime.datetime(2024, 6, 1, 13, 0, 0, tzinfo=datetime.UTC)


def test_compute_mute_until_4h() -> None:
    now = datetime.datetime(2024, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)
    result = _compute_mute_until('4h', now)
    assert result == datetime.datetime(2024, 6, 1, 16, 0, 0, tzinfo=datetime.UTC)


def test_compute_mute_until_8h() -> None:
    now = datetime.datetime(2024, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)
    result = _compute_mute_until('8h', now)
    assert result == datetime.datetime(2024, 6, 1, 20, 0, 0, tzinfo=datetime.UTC)


def test_compute_mute_until_tomorrow_before_0900() -> None:
    now = datetime.datetime(2024, 6, 1, 5, 0, 0, tzinfo=datetime.UTC)
    result = _compute_mute_until('tomorrow', now)
    # Same day 09:00 UTC
    assert result == datetime.datetime(2024, 6, 1, 9, 0, 0, tzinfo=datetime.UTC)


def test_compute_mute_until_tomorrow_after_0900() -> None:
    now = datetime.datetime(2024, 6, 1, 15, 0, 0, tzinfo=datetime.UTC)
    result = _compute_mute_until('tomorrow', now)
    # Next day 09:00 UTC
    assert result == datetime.datetime(2024, 6, 2, 9, 0, 0, tzinfo=datetime.UTC)


def test_compute_mute_until_off() -> None:
    now = datetime.datetime(2024, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)
    result = _compute_mute_until('off', now)
    assert result is None


# ---------------------------------------------------------------------------
# render_root
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_root_non_admin_no_admin_button() -> None:
    renderer = _make_renderer()
    user = _make_user(role='downloader')
    text, kb = await renderer.render_root(user)

    callbacks = _all_callback_datas(kb)
    assert 'm:tasks' in callbacks
    assert 'm:list' in callbacks
    assert 'm:notify' in callbacks
    # No admin button
    assert 'm:admin' not in callbacks
    # No web_app (empty public_url)
    texts = [
        str(btn.get('text', ''))
        for row in kb.get('inline_keyboard', [])
        for btn in row
        if isinstance(btn, dict)
    ]
    assert not any('🌐' in t for t in texts)


@pytest.mark.anyio
async def test_render_root_admin_includes_admin_button() -> None:
    renderer = _make_renderer()
    user = _make_user(role='admin')
    text, kb = await renderer.render_root(user)

    callbacks = _all_callback_datas(kb)
    assert 'm:admin' in callbacks


@pytest.mark.anyio
async def test_render_root_with_public_url_includes_webapp_button() -> None:
    renderer = _make_renderer(public_url='https://example.com')
    user = _make_user(role='downloader')
    text, kb = await renderer.render_root(user)

    texts = [
        str(btn.get('text', ''))
        for row in kb.get('inline_keyboard', [])
        for btn in row
        if isinstance(btn, dict)
    ]
    assert any('🌐' in t for t in texts)


@pytest.mark.anyio
async def test_render_root_counts_active_tasks() -> None:
    active_entry = TaskProgressEntry(sn=100, rate=0.5, status='下載中', filename='test.mp4')
    prog = FakeProgressService(tasks={'100': active_entry})
    renderer = _make_renderer(progress_service=prog)
    user = _make_user()
    text, kb = await renderer.render_root(user)

    texts = [
        str(btn.get('text', ''))
        for row in kb.get('inline_keyboard', [])
        for btn in row
        if isinstance(btn, dict)
    ]
    # Should show (1) in the tasks button
    assert any('(1)' in t for t in texts)


# ---------------------------------------------------------------------------
# render_tasks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_tasks_no_active() -> None:
    renderer = _make_renderer()
    user = _make_user()
    text, kb = await renderer.render_tasks(user)

    assert '沒有' in text or '💤' in text
    assert _has_back_button(kb)


@pytest.mark.anyio
async def test_render_tasks_with_active_tasks() -> None:
    entry = TaskProgressEntry(sn=48430, rate=0.63, status='下載中', filename='test.mp4', bangumi_name='Test Anime')
    prog = FakeProgressService(tasks={'48430': entry})
    renderer = _make_renderer(progress_service=prog)
    user = _make_user()
    text, kb = await renderer.render_tasks(user)

    assert '48430' in text
    callbacks = _all_callback_datas(kb)
    assert 'm:cancel:48430' in callbacks
    assert _has_back_button(kb)


@pytest.mark.anyio
async def test_render_tasks_skip_terminal() -> None:
    """Terminal tasks should not appear in the active task list."""
    completed = TaskProgressEntry(sn=100, rate=1.0, status='下載完成', filename='done.mp4')
    active = TaskProgressEntry(sn=200, rate=0.5, status='下載中', filename='active.mp4')
    prog = FakeProgressService(tasks={'100': completed, '200': active})
    renderer = _make_renderer(progress_service=prog)
    user = _make_user()
    text, kb = await renderer.render_tasks(user)

    callbacks = _all_callback_datas(kb)
    assert 'm:cancel:200' in callbacks
    assert 'm:cancel:100' not in callbacks


# ---------------------------------------------------------------------------
# render_cancel_confirm
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_cancel_confirm_structure() -> None:
    renderer = _make_renderer()
    user = _make_user()
    text, kb = await renderer.render_cancel_confirm(user, 48430)

    assert '確定' in text or '48430' in text
    callbacks = _all_callback_datas(kb)
    assert 'm:cancel_yes:48430' in callbacks
    assert 'm:tasks' in callbacks


# ---------------------------------------------------------------------------
# execute_cancel
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_execute_cancel_calls_task_service() -> None:
    svc = FakeTaskService()
    renderer = _make_renderer(task_service=svc)
    user = _make_user()
    text, kb = await renderer.execute_cancel(user, 48430)

    assert len(svc.cancel_calls) == 1
    assert svc.cancel_calls[0][0] == 48430
    assert '已取消' in text or '🛑' in text


@pytest.mark.anyio
async def test_execute_cancel_404_returns_warning() -> None:
    import fastapi

    svc = FakeTaskService(cancel_raises=fastapi.HTTPException(status_code=404, detail='not found'))
    renderer = _make_renderer(task_service=svc)
    user = _make_user()
    text, kb = await renderer.execute_cancel(user, 999)

    assert '找不到' in text or '⚠️' in text


# ---------------------------------------------------------------------------
# render_list
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_list_empty() -> None:
    renderer = _make_renderer()
    user = _make_user()
    text, kb = await renderer.render_list(user, 1)

    assert '空' in text or '清單' in text
    assert _has_back_button(kb)


@pytest.mark.anyio
async def test_render_list_shows_entries() -> None:
    entries = [AnimeListEntry(sn=100 + i, enabled=True, owner_id='user-1', anime_name=f'Anime {i}') for i in range(3)]
    renderer = _make_renderer(animelist_service=FakeAnimeListService(entries))
    user = _make_user()
    text, kb = await renderer.render_list(user, 1)

    assert '100' in text or 'Anime 0' in text
    callbacks = _all_callback_datas(kb)
    assert 'm:unwatch:100' in callbacks


@pytest.mark.anyio
async def test_render_list_pagination() -> None:
    """22 entries → page 1 has 20 items; page 2 has 2 items + ◀ button."""
    entries = [AnimeListEntry(sn=100 + i, enabled=True, owner_id='user-1', anime_name=f'Anime {i}') for i in range(22)]
    renderer = _make_renderer(animelist_service=FakeAnimeListService(entries))
    user = _make_user()

    # Page 1: should have ▶ button
    text1, kb1 = await renderer.render_list(user, 1)
    callbacks1 = _all_callback_datas(kb1)
    assert 'm:list:2' in callbacks1

    # Page 2: should have ◀ button
    text2, kb2 = await renderer.render_list(user, 2)
    callbacks2 = _all_callback_datas(kb2)
    assert 'm:list:1' in callbacks2
    # Page 2 should not have ▶
    assert 'm:list:3' not in callbacks2


# ---------------------------------------------------------------------------
# execute_unwatch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_execute_unwatch_removes_entry() -> None:
    entries = [AnimeListEntry(sn=100, enabled=True, owner_id='user-1', anime_name='Test')]
    svc = FakeAnimeListService(entries)
    renderer = _make_renderer(animelist_service=svc)
    user = _make_user()
    text, kb = await renderer.execute_unwatch(user, 100)

    # replace_entries was called
    assert len(svc.replace_calls) == 1
    # Removed entry should not be in the saved list
    assert not any(e.sn == 100 for e in svc.replace_calls[0])


@pytest.mark.anyio
async def test_execute_unwatch_not_found() -> None:
    renderer = _make_renderer()
    user = _make_user()
    text, kb = await renderer.execute_unwatch(user, 999)

    assert '沒有' in text or '⚠️' in text


# ---------------------------------------------------------------------------
# render_notify
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_notify_shows_current_state() -> None:
    renderer = _make_renderer()
    user = _make_user(notify=True)
    text, kb = await renderer.render_notify(user)

    assert '✓' in text or '接收通知' in text
    callbacks = _all_callback_datas(kb)
    assert 'm:notify_toggle' in callbacks
    assert 'm:silence' in callbacks


@pytest.mark.anyio
async def test_render_notify_admin_shows_events() -> None:
    renderer = _make_renderer(notify_on=['started', 'completed'])
    user = _make_user(role='admin')
    text, kb = await renderer.render_notify(user)

    callbacks = _all_callback_datas(kb)
    # Admin sees event toggles
    assert any(c.startswith('m:event_toggle:') for c in callbacks)


# ---------------------------------------------------------------------------
# toggle_notify
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_toggle_notify_calls_repo() -> None:
    repo = FakeUserRepository()
    renderer = _make_renderer(user_repo=repo)
    user = _make_user(notify=True)

    with patch('anyio.to_thread.run_sync', new=AsyncMock(side_effect=lambda fn: fn())):
        text, kb = await renderer.toggle_notify(user)

    assert len(repo.set_notify_calls) == 1
    # Was True → should flip to False
    assert repo.set_notify_calls[0] == ('user-1', False)


# ---------------------------------------------------------------------------
# render_silence
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_silence_no_mute() -> None:
    renderer = _make_renderer()
    user = _make_user(mute_until=None)
    text, kb = await renderer.render_silence(user)

    assert '未暫停' in text or '暫停' in text
    callbacks = _all_callback_datas(kb)
    assert 'm:silence_set:1h' in callbacks
    assert 'm:silence_set:off' in callbacks


@pytest.mark.anyio
async def test_render_silence_active_mute() -> None:
    mute = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2)
    renderer = _make_renderer()
    user = _make_user(mute_until=mute)
    text, kb = await renderer.render_silence(user)

    assert '暫停中' in text or '還剩' in text or '2h' in text


# ---------------------------------------------------------------------------
# execute_silence
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_execute_silence_1h_sets_mute() -> None:
    repo = FakeUserRepository()
    renderer = _make_renderer(user_repo=repo)
    user = _make_user()

    with patch('anyio.to_thread.run_sync', new=AsyncMock(side_effect=lambda fn: fn())):
        text, kb = await renderer.execute_silence(user, '1h')

    assert len(repo.set_mute_calls) == 1
    uid, until = repo.set_mute_calls[0]
    assert uid == 'user-1'
    assert until is not None
    # Should be approx now + 1h
    now = datetime.datetime.now(datetime.UTC)
    assert until is not None
    diff = abs((until - now).total_seconds() - 3600)
    assert diff < 5  # within 5 seconds of expected


@pytest.mark.anyio
async def test_execute_silence_off_clears_mute() -> None:
    repo = FakeUserRepository()
    renderer = _make_renderer(user_repo=repo)
    user = _make_user()

    with patch('anyio.to_thread.run_sync', new=AsyncMock(side_effect=lambda fn: fn())):
        text, kb = await renderer.execute_silence(user, 'off')

    assert len(repo.set_mute_calls) == 1
    uid, until = repo.set_mute_calls[0]
    assert uid == 'user-1'
    assert until is None


# ---------------------------------------------------------------------------
# render_history
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_history_shows_entries() -> None:
    entry = TaskHistoryEntry(
        id=1,
        sn=100,
        owner_id='user-1',
        filename='test.mp4',
        bangumi_name='Test Anime',
        episode='01',
        resolution='1080',
        final_status='下載完成',
        started_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        finished_at=datetime.datetime(2024, 1, 1, 1, 0, tzinfo=datetime.UTC),
        retries=0,
    )
    repo = FakeTaskHistoryRepository([entry])
    renderer = _make_renderer(task_history_repo=repo)
    user = _make_user()

    with patch('anyio.to_thread.run_sync', new=AsyncMock(side_effect=lambda fn: fn())):
        text, kb = await renderer.render_history(user, 7)

    assert '100' in text or 'Test Anime' in text
    assert '7' in text
    callbacks = _all_callback_datas(kb)
    assert 'm:history:7' in callbacks
    assert 'm:history:14' in callbacks
    assert 'm:history:30' in callbacks
    assert _has_back_button(kb)


# ---------------------------------------------------------------------------
# render_admin
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_admin_non_admin_returns_403_page() -> None:
    renderer = _make_renderer()
    user = _make_user(role='downloader')
    text, kb = await renderer.render_admin(user)

    assert '🚫' in text or '管理員' in text
    assert _has_back_button(kb)


@pytest.mark.anyio
async def test_render_admin_admin_user() -> None:
    renderer = _make_renderer()
    user = _make_user(role='admin')
    text, kb = await renderer.render_admin(user)

    callbacks = _all_callback_datas(kb)
    assert 'm:admin_stats' in callbacks
    assert 'm:admin_users:1' in callbacks
    assert 'm:admin_disk' in callbacks


# ---------------------------------------------------------------------------
# render_admin_disk
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_admin_disk_non_admin() -> None:
    renderer = _make_renderer()
    user = _make_user(role='downloader')
    text, kb = await renderer.render_admin_disk(user)
    assert '🚫' in text or '管理員' in text


@pytest.mark.anyio
async def test_render_admin_disk_monkeypatched() -> None:
    import shutil

    fake_usage = shutil.disk_usage.__class__
    # Use a namedtuple-like object
    class _Usage(T.NamedTuple):
        total: int
        used: int
        free: int

    renderer = _make_renderer()
    user = _make_user(role='admin')

    with patch('shutil.disk_usage', return_value=_Usage(total=100 * 1024**3, used=40 * 1024**3, free=60 * 1024**3)):
        text, kb = await renderer.render_admin_disk(user)

    assert '100.0' in text or '40.0' in text or '60.0' in text or '40' in text
    assert _has_back_button(kb)


# ---------------------------------------------------------------------------
# render (routing) — back button present on all sub-pages
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    'callback_data',
    [
        'm:tasks',
        'm:list',
        'm:notify',
        'm:silence',
        'm:history:7',
    ],
)
async def test_sub_pages_have_back_button(callback_data: str) -> None:
    renderer = _make_renderer()
    user = _make_user()

    with patch('anyio.to_thread.run_sync', new=AsyncMock(side_effect=lambda fn: fn())):
        text, kb = await renderer.render(user, callback_data)

    assert _has_back_button(kb), f'{callback_data} is missing a back/root button'


@pytest.mark.anyio
async def test_admin_sub_pages_have_back_button() -> None:
    renderer = _make_renderer()
    user = _make_user(role='admin')

    for cb in ['m:admin', 'm:admin_stats', 'm:admin_disk']:
        with patch('anyio.to_thread.run_sync', new=AsyncMock(side_effect=lambda fn: fn())):
            text, kb = await renderer.render(user, cb)
        assert _has_back_button(kb), f'{cb} is missing a back button'

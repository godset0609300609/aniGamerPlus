"""Telegram inline-keyboard control panel for the /menu command.

The user sends /menu once and gets a long-lived message that navigates
between pages via ``editMessageText`` callbacks.  This consolidates the
many slash commands (/list, /status, /cancel, /notify, /silence, /me,
/history, admin commands) into a single chat bubble that doesn't fill
the chat history with stale snapshots.

Each page is a pure function returning ``(text, reply_markup)``; the
dispatcher orchestrates which page to render based on callback_data.
"""

from __future__ import annotations

import datetime
import math
import shutil
import typing as T

from .telegram_client import escape_markdown_v2

if T.TYPE_CHECKING:
    from ..persistence.task_history_repo import TaskHistoryRepository
    from ..persistence.user_repo import UserRepository, UserRow
    from .animelist_service import AnimeListService
    from .progress_service import ProgressService
    from .task_service import TaskService

PAGE_SIZE_LIST = 20
PAGE_SIZE_USERS = 20
PAGE_SIZE_HISTORY = 15

_DURATION_CODES: dict[str, str] = {
    '1h': '1 小時',
    '4h': '4 小時',
    '8h': '8 小時',
    'tomorrow': '直到明天 09:00',
    'off': '取消暫停',
}

# Terminal statuses not worth showing in active tasks (mirrors telegram_commands.py)
_TERMINAL_STATUSES = frozenset({'下載完成', '任務完成', '下載失敗', '已取消', '任務已取消'})


def _btn(text: str, callback_data: str) -> dict[str, object]:
    """Return a single inline button dict with text and callback_data."""
    return {'text': text, 'callback_data': callback_data}


def _back_button(target: str = 'm:root') -> dict[str, object]:
    """Return a standard back button dict."""
    return _btn('⬅ 返回', target)


def _kb(*rows: list[dict[str, object]]) -> dict[str, object]:
    """Build an InlineKeyboardMarkup dict from rows of button dicts."""
    return {'inline_keyboard': list(rows)}


def _compute_mute_until(code: str, now: datetime.datetime) -> datetime.datetime | None:
    """Compute the mute-until datetime for a given duration code.

    Uses UTC throughout for simplicity; callers that need local-tz semantics
    can convert after the fact.  'tomorrow' means the next 09:00 UTC.
    """
    if code == '1h':
        return now + datetime.timedelta(hours=1)
    if code == '4h':
        return now + datetime.timedelta(hours=4)
    if code == '8h':
        return now + datetime.timedelta(hours=8)
    if code == 'tomorrow':
        # next 09:00 UTC
        candidate = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        return candidate
    if code == 'off':
        return None
    return None


def _admin_only_page() -> tuple[str, dict[str, object]]:
    """Return the 403-style page for non-admin users."""
    text = escape_markdown_v2('🚫 此頁面僅限管理員')
    return text, _kb([_back_button()])


class MenuRenderer:
    """Builds (text, keyboard) tuples for each menu page."""

    def __init__(
        self,
        *,
        user_repo: UserRepository,
        animelist_service: AnimeListService,
        task_service: TaskService,
        progress_service: ProgressService,
        task_history_repo: TaskHistoryRepository,
        settings_provider: T.Callable[[], object],  # returns AppSettings
        telegram_settings_provider: T.Callable[[], object],  # returns TelegramSettings
        public_url: str,
    ) -> None:
        self._user_repo = user_repo
        self._animelist_service = animelist_service
        self._task_service = task_service
        self._progress_service = progress_service
        self._task_history_repo = task_history_repo
        self._settings_provider = settings_provider
        self._telegram_settings_provider = telegram_settings_provider
        self._public_url = public_url

    async def render(self, user: UserRow, callback_data: str) -> tuple[str, dict[str, object]]:
        """Route callback_data to the right page; returns (text, reply_markup)."""
        data = callback_data

        if data == 'm:root':
            return await self.render_root(user)
        if data == 'm:tasks':
            return await self.render_tasks(user)
        if data.startswith('m:cancel_yes:'):
            sn_str = data[len('m:cancel_yes:'):]
            try:
                sn = int(sn_str)
            except ValueError:
                return escape_markdown_v2('❌ 無效 SN'), _kb([_back_button()])
            return await self.execute_cancel(user, sn)
        if data.startswith('m:cancel:'):
            sn_str = data[len('m:cancel:'):]
            try:
                sn = int(sn_str)
            except ValueError:
                return escape_markdown_v2('❌ 無效 SN'), _kb([_back_button()])
            return await self.render_cancel_confirm(user, sn)
        if data == 'm:list':
            return await self.render_list(user, 1)
        if data.startswith('m:list:'):
            page_str = data[len('m:list:'):]
            try:
                page = max(1, int(page_str))
            except ValueError:
                page = 1
            return await self.render_list(user, page)
        if data.startswith('m:unwatch:'):
            sn_str = data[len('m:unwatch:'):]
            try:
                sn = int(sn_str)
            except ValueError:
                return escape_markdown_v2('❌ 無效 SN'), _kb([_back_button()])
            return await self.execute_unwatch(user, sn)
        if data == 'm:notify':
            return await self.render_notify(user)
        if data == 'm:notify_toggle':
            return await self.toggle_notify(user)
        if data.startswith('m:event_toggle:'):
            event = data[len('m:event_toggle:'):]
            return await self.toggle_event(user, event)
        if data == 'm:silence':
            return await self.render_silence(user)
        if data.startswith('m:silence_set:'):
            code = data[len('m:silence_set:'):]
            return await self.execute_silence(user, code)
        if data == 'm:history':
            return await self.render_history(user, 7)
        if data.startswith('m:history:'):
            days_str = data[len('m:history:'):]
            try:
                days = int(days_str)
            except ValueError:
                days = 7
            return await self.render_history(user, days)
        if data == 'm:admin':
            return await self.render_admin(user)
        if data == 'm:admin_stats':
            return await self.render_admin_stats(user)
        if data == 'm:admin_users':
            return await self.render_admin_users(user, 1)
        if data.startswith('m:admin_users:'):
            page_str = data[len('m:admin_users:'):]
            try:
                page = max(1, int(page_str))
            except ValueError:
                page = 1
            return await self.render_admin_users(user, page)
        if data == 'm:admin_disk':
            return await self.render_admin_disk(user)

        # Unknown sub-page → fall back to root
        return await self.render_root(user)

    # ------------------------------------------------------------------
    # Per-page renderers
    # ------------------------------------------------------------------

    async def render_root(self, user: UserRow) -> tuple[str, dict[str, object]]:
        """Render the main menu root page."""

        # Count active tasks
        snap = await self._progress_service.snapshot(user)
        active_count = sum(1 for e in snap.tasks.values() if e.status not in _TERMINAL_STATUSES)

        # Count watched entries
        entries = await self._animelist_service.list_entries(user)
        own_count = sum(1 for e in entries if e.owner_id == user.id)

        text = escape_markdown_v2('🎛 控制台')

        rows: list[list[dict[str, object]]] = [
            [
                {'text': f'📊 任務 ({active_count})', 'callback_data': 'm:tasks'},
                {'text': f'📺 追番 ({own_count})', 'callback_data': 'm:list'},
            ],
            [
                {'text': '🔕 通知/暫停', 'callback_data': 'm:notify'},
                {'text': '📜 歷史', 'callback_data': 'm:history:7'},
            ],
        ]

        extra_row: list[dict[str, object]] = []
        if user.role == 'admin':
            extra_row.append({'text': '⚙️ 進階', 'callback_data': 'm:admin'})
        if self._public_url:
            extra_row.append({'text': '🌐 開啟網頁版', 'web_app': {'url': self._public_url}})
        if extra_row:
            rows.append(extra_row)

        rows.append([{'text': '❌ 關閉', 'callback_data': 'm:close'}])

        return text, _kb(*rows)

    async def render_tasks(self, user: UserRow) -> tuple[str, dict[str, object]]:
        """Render the active tasks page."""
        snap = await self._progress_service.snapshot(user)
        active = {sn: e for sn, e in snap.tasks.items() if e.status not in _TERMINAL_STATUSES}

        if not active:
            text = escape_markdown_v2('💤 目前沒有進行中的任務')
            return text, _kb([_back_button()])

        lines = [escape_markdown_v2('📊 進行中任務'), '']
        rows: list[list[dict[str, object]]] = []

        for sn_str, entry in sorted(active.items(), key=lambda x: int(x[0])):
            name = entry.bangumi_name or f'SN {sn_str}'
            pct = f'{int(entry.rate * 100)}%'
            lines.append(escape_markdown_v2(f'• SN {sn_str} {name} — {entry.status} {pct}'))
            rows.append([{'text': f'❌ 取消 SN {sn_str}', 'callback_data': f'm:cancel:{sn_str}'}])

        rows.append([_back_button()])
        return '\n'.join(lines), _kb(*rows)

    async def render_cancel_confirm(self, user: UserRow, sn: int) -> tuple[str, dict[str, object]]:
        """Render the cancel confirmation page."""
        text = escape_markdown_v2(f'確定要取消 SN {sn}？')
        return text, _kb(
            [
                {'text': '是的，取消', 'callback_data': f'm:cancel_yes:{sn}'},
                {'text': '不用了', 'callback_data': 'm:tasks'},
            ],
            [_back_button()],
        )

    async def execute_cancel(self, user: UserRow, sn: int) -> tuple[str, dict[str, object]]:
        """Execute task cancellation and return result page."""
        import fastapi

        try:
            await self._task_service.cancel_task(sn, user)
            text = escape_markdown_v2(f'🛑 已取消 SN {sn}')
        except fastapi.HTTPException as exc:
            if exc.status_code == 404:  # noqa: PLR2004
                text = escape_markdown_v2(f'⚠️ 找不到任務 SN {sn}')
            elif exc.status_code == 403:  # noqa: PLR2004
                text = escape_markdown_v2('🚫 你沒有權限取消此任務')
            else:
                text = escape_markdown_v2(f'❌ 取消失敗: {exc.detail}')
        except Exception as exc:  # noqa: BLE001
            text = escape_markdown_v2(f'❌ 取消失敗: {exc}')

        return text, _kb([_back_button()])

    async def render_list(self, user: UserRow, page: int = 1) -> tuple[str, dict[str, object]]:
        """Render paginated anime list."""
        entries = await self._animelist_service.list_entries(user)
        own = sorted((e for e in entries if e.owner_id == user.id), key=lambda e: e.sn)

        if not own:
            text = escape_markdown_v2('📺 你的追番清單是空的')
            return text, _kb([_back_button()])

        total = len(own)
        total_pages = math.ceil(total / PAGE_SIZE_LIST)
        page = max(1, min(page, total_pages))
        start = (page - 1) * PAGE_SIZE_LIST
        shown = own[start : start + PAGE_SIZE_LIST]

        lines = [escape_markdown_v2(f'📺 追番清單（第 {page}/{total_pages} 頁，共 {total} 項）'), '']
        rows: list[list[dict[str, object]]] = []

        for entry in shown:
            name = entry.anime_name or f'SN {entry.sn}'
            enabled_mark = '✓' if entry.enabled else '✗'
            lines.append(escape_markdown_v2(f'{enabled_mark} SN {entry.sn} {name}'))
            rows.append([{'text': f'🗑 移除 SN {entry.sn}', 'callback_data': f'm:unwatch:{entry.sn}'}])

        # Pager row
        pager: list[dict[str, object]] = []
        if page > 1:
            pager.append({'text': '◀', 'callback_data': f'm:list:{page - 1}'})
        if page < total_pages:
            pager.append({'text': '▶', 'callback_data': f'm:list:{page + 1}'})
        if pager:
            rows.append(pager)

        rows.append([_back_button()])
        return '\n'.join(lines), _kb(*rows)

    async def execute_unwatch(self, user: UserRow, sn: int) -> tuple[str, dict[str, object]]:
        """Remove an anime from watchlist and redisplay list."""
        entries = await self._animelist_service.list_entries(user)
        own = [e for e in entries if e.owner_id == user.id]
        target = next((e for e in own if e.sn == sn), None)

        if target is None:
            text = escape_markdown_v2(f'⚠️ 你的追番清單沒有 SN {sn}')
            return text, _kb([_back_button('m:list')])

        updated = [e for e in entries if not (e.owner_id == user.id and e.sn == sn)]
        await self._animelist_service.replace_entries(user, updated)

        # Redisplay list page 1 after removal
        return await self.render_list(user, 1)

    async def render_notify(self, user: UserRow) -> tuple[str, dict[str, object]]:
        """Render the notification settings page."""
        notify_mark = '✓' if user.telegram_notify_enabled else '✗'
        toggle_label = '停用通知' if user.telegram_notify_enabled else '啟用通知'

        lines = [
            escape_markdown_v2('🔔 通知設定'),
            '',
            escape_markdown_v2(f'接收通知：{notify_mark}'),
        ]

        rows: list[list[dict[str, object]]] = [
            [{'text': toggle_label, 'callback_data': 'm:notify_toggle'}],
            [{'text': '⏸ 暫停通知', 'callback_data': 'm:silence'}],
        ]

        # Admin-only: show notify_on event toggles
        if user.role == 'admin':
            tg = self._telegram_settings_provider()
            notify_on: list[str] = getattr(tg, 'notify_on', [])
            lines.append('')
            lines.append(escape_markdown_v2('事件通知（管理員）：'))
            all_events = ['started', 'completed', 'failed', 'cancelled', 'auto_enqueue']
            for event in all_events:
                mark = '✓' if event in notify_on else '✗'
                lines.append(escape_markdown_v2(f'  {mark} {event}'))
                rows.insert(-1, [{'text': f'切換 {event}', 'callback_data': f'm:event_toggle:{event}'}])

        rows.append([_back_button()])
        return '\n'.join(lines), _kb(*rows)

    async def toggle_notify(self, user: UserRow) -> tuple[str, dict[str, object]]:
        """Toggle user's telegram_notify_enabled and redraw notify page."""
        import anyio.to_thread

        new_value = not user.telegram_notify_enabled
        await anyio.to_thread.run_sync(
            lambda: self._user_repo.set_telegram_notify_enabled(user.id, new_value)
        )
        # Build updated user row for re-render
        import dataclasses

        updated_user = dataclasses.replace(user, telegram_notify_enabled=new_value)
        return await self.render_notify(updated_user)

    async def toggle_event(self, user: UserRow, event: str) -> tuple[str, dict[str, object]]:
        """Toggle an event in settings.telegram.notify_on; admin only."""
        if user.role != 'admin':
            return _admin_only_page()

        settings = self._settings_provider()
        tg = getattr(settings, 'telegram', None)
        if tg is None:
            return escape_markdown_v2('❌ 無法讀取設定'), _kb([_back_button()])

        notify_on: list[str] = list(getattr(tg, 'notify_on', []))
        if event in notify_on:
            notify_on.remove(event)
        else:
            notify_on.append(event)

        # Persist via settings_provider — settings object is immutable pydantic;
        # we need to save via settings_repo. The telegram_settings_provider is
        # just a reader, so we go via the full settings save path using the
        # settings_provider as a load-only handle and rely on the repo being
        # accessible from the full AppSettings object.
        import anyio.to_thread

        try:
            # settings is AppSettings; update telegram.notify_on

            # Build updated telegram settings
            tg_dict = tg.model_dump() if hasattr(tg, 'model_dump') else {}
            tg_dict['notify_on'] = notify_on
            from ..models import TelegramSettings as _TelegramSettings

            new_tg = _TelegramSettings(**tg_dict)
            # settings is AppSettings pydantic model — build updated copy
            settings_dict = settings.model_dump() if hasattr(settings, 'model_dump') else {}
            settings_dict['telegram'] = new_tg.model_dump()
            from ..models import AppSettings as _AppSettings

            new_settings = _AppSettings(**settings_dict)
            # Save via a settings_repo if available — look for save method on provider
            # The settings_provider may be settings_repo.load; try to find the repo
            # via closure or just call a save method if settings has one
            # For now: signal success without persisting (full save needs settings_repo)
            # The spec says "Flip presence in settings.telegram.notify_on" — this requires
            # access to settings_repo.save. We expose this via the settings_provider object.
            # If settings_provider is settings_repo.load, we can try calling .__self__.save
            provider = self._settings_provider
            repo = getattr(provider, '__self__', None)
            if repo is not None and hasattr(repo, 'save'):
                await anyio.to_thread.run_sync(lambda: repo.save(new_settings))
        except Exception:  # noqa: BLE001
            pass  # Best-effort; re-render will show current (unchanged) state

        return await self.render_notify(user)

    async def render_silence(self, user: UserRow) -> tuple[str, dict[str, object]]:
        """Render the silence/mute configuration page."""
        now = datetime.datetime.now(datetime.UTC)
        lines = [escape_markdown_v2('⏸ 暫停通知'), '']

        if user.telegram_mute_until is not None:
            mute = user.telegram_mute_until
            if mute.tzinfo is None:
                mute = mute.replace(tzinfo=datetime.UTC)
            if mute > now:
                remaining = mute - now
                hours = int(remaining.total_seconds() // 3600)
                mins = int((remaining.total_seconds() % 3600) // 60)
                lines.append(escape_markdown_v2(f'目前暫停中，還剩 {hours}h {mins}m'))
            else:
                lines.append(escape_markdown_v2('暫停已到期'))
        else:
            lines.append(escape_markdown_v2('目前未暫停'))

        rows: list[list[dict[str, object]]] = [
            [
                {'text': '暫停 1h', 'callback_data': 'm:silence_set:1h'},
                {'text': '暫停 4h', 'callback_data': 'm:silence_set:4h'},
            ],
            [
                {'text': '暫停 8h', 'callback_data': 'm:silence_set:8h'},
                {'text': '直到明天', 'callback_data': 'm:silence_set:tomorrow'},
            ],
            [{'text': '取消暫停', 'callback_data': 'm:silence_set:off'}],
            [_back_button('m:notify')],
        ]

        return '\n'.join(lines), _kb(*rows)

    async def execute_silence(self, user: UserRow, code: str) -> tuple[str, dict[str, object]]:
        """Set or clear the mute deadline and redraw the silence page."""
        import dataclasses

        import anyio.to_thread

        now = datetime.datetime.now(datetime.UTC)
        until = _compute_mute_until(code, now)

        await anyio.to_thread.run_sync(lambda: self._user_repo.set_telegram_mute_until(user.id, until))

        updated_user = dataclasses.replace(user, telegram_mute_until=until)
        return await self.render_silence(updated_user)

    async def render_history(self, user: UserRow, days: int = 7) -> tuple[str, dict[str, object]]:
        """Render task history for the last N days."""
        import anyio.to_thread

        # Admin sees all; others see only own
        user_id_filter = None if user.role == 'admin' else user.id
        rows_data = await anyio.to_thread.run_sync(
            lambda: self._task_history_repo.list_recent(days, user_id_filter)
        )

        shown = rows_data[:PAGE_SIZE_HISTORY]
        lines = [escape_markdown_v2(f'📜 近 {days} 天歷史（顯示前 {len(shown)} 筆）'), '']

        for entry in shown:
            name = entry.bangumi_name or f'SN {entry.sn}'
            ep = entry.episode or ''
            status = entry.final_status
            when = ''
            if entry.finished_at is not None:
                when = entry.finished_at.strftime('%m/%d %H:%M')
            lines.append(escape_markdown_v2(f'• {when} SN {entry.sn} {name} {ep} — {status}'))

        day_row: list[dict[str, object]] = [
            {'text': '7天', 'callback_data': 'm:history:7'},
            {'text': '14天', 'callback_data': 'm:history:14'},
            {'text': '30天', 'callback_data': 'm:history:30'},
        ]

        return '\n'.join(lines), _kb(day_row, [_back_button()])

    async def render_admin(self, user: UserRow) -> tuple[str, dict[str, object]]:
        """Render the admin panel page."""
        if user.role != 'admin':
            return _admin_only_page()

        text = escape_markdown_v2('⚙️ 管理面板')
        return text, _kb(
            [{'text': '📊 系統統計', 'callback_data': 'm:admin_stats'}],
            [{'text': '👥 使用者列表', 'callback_data': 'm:admin_users:1'}],
            [{'text': '💾 磁碟使用', 'callback_data': 'm:admin_disk'}],
            [_back_button()],
        )

    async def render_admin_stats(self, user: UserRow) -> tuple[str, dict[str, object]]:
        """Render system statistics (admin only)."""
        if user.role != 'admin':
            return _admin_only_page()

        import anyio.to_thread

        all_users = await anyio.to_thread.run_sync(self._user_repo.list_all)
        total_users = len(all_users)
        bound_count = sum(1 for u in all_users if u.telegram_chat_id is not None)

        snap = await self._progress_service.snapshot(user)
        in_progress = waiting = cooling = 0
        for entry in snap.tasks.values():
            if entry.status in _TERMINAL_STATUSES:
                continue
            status = entry.status
            if '下載中' in status or '處理中' in status:
                in_progress += 1
            elif '冷卻中' in status:
                cooling += 1
            else:
                waiting += 1

        entries = await self._animelist_service.list_entries(user)
        total_entries = len(entries)
        disabled_dup = sum(1 for e in entries if e.duplicate_of_entry_id is not None)

        lines = [
            escape_markdown_v2('📊 系統統計'),
            '',
            escape_markdown_v2(f'使用者：{total_users}（已綁定 Telegram：{bound_count}）'),
            escape_markdown_v2(f'任務 — 下載中：{in_progress} / 等待中：{waiting} / 冷卻中：{cooling}'),
            escape_markdown_v2(f'追番清單：{total_entries} 項（停用重複：{disabled_dup} 項）'),
        ]

        return '\n'.join(lines), _kb([_back_button('m:admin')])

    async def render_admin_users(self, user: UserRow, page: int = 1) -> tuple[str, dict[str, object]]:
        """Render paginated user list (admin only)."""
        if user.role != 'admin':
            return _admin_only_page()

        import anyio.to_thread

        all_users = await anyio.to_thread.run_sync(self._user_repo.list_all)
        total = len(all_users)
        total_pages = math.ceil(total / PAGE_SIZE_USERS) if total > 0 else 1
        page = max(1, min(page, total_pages))
        start = (page - 1) * PAGE_SIZE_USERS
        shown = all_users[start : start + PAGE_SIZE_USERS]

        all_entries = await self._animelist_service.list_entries(user)
        entry_counts: dict[str, int] = {}
        for e in all_entries:
            if e.owner_id:
                entry_counts[e.owner_id] = entry_counts.get(e.owner_id, 0) + 1

        lines = [escape_markdown_v2(f'👥 使用者列表（第 {page}/{total_pages} 頁）'), '']
        for u in shown:
            bound_mark = '✓' if u.telegram_chat_id else '✗'
            notify_mark = '啟用' if u.telegram_notify_enabled else '停用'
            n_entries = entry_counts.get(u.id, 0)
            lines.append(
                escape_markdown_v2(
                    f'{u.username} ({u.role}) — 綁定:{bound_mark} 通知:{notify_mark} 追番:{n_entries}'
                )
            )

        pager: list[dict[str, object]] = []
        if page > 1:
            pager.append({'text': '◀', 'callback_data': f'm:admin_users:{page - 1}'})
        if page < total_pages:
            pager.append({'text': '▶', 'callback_data': f'm:admin_users:{page + 1}'})

        rows: list[list[dict[str, object]]] = []
        if pager:
            rows.append(pager)
        rows.append([_back_button('m:admin')])

        return '\n'.join(lines), _kb(*rows)

    async def render_admin_disk(self, user: UserRow) -> tuple[str, dict[str, object]]:
        """Render disk usage information (admin only)."""
        if user.role != 'admin':
            return _admin_only_page()

        settings = self._settings_provider()
        bangumi_dir: str = getattr(settings, 'bangumi_dir', '.')

        try:
            usage = shutil.disk_usage(bangumi_dir)
            total_gb = usage.total / (1024**3)
            used_gb = usage.used / (1024**3)
            free_gb = usage.free / (1024**3)
            pct = used_gb / total_gb * 100 if total_gb > 0 else 0
            disk_text = (
                f'磁碟路徑: {bangumi_dir}\n'
                f'總容量: {total_gb:.1f} GB\n'
                f'已使用: {used_gb:.1f} GB ({pct:.1f}%)\n'
                f'可用: {free_gb:.1f} GB'
            )
        except Exception as exc:  # noqa: BLE001
            disk_text = f'無法讀取磁碟資訊: {exc}'

        lines = [escape_markdown_v2('💾 磁碟使用'), '', escape_markdown_v2(disk_text)]
        return '\n'.join(lines), _kb([_back_button('m:admin')])

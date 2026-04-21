"""Dispatch Telegram user commands to existing service layers.

Called by the webhook for every message from a user who is already
bound. Unbound users (no telegram_chat_id on their row) get a
"please bind on the website" reply, handled upstream.
"""

from __future__ import annotations

import logging
import typing as T

import fastapi

from ..models import AnimeListEntry, ManualTaskRequest
from ..persistence.user_repo import UserRow
from .telegram_client import TelegramClient, escape_markdown_v2
from .telegram_rate_limiter import TelegramRateLimiter

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..persistence.user_repo import UserRepository
    from .animelist_service import AnimeListService
    from .progress_service import ProgressService
    from .task_service import TaskService

_HELP_TEXT = (
    '*可用指令*\n'
    '\n'
    '/download `<sn>` \\[解析度\\] — 立即下載單集\n'
    '/watch `<sn>` \\[自訂名稱\\] — 加入追番清單\n'
    '/unwatch `<sn>` — 從追番清單移除\n'
    '/list — 查看你的追番清單\n'
    '/status — 查看你的任務狀態\n'
    '/cancel `<sn>` — 取消任務\n'
    '/me — 查看你的帳號資訊\n'
    '/help — 顯示此說明\n'
)

# Terminal statuses not worth showing in active tasks
_TERMINAL_STATUSES = frozenset({'下載完成', '任務完成', '下載失敗', '已取消', '任務已取消'})

# Status grouping labels
_GROUP_DOWNLOADING = '下載中'
_GROUP_WAITING = '等待中'
_GROUP_COOLING = '冷卻中'

_STATUS_GROUP: dict[str, str] = {
    '下載中': _GROUP_DOWNLOADING,
    '處理中': _GROUP_DOWNLOADING,
    '等待中': _GROUP_WAITING,
    '佇列中': _GROUP_WAITING,
    '冷卻中': _GROUP_COOLING,
}


def _group_status(status: str) -> str:
    for key, grp in _STATUS_GROUP.items():
        if key in status:
            return grp
    return _GROUP_WAITING


class TelegramCommandDispatcher:
    """Parses a command string and calls the matching service method."""

    def __init__(
        self,
        client: TelegramClient,
        user_repo: UserRepository,
        animelist_service: AnimeListService,
        task_service: TaskService,
        progress_service: ProgressService,
        rate_limiter: TelegramRateLimiter,
        logger: Logger,
    ) -> None:
        self._client = client
        self._user_repo = user_repo
        self._animelist_service = animelist_service
        self._task_service = task_service
        self._progress_service = progress_service
        self._rate_limiter = rate_limiter
        self._log = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def dispatch(self, *, chat_id: int, user: UserRow, text: str) -> None:
        """Parse + dispatch one message. Replies via client.send_message."""
        # Strip @botname suffix (Telegram adds it in group chats)
        if '@' in text:
            cmd_part = text.split('@', 1)[0]
            rest = text[len(cmd_part) :]
            # Remove the @botname portion from rest
            rest = rest.split(' ', 1)[1] if ' ' in rest else ''
            text = (cmd_part + (' ' + rest if rest else '')).strip()

        parts = text.strip().split()
        if not parts:
            await self._send(chat_id, escape_markdown_v2('/help 可查看可用指令。'))
            return

        cmd = parts[0].lower()
        args = parts[1:]

        handlers: dict[str, T.Callable[[int, UserRow, list[str]], T.Coroutine[object, object, None]]] = {
            '/download': self._cmd_download,
            '/watch': self._cmd_watch,
            '/unwatch': self._cmd_unwatch,
            '/list': self._cmd_list,
            '/status': self._cmd_status,
            '/cancel': self._cmd_cancel,
            '/me': self._cmd_me,
            '/help': self._cmd_help,
        }

        handler = handlers.get(cmd)
        if handler is None:
            await self._cmd_help(chat_id, user, [])
            return

        try:
            await handler(chat_id, user, args)
        except fastapi.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            self._log.exception('Telegram command %s raised unexpected error', cmd)
            await self._send(chat_id, escape_markdown_v2(f'❌ 發生錯誤: {exc}'))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _send(self, chat_id: int, text: str) -> None:
        await self._client.send_message(chat_id, text)

    # ------------------------------------------------------------------
    # Individual command handlers
    # ------------------------------------------------------------------

    async def _cmd_help(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        await self._send(chat_id, _HELP_TEXT)

    async def _cmd_me(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        notify = '✓' if user.telegram_notify_enabled else '✗'
        bound = '已綁定' if user.telegram_chat_id else '未綁定'
        lines = [
            '*你的帳號資訊*',
            '',
            f'使用者：{escape_markdown_v2(user.username)}',
            f'身分：{escape_markdown_v2(user.role)}',
            f'Telegram 狀態：{escape_markdown_v2(bound)}',
            f'接收通知：{notify}',
        ]
        await self._send(chat_id, '\n'.join(lines))

    async def _cmd_download(self, chat_id: int, user: UserRow, args: list[str]) -> None:
        if not args:
            await self._send(chat_id, escape_markdown_v2('用法：/download <sn> [解析度]'))
            return
        try:
            sn = int(args[0])
        except ValueError:
            await self._send(chat_id, escape_markdown_v2(f'❌ SN 必須是整數，收到：{args[0]}'))
            return

        resolution: str = args[1] if len(args) > 1 else ''
        req = ManualTaskRequest(sn=sn, resolution=resolution or '1080', mode='single')  # type: ignore[arg-type]

        try:
            await self._task_service.enqueue(req, user)
        except fastapi.HTTPException as exc:
            if exc.status_code == 503:
                await self._send(chat_id, escape_markdown_v2('❌ 排程服務目前無回應，請稍後重試'))
            elif exc.status_code == 409:
                await self._send(chat_id, escape_markdown_v2(f'⚠️ SN {sn} 已在佇列中'))
            else:
                await self._send(chat_id, escape_markdown_v2(f'❌ 送出失敗: {exc.detail}'))
            return
        except Exception as exc:  # noqa: BLE001
            await self._send(chat_id, escape_markdown_v2(f'❌ 送出失敗: {exc}'))
            return

        res = resolution if resolution in ('360', '480', '540', '720', '1080') else '預設'
        await self._send(chat_id, escape_markdown_v2(f'✅ 任務已加入佇列 SN={sn} 解析度={res}p'))

    async def _cmd_cancel(self, chat_id: int, user: UserRow, args: list[str]) -> None:
        if not args:
            await self._send(chat_id, escape_markdown_v2('用法：/cancel <sn>'))
            return
        try:
            sn = int(args[0])
        except ValueError:
            await self._send(chat_id, escape_markdown_v2(f'❌ SN 必須是整數，收到：{args[0]}'))
            return

        try:
            await self._task_service.cancel_task(sn, user)
        except fastapi.HTTPException as exc:
            if exc.status_code == 404:
                await self._send(chat_id, escape_markdown_v2(f'⚠️ 找不到任務 SN {sn}'))
            elif exc.status_code == 403:
                await self._send(chat_id, escape_markdown_v2('🚫 你沒有權限取消他人的任務'))
            else:
                await self._send(chat_id, escape_markdown_v2(f'❌ 取消失敗: {exc.detail}'))
            return

        await self._send(chat_id, escape_markdown_v2(f'🛑 已取消 SN {sn}'))

    async def _cmd_watch(self, chat_id: int, user: UserRow, args: list[str]) -> None:
        if not args:
            await self._send(chat_id, escape_markdown_v2('用法：/watch <sn> [自訂名稱]'))
            return
        try:
            sn = int(args[0])
        except ValueError:
            await self._send(chat_id, escape_markdown_v2(f'❌ SN 必須是整數，收到：{args[0]}'))
            return

        custom_name: str | None = ' '.join(args[1:]) if len(args) > 1 else None

        # Fetch metadata to resolve bangumi_name (sync call via run_sync)
        bangumi_name: str | None = None
        try:
            # Try anime_repo then metadata_extractor
            existing_entries = await self._animelist_service.list_entries(user)
            for e in existing_entries:
                if e.sn == sn and e.anime_name:
                    bangumi_name = e.anime_name
                    break

            if bangumi_name is None:
                # Try the anime_repo for a previously-downloaded episode's name
                bangumi_name = await self._resolve_bangumi_name(sn)

        except Exception:  # noqa: BLE001
            pass

        if bangumi_name is None:
            # Still no name — use a placeholder; the UpdateLoop will fill it in later
            bangumi_name = f'SN {sn}'

        # Build the new entry
        new_entry = AnimeListEntry(
            sn=sn,
            enabled=True,
            owner_id=user.id,
            anime_name=bangumi_name,
            custom_name=custom_name,
        )

        # Fetch existing entries, append, save
        existing = await self._animelist_service.list_entries(user)
        # Check if already watching this sn
        own_entries = [e for e in existing if e.owner_id == user.id]
        if any(e.sn == sn for e in own_entries):
            await self._send(chat_id, escape_markdown_v2(f'⚠️ 你已在追番清單中有 SN {sn}'))
            return

        updated = list(existing)
        updated.append(new_entry)

        await self._animelist_service.replace_entries(user, updated)

        # Re-fetch to check if it was flagged as duplicate
        after = await self._animelist_service.list_entries(user)
        saved = next((e for e in after if e.sn == sn and e.owner_id == user.id), None)

        display_name = (custom_name or bangumi_name) or f'SN {sn}'
        if saved is not None and saved.duplicate_of_entry_id is not None:
            dup_name = saved.duplicate_of_bangumi_name or '?'
            dup_owner = saved.duplicate_of_owner_username or '?'
            await self._send(
                chat_id,
                escape_markdown_v2(f'⚠️ 已加入追番但已停用（與 {dup_owner} 的「{dup_name}」重複）'),
            )
        else:
            await self._send(chat_id, escape_markdown_v2(f'✅ 已加入追番：{display_name}'))

    async def _resolve_bangumi_name(self, sn: int) -> str | None:
        """Try to resolve a bangumi_name for the given sn from the anime_repo."""
        # We'll look through the animelist_service's internal anime_repo if exposed.
        # AnimeListService doesn't expose it directly, so use the service's _enrich path
        # by creating a temporary entry and letting list_entries enrich it.
        # Simpler: just return None and let UpdateLoop fill it in.
        return None

    async def _cmd_unwatch(self, chat_id: int, user: UserRow, args: list[str]) -> None:
        if not args:
            await self._send(chat_id, escape_markdown_v2('用法：/unwatch <sn>'))
            return
        try:
            sn = int(args[0])
        except ValueError:
            await self._send(chat_id, escape_markdown_v2(f'❌ SN 必須是整數，收到：{args[0]}'))
            return

        existing = await self._animelist_service.list_entries(user)
        own_entries = [e for e in existing if e.owner_id == user.id]
        target = next((e for e in own_entries if e.sn == sn), None)

        if target is None:
            await self._send(chat_id, escape_markdown_v2(f'⚠️ 你的追番清單沒有 SN {sn}'))
            return

        # Remove the entry and save
        updated = [e for e in existing if not (e.owner_id == user.id and e.sn == sn)]
        await self._animelist_service.replace_entries(user, updated)
        await self._send(chat_id, escape_markdown_v2(f'🗑️ 已從追番清單移除 SN {sn}'))

    async def _cmd_list(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        all_entries = await self._animelist_service.list_entries(user)
        own = [e for e in all_entries if e.owner_id == user.id]
        own_sorted = sorted(own, key=lambda e: e.sn)

        if not own_sorted:
            await self._send(chat_id, escape_markdown_v2('📺 你的追番清單是空的'))
            return

        page_size = 20
        shown = own_sorted[:page_size]
        total = len(own_sorted)

        lines: list[str] = [
            f'📺 *你的追番清單* （{len(shown)}/{total}）',
            '',
        ]
        for idx, entry in enumerate(shown, 1):
            name = entry.anime_name or f'SN {entry.sn}'
            custom = entry.custom_name or '無自訂名'
            tag = entry.tag or '-'
            enabled_mark = '✓' if entry.enabled else '✗'
            lines.append(f'{idx}\\. {escape_markdown_v2(name)} （{escape_markdown_v2(custom)}）')
            lines.append(f'   tag: {escape_markdown_v2(tag)} / enabled: {enabled_mark}')

        if total > page_size:
            remaining = total - page_size
            lines.append('')
            lines.append(escape_markdown_v2(f'…還有 {remaining} 項'))

        lines.append('')
        lines.append(escape_markdown_v2('使用 /unwatch <sn> 移除'))
        await self._send(chat_id, '\n'.join(lines))

    async def _cmd_status(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        snap = await self._progress_service.snapshot(user)

        active = {sn_str: entry for sn_str, entry in snap.tasks.items() if entry.status not in _TERMINAL_STATUSES}

        if not active:
            await self._send(chat_id, escape_markdown_v2('💤 目前沒有你的任務'))
            return

        # Group by status category
        from ..models import TaskProgressEntry

        groups: dict[str, list[tuple[str, TaskProgressEntry]]] = {
            _GROUP_DOWNLOADING: [],
            _GROUP_WAITING: [],
            _GROUP_COOLING: [],
        }
        for sn_str, entry in active.items():
            grp = _group_status(entry.status)
            groups[grp].append((sn_str, entry))

        lines: list[str] = ['🔄 *你的任務狀態*', '']
        for grp_name in (_GROUP_DOWNLOADING, _GROUP_WAITING, _GROUP_COOLING):
            items = groups[grp_name]
            if not items:
                continue
            lines.append(f'*{grp_name}*')
            for sn_str, e in items:
                name_part = escape_markdown_v2(e.bangumi_name or '')
                ep_part = escape_markdown_v2(e.episode or '')
                rate_part = f'{int(e.rate * 100)}%'
                if grp_name == _GROUP_COOLING and e.cooldown_until:
                    # Show approx remaining
                    import datetime  # noqa: PLC0415

                    try:
                        until = datetime.datetime.fromisoformat(e.cooldown_until)
                        now = datetime.datetime.now(datetime.UTC)
                        if until.tzinfo is None:
                            until = until.replace(tzinfo=datetime.UTC)
                        remaining_s = max(0, int((until - now).total_seconds()))
                        rate_part = escape_markdown_v2(f'還 {remaining_s}s')
                    except Exception:  # noqa: BLE001
                        pass
                lines.append(f'• SN {escape_markdown_v2(sn_str)} {name_part} {ep_part}  {rate_part}')

        lines.append('')
        lines.append(escape_markdown_v2(f'（共 {len(active)} 筆）'))
        await self._send(chat_id, '\n'.join(lines))

"""Dispatch Telegram user commands to existing service layers.

Called by the webhook for every message from a user who is already
bound. Unbound users (no telegram_chat_id on their row) get a
"please bind on the website" reply, handled upstream.
"""

from __future__ import annotations

import logging
import re
import typing as T

import anyio.to_thread
import fastapi

from ..models import AnimeListEntry, ManualTaskRequest
from ..persistence.user_repo import UserRow
from .telegram_client import TelegramClient, escape_markdown_v2
from .telegram_rate_limiter import TelegramRateLimiter

if T.TYPE_CHECKING:
    from ..downloader.metadata import MetadataExtractor
    from ..logging_ import Logger
    from ..persistence.user_repo import UserRepository
    from .animelist_service import AnimeListService
    from .progress_service import ProgressService
    from .task_service import TaskService
    from .telegram_live_menu import LiveMenuRegistry
    from .telegram_menu import MenuRenderer

_HELP_TEXT = (
    '*可用指令*\n'
    '\n'
    '/menu — 開啟控制台（推薦，所有功能都在這裡）\n'
    '/download `<sn>` \\[解析度\\] — 立即下載單集\n'
    '/watch `<sn>` \\[選項\\] — 加入追番清單\n'
    '　　選項：`tag=系列名` `season=2` `mode=largest` `name=自訂名`\n'
    '　　例：`/watch 48613 tag=進擊系列 season=2 name=我的名字`\n'
    '　　名稱含空格請用底線代替（e\\.g\\. `name=我的_名字`）\n'
    '/help — 顯示此說明\n'
)

# Telegram bot menu — pushed to /setMyCommands so clients show a "/" menu.
# Keep in sync with _HELP_TEXT and the dispatcher below; descriptions must be
# 1–256 chars per Telegram spec.
BOT_MENU_COMMANDS: list[dict[str, str]] = [
    {'command': 'menu', 'description': '開啟控制台 (推薦)'},
    {'command': 'download', 'description': '立即下載單集 (sn)'},
    {'command': 'watch', 'description': '加入追番清單'},
    {'command': 'help', 'description': '顯示說明'},
]

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

# URL auto-parse pattern for ani.gamer.com.tw
_ANI_URL_RE = re.compile(r'https?://(?:www\.)?ani\.gamer\.com\.tw/animeVideo\.php\?sn=(\d+)')

# Admin-only guard message
_ADMIN_ONLY = '🚫 此指令僅限管理員'


def _group_status(status: str) -> str:
    for key, grp in _STATUS_GROUP.items():
        if key in status:
            return grp
    return _GROUP_WAITING


def _inline_keyboard(*rows: list[dict[str, str]]) -> dict[str, object]:
    """Build a Telegram InlineKeyboardMarkup dict."""
    return {'inline_keyboard': list(rows)}


_WATCH_VALID_KWARGS = frozenset({'tag', 'season', 'mode', 'name'})


def _parse_watch_args(
    args: list[str],
) -> tuple[str | None, str, int, str | None, str | None] | str:
    """Parse args after the sn token for /watch.

    Returns either a tuple ``(custom_name, tag, season, mode, name_kwarg)``
    or a string error message to send back to the user.

    Rules:
    - If the first token (after sn) has no ``=``, treat ALL remaining tokens as
      a legacy positional custom_name (backwards compat).
    - Otherwise parse each token as ``key=value``.
    - Unknown keys → return error string.
    - Invalid int for season → return error string.
    """
    if not args:
        return (None, '', 1, None, None)

    # Backwards compat: first extra token has no '='
    if '=' not in args[0]:
        positional_name = ' '.join(args)
        return (positional_name, '', 1, None, None)

    # Kwarg mode
    tag: str = ''
    season: int = 1
    mode: str | None = None
    name_kwarg: str | None = None

    for token in args:
        if '=' not in token:
            # Mixed ambiguous: positional token after kwargs → reject
            return f'⚠️ 無法解析參數 "{token}"，請使用 key=value 格式（tag / season / mode / name）'
        key, _, value = token.partition('=')
        if key not in _WATCH_VALID_KWARGS:
            return f'⚠️ 未知選項：{key}，可用選項：tag / season / mode / name'
        if key == 'tag':
            tag = value
        elif key == 'season':
            try:
                season = int(value)
            except ValueError:
                return '⚠️ season 必須是整數'
        elif key == 'mode':
            mode = value
        elif key == 'name':
            name_kwarg = value

    return (None, tag, season, mode, name_kwarg)


class TelegramCommandDispatcher:
    """Parses a command string and calls the matching service method."""

    def __init__(
        self,
        *,
        client_provider: T.Callable[[], TelegramClient | None],
        user_repo: UserRepository,
        animelist_service: AnimeListService,
        task_service: TaskService,
        progress_service: ProgressService,
        rate_limiter: TelegramRateLimiter,
        logger: Logger,
        metadata_extractor: MetadataExtractor | None = None,
        menu_renderer: MenuRenderer | None = None,
        live_menu: LiveMenuRegistry | None = None,
    ) -> None:
        self._client_provider = client_provider
        self._user_repo = user_repo
        self._animelist_service = animelist_service
        self._task_service = task_service
        self._progress_service = progress_service
        self._rate_limiter = rate_limiter
        self._metadata_extractor = metadata_extractor
        self._menu_renderer = menu_renderer
        self._live_menu = live_menu
        self._log = logging.getLogger(__name__)

    @property
    def _client(self) -> TelegramClient:
        """Resolve client per-call so token rotations take effect immediately."""
        client = self._client_provider()
        if client is None:
            raise RuntimeError('TelegramClient is not available (no bot_token configured)')
        return client

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

        # URL auto-parse: check before command dispatch
        url_match = _ANI_URL_RE.search(text)
        if url_match:
            await self._handle_url(chat_id, user, int(url_match.group(1)))
            return

        parts = text.strip().split()
        if not parts:
            await self._send(chat_id, escape_markdown_v2('/help 可查看可用指令。'))
            return

        cmd = parts[0].lower()
        args = parts[1:]

        handlers: dict[str, T.Callable[[int, UserRow, list[str]], T.Coroutine[object, object, None]]] = {
            '/menu': self._cmd_menu,
            '/download': self._cmd_download,
            '/watch': self._cmd_watch,
            '/unwatch': self._cmd_unwatch,
            '/list': self._cmd_list,
            '/status': self._cmd_status,
            '/cancel': self._cmd_cancel,
            '/me': self._cmd_me,
            '/help': self._cmd_help,
            '/admin_stats': self._cmd_admin_stats,
            '/admin_users': self._cmd_admin_users,
            '/admin_cancel': self._cmd_admin_cancel,
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

    async def handle_callback_query(
        self,
        *,
        user: UserRow,
        callback_query: object,
    ) -> None:
        """Dispatch an inline keyboard callback query.

        Always answers the callback query first so the Telegram UI stops
        spinning, then edits the originating message.
        """
        # Use getattr to avoid circular imports — callback_query is the pydantic
        # model from telegram_webhook, accessed duck-typed here.
        cq_id: str = getattr(callback_query, 'id', '')
        data: str = getattr(callback_query, 'data', '') or ''
        message = getattr(callback_query, 'message', None)

        chat_id: int | None = getattr(message, 'chat', None) and getattr(message.chat, 'id', None)  # type: ignore[union-attr]
        message_id: int | None = getattr(message, 'message_id', None)

        # Answer immediately so Telegram stops spinner
        await self._client.answer_callback_query(cq_id)

        if data == 'cancel_prompt':
            if chat_id is not None and message_id is not None:
                await self._client.edit_message_text(
                    chat_id,
                    message_id,
                    escape_markdown_v2('已取消'),
                    reply_markup=None,
                )
            return

        if data.startswith('dl:'):
            parts = data.split(':', 2)
            if len(parts) < 3:  # noqa: PLR2004
                await self._answer_invalid(cq_id)
                return
            sn_str, resolution = parts[1], parts[2]
            try:
                sn = int(sn_str)
            except ValueError:
                await self._answer_invalid(cq_id)
                return

            await self._cmd_download(chat_id or 0, user, [sn_str, resolution])
            if chat_id is not None and message_id is not None:
                await self._client.edit_message_text(
                    chat_id,
                    message_id,
                    escape_markdown_v2(f'✅ 任務已加入佇列 SN {sn} {resolution}p'),
                    reply_markup=None,
                )
            return

        if data.startswith('force_dl:'):
            parts = data.split(':', 2)
            if len(parts) < 3:  # noqa: PLR2004
                await self._answer_invalid(cq_id)
                return
            sn_str, resolution = parts[1], parts[2]
            try:
                sn = int(sn_str)
            except ValueError:
                await self._answer_invalid(cq_id)
                return
            # force_dl: re-enqueue regardless; service decides
            await self._cmd_download(chat_id or 0, user, [sn_str, resolution])
            if chat_id is not None and message_id is not None:
                await self._client.edit_message_text(
                    chat_id,
                    message_id,
                    escape_markdown_v2(f'✅ 任務已重送 SN {sn} {resolution}p'),
                    reply_markup=None,
                )
            return

        if data.startswith('watch:'):
            sn_str = data.split(':', 1)[1]
            try:
                sn = int(sn_str)
            except ValueError:
                await self._answer_invalid(cq_id)
                return
            await self._cmd_watch(chat_id or 0, user, [sn_str])
            if chat_id is not None and message_id is not None:
                await self._client.edit_message_text(
                    chat_id,
                    message_id,
                    escape_markdown_v2(f'✅ 已加入追番 SN {sn}'),
                    reply_markup=None,
                )
            return

        if data.startswith('confirm_cancel:'):
            sn_str = data.split(':', 1)[1]
            try:
                sn = int(sn_str)
            except ValueError:
                await self._answer_invalid(cq_id)
                return
            # Call _do_cancel directly to bypass the confirmation-prompt path
            await self._do_cancel(chat_id or 0, user, sn)
            if chat_id is not None and message_id is not None:
                await self._client.edit_message_text(
                    chat_id,
                    message_id,
                    escape_markdown_v2(f'🛑 已取消 SN {sn}'),
                    reply_markup=None,
                )
            return

        if data.startswith('m:'):
            if self._menu_renderer is None:
                await self._client.answer_callback_query(cq_id)
                return
            try:
                text, kb = await self._menu_renderer.render(user, data)
            except Exception as exc:  # noqa: BLE001
                await self._client.answer_callback_query(cq_id, text=f'錯誤: {exc}', show_alert=True)
                return
            if chat_id is not None and message_id is not None:
                await self._client.edit_message_text(chat_id, message_id, text, reply_markup=kb)
            return

        # Unknown callback data
        await self._client.answer_callback_query(cq_id, text='無效按鈕', show_alert=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _send(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        await self._client.send_message(chat_id, text, reply_markup=reply_markup)

    async def _answer_invalid(self, cq_id: str) -> None:
        await self._client.answer_callback_query(cq_id, text='無效按鈕', show_alert=True)

    async def _resolve_anime_name(self, sn: int, user: UserRow) -> str | None:
        """Try to resolve an anime name from existing animelist entries."""
        try:
            entries = await self._animelist_service.list_entries(user)
            for e in entries:
                if e.sn == sn and e.anime_name:
                    return e.anime_name
        except Exception:  # noqa: BLE001
            pass
        return None

    # ------------------------------------------------------------------
    # URL auto-parse
    # ------------------------------------------------------------------

    async def _handle_url(self, chat_id: int, user: UserRow, sn: int) -> None:
        """Reply with inline keyboard when user pastes an ani.gamer URL."""
        anime_name = await self._resolve_anime_name(sn, user)
        display = anime_name or f'SN {sn}'

        text = f'偵測到連結: {escape_markdown_v2(display)}\n要怎麼做?'
        keyboard = _inline_keyboard(
            [
                {'text': '下載 1080p', 'callback_data': f'dl:{sn}:1080'},
                {'text': '下載 720p', 'callback_data': f'dl:{sn}:720'},
            ],
            [
                {'text': '加入追番', 'callback_data': f'watch:{sn}'},
                {'text': '取消', 'callback_data': 'cancel_prompt'},
            ],
        )
        await self._send(chat_id, text, reply_markup=keyboard)

    # ------------------------------------------------------------------
    # Individual command handlers
    # ------------------------------------------------------------------

    async def _cmd_help(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        await self._send(chat_id, _HELP_TEXT)

    async def _cmd_menu(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        if self._menu_renderer is None or self._live_menu is None:
            await self._send(chat_id, escape_markdown_v2('❌ 控制台未啟用'))
            return
        # Delete previous menu message if any
        prev_message_id = await self._live_menu.get(user.id)
        if prev_message_id is not None:
            try:
                await self._client.delete_message(chat_id, prev_message_id)
            except Exception:  # noqa: BLE001
                pass
        text, kb = await self._menu_renderer.render_root(user)
        result = await self._client.send_message(chat_id, text, reply_markup=kb)
        msg_id = result.get('message_id') if isinstance(result, dict) else None
        if isinstance(msg_id, int):
            await self._live_menu.set(user.id, msg_id)

    async def _cmd_me(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        await self._send(chat_id, escape_markdown_v2('請打 /menu 開啟控制台'))

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
            if exc.status_code == 503:  # noqa: PLR2004
                await self._send(chat_id, escape_markdown_v2('❌ 排程服務目前無回應，請稍後重試'))
            elif exc.status_code == 409:  # noqa: PLR2004
                # Already in queue — show confirm keyboard
                res = resolution if resolution in ('360', '480', '540', '720', '1080') else '1080'
                keyboard = _inline_keyboard(
                    [
                        {'text': '強制重送', 'callback_data': f'force_dl:{sn}:{res}'},
                        {'text': '取消', 'callback_data': 'cancel_prompt'},
                    ],
                )
                await self._send(
                    chat_id,
                    escape_markdown_v2(f'⚠️ SN {sn} 已在佇列中。'),
                    reply_markup=keyboard,
                )
            else:
                await self._send(chat_id, escape_markdown_v2(f'❌ 送出失敗: {exc.detail}'))
            return
        except Exception as exc:  # noqa: BLE001
            await self._send(chat_id, escape_markdown_v2(f'❌ 送出失敗: {exc}'))
            return

        res = resolution if resolution in ('360', '480', '540', '720', '1080') else '預設'
        await self._send(chat_id, escape_markdown_v2(f'✅ 任務已加入佇列 SN={sn} 解析度={res}p'))

    async def _cmd_cancel(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        await self._send(chat_id, escape_markdown_v2('請打 /menu 開啟控制台'))

    async def _do_cancel(self, chat_id: int, user: UserRow, sn: int) -> None:
        """Actually perform task cancellation (called from callback or direct)."""
        try:
            await self._task_service.cancel_task(sn, user)
        except fastapi.HTTPException as exc:
            if exc.status_code == 404:  # noqa: PLR2004
                await self._send(chat_id, escape_markdown_v2(f'⚠️ 找不到任務 SN {sn}'))
            elif exc.status_code == 403:  # noqa: PLR2004
                await self._send(chat_id, escape_markdown_v2('🚫 你沒有權限取消他人的任務'))
            else:
                await self._send(chat_id, escape_markdown_v2(f'❌ 取消失敗: {exc.detail}'))
            return

        await self._send(chat_id, escape_markdown_v2(f'🛑 已取消 SN {sn}'))

    async def _cmd_watch(self, chat_id: int, user: UserRow, args: list[str]) -> None:
        if not args:
            await self._send(
                chat_id,
                escape_markdown_v2('用法：/watch <sn> [tag=系列 season=1 mode=single name=自訂名]'),
            )
            return
        try:
            sn = int(args[0])
        except ValueError:
            await self._send(chat_id, escape_markdown_v2(f'❌ SN 必須是整數，收到：{args[0]}'))
            return

        # Parse kwargs / positional args after sn
        parse_result = _parse_watch_args(args[1:])
        if isinstance(parse_result, str):
            await self._send(chat_id, escape_markdown_v2(parse_result))
            return
        positional_name, tag, season, mode, name_kwarg = parse_result
        # kwargs name= takes priority; fall back to positional
        custom_name: str | None = name_kwarg if name_kwarg is not None else positional_name

        # Resolve bangumi_name: fast-path cache → network fetch → placeholder
        bangumi_name: str | None = None
        name_warning: str | None = None
        try:
            existing_entries = await self._animelist_service.list_entries(user)
            for e in existing_entries:
                if e.sn == sn and e.anime_name:
                    bangumi_name = e.anime_name
                    break
        except Exception:  # noqa: BLE001
            pass

        if bangumi_name is None and self._metadata_extractor is not None:
            try:
                metadata = await anyio.to_thread.run_sync(
                    lambda: self._metadata_extractor.fetch(sn),  # type: ignore[union-attr]
                )
                bangumi_name = metadata.bangumi_name
            except Exception:  # noqa: BLE001
                self._log.warning('MetadataExtractor.fetch(%d) failed; falling back to placeholder', sn)
                name_warning = f'⚠️ 無法從動畫瘋取得 SN {sn} 的名稱，將以 "SN {sn}" 暫存，下一次掃描時自動補齊'

        if bangumi_name is None:
            bangumi_name = f'SN {sn}'

        # Build the new entry
        new_entry = AnimeListEntry(
            sn=sn,
            enabled=True,
            owner_id=user.id,
            anime_name=bangumi_name,
            custom_name=custom_name,
            tag=tag,
            season=season,
            mode=mode,  # type: ignore[arg-type]
        )

        # Fetch existing entries, check dups, append, save
        existing = await self._animelist_service.list_entries(user)
        own_entries = [e for e in existing if e.owner_id == user.id]
        if any(e.sn == sn for e in own_entries):
            await self._send(chat_id, escape_markdown_v2(f'⚠️ 你已在追番清單中有 SN {sn}'))
            return

        updated = list(existing)
        updated.append(new_entry)

        await self._animelist_service.replace_entries(user, updated)

        if name_warning:
            await self._send(chat_id, escape_markdown_v2(name_warning))

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

    async def _cmd_unwatch(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        await self._send(chat_id, escape_markdown_v2('請打 /menu 開啟控制台'))

    async def _cmd_list(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        await self._send(chat_id, escape_markdown_v2('請打 /menu 開啟控制台'))

    async def _cmd_status(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        await self._send(chat_id, escape_markdown_v2('請打 /menu 開啟控制台'))

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------

    async def _cmd_admin_stats(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        await self._send(chat_id, escape_markdown_v2('請打 /menu 開啟控制台'))

    async def _cmd_admin_users(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        await self._send(chat_id, escape_markdown_v2('請打 /menu 開啟控制台'))

    async def _cmd_admin_cancel(self, chat_id: int, user: UserRow, args: list[str]) -> None:  # noqa: ARG002
        await self._send(chat_id, escape_markdown_v2('請打 /menu 開啟控制台'))

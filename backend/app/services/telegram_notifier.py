"""Dispatch download lifecycle events to Telegram DMs.

Handles 'started', 'completed', 'failed', 'cancelled', and 'auto_enqueue'
events. Terminal events (completed/failed/cancelled) edit the 'started'
progress bubble in-place via LiveMessageRegistry so each task produces one
DM thread rather than a burst of separate messages.

Errors:
- TelegramBotBlockedError / TelegramChatNotFoundError → clear that
  user's telegram_chat_id in the repo and log. The user must re-bind.
- Any other error → log WARN + swallow. Never let a notification
  failure kill a download or abort the scheduler loop.
"""

from __future__ import annotations

import asyncio
import collections.abc
import datetime
import time
import typing as T

from .telegram_client import TelegramBotBlockedError, TelegramChatNotFoundError, escape_markdown_v2

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..models import TelegramSettings
    from ..persistence.user_repo import UserRepository
    from .telegram_client import TelegramClient
    from .telegram_live_messages import LiveMessageRegistry

# Maximum inline retries on 429 when sending the 'started' DM (we need the
# message_id back immediately and can't use the actor pathway for that).
_STARTED_429_MAX_RETRIES = 3
_STARTED_429_BACKOFF_BASE = 2.0  # seconds


class TelegramNotifier:
    """Send download-event DMs to owner + admins via Telegram."""

    def __init__(
        self,
        client: TelegramClient,
        user_repo: UserRepository,
        settings_provider: collections.abc.Callable[[], TelegramSettings],
        live_messages: LiveMessageRegistry | None,
        logger: Logger,
    ) -> None:
        self._client = client
        self._user_repo = user_repo
        self._settings_provider = settings_provider
        self._live_messages = live_messages
        self._logger = logger

    # ------------------------------------------------------------------ public

    async def notify_download_event(
        self,
        *,
        event: T.Literal['started', 'completed', 'failed', 'cancelled', 'auto_enqueue'],
        owner_id: str | None,
        sn: int,
        bangumi_name: str,
        episode: str | None,
        resolution: str | None,
        file_size_mb: int | None = None,
        error_message: str | None = None,
        custom_name: str | None = None,
        season: int = 1,
        episode_number: int | None = None,
    ) -> None:
        """Send DM to owner + (optionally) admins.

        No-op when settings.enabled is False or the event is not in
        settings.notify_on.  owner_id=None skips the owner DM but still
        notifies admins for events that support it.
        """
        settings = self._settings_provider()
        if not settings.enabled:
            return
        if event not in settings.notify_on:
            return

        if event == 'started':
            await self._handle_started(
                sn=sn,
                owner_id=owner_id,
                bangumi_name=bangumi_name,
                episode=episode,
                resolution=resolution,
                custom_name=custom_name,
                season=season,
                episode_number=episode_number,
                settings=settings,
            )

        elif event == 'auto_enqueue':
            await self._handle_auto_enqueue(
                sn=sn,
                owner_id=owner_id,
                bangumi_name=bangumi_name,
                episode=episode,
                custom_name=custom_name,
                season=season,
                episode_number=episode_number,
                settings=settings,
            )

        else:  # completed / failed / cancelled
            await self._handle_terminal(
                event=event,
                sn=sn,
                owner_id=owner_id,
                bangumi_name=bangumi_name,
                episode=episode,
                resolution=resolution,
                file_size_mb=file_size_mb,
                error_message=error_message,
                custom_name=custom_name,
                season=season,
                episode_number=episode_number,
                settings=settings,
            )

    # ------------------------------------------------------------------ per-event handlers

    async def _handle_started(
        self,
        *,
        sn: int,
        owner_id: str | None,
        bangumi_name: str,
        episode: str | None,
        resolution: str | None,
        custom_name: str | None,
        season: int,
        episode_number: int | None,
        settings: TelegramSettings,
    ) -> None:
        text = _format_message(
            event='started',
            bangumi_name=bangumi_name,
            episode=episode,
            resolution=resolution,
            file_size_mb=None,
            error_message=None,
            custom_name=custom_name,
            season=season,
            episode_number=episode_number,
        )
        keyboard = _started_keyboard(sn)
        recipients = await self._recipient_chat_ids(owner_id, settings=settings)

        for uid, chat_id in recipients:
            result = await self._send_with_429_retry(
                chat_id,
                text,
                uid,
                reply_markup=keyboard,
            )
            if result is not None and self._live_messages is not None:
                msg_id = result.get('message_id')
                if isinstance(msg_id, int):
                    await self._live_messages.set(
                        sn,
                        chat_id,
                        message_id=msg_id,
                        last_edit_at=time.monotonic(),
                        last_rate=0.0,
                    )

    async def _handle_auto_enqueue(
        self,
        *,
        sn: int,
        owner_id: str | None,
        bangumi_name: str,
        episode: str | None,
        custom_name: str | None,
        season: int,
        episode_number: int | None,
        settings: TelegramSettings,
    ) -> None:
        """Notify only the owner (no admin broadcast for auto_enqueue)."""
        text = _format_message(
            event='auto_enqueue',
            bangumi_name=bangumi_name,
            episode=episode,
            resolution=None,
            file_size_mb=None,
            error_message=None,
            custom_name=custom_name,
            season=season,
            episode_number=episode_number,
        )
        # auto_enqueue goes only to owner — admins don't need per-episode queue pings.
        if owner_id is None:
            return
        owner = self._user_repo.get(owner_id)
        if owner is None or owner.telegram_chat_id is None or not owner.telegram_notify_enabled:
            return
        if not self._is_muted(owner):
            await self._send_via_actor(owner.telegram_chat_id, text, owner_id, settings=settings)

    async def _handle_terminal(
        self,
        *,
        event: str,
        sn: int,
        owner_id: str | None,
        bangumi_name: str,
        episode: str | None,
        resolution: str | None,
        file_size_mb: int | None,
        error_message: str | None,
        custom_name: str | None,
        season: int,
        episode_number: int | None,
        settings: TelegramSettings,
    ) -> None:
        text = _format_message(
            event=event,
            bangumi_name=bangumi_name,
            episode=episode,
            resolution=resolution,
            file_size_mb=file_size_mb,
            error_message=error_message,
            custom_name=custom_name,
            season=season,
            episode_number=episode_number,
        )
        recipients = await self._recipient_chat_ids(owner_id, settings=settings)

        for uid, chat_id in recipients:
            # Try to upgrade the 'started' DM in-place.
            if self._live_messages is not None:
                existing = await self._live_messages.get(sn, chat_id)
                if existing is not None:
                    message_id, _, _ = existing
                    await self._live_messages.clear(sn, chat_id)
                    await self._edit_via_actor(chat_id, message_id, text, uid, settings=settings, reply_markup=None)
                    continue

            # No live message found → send a fresh DM.
            await self._send_via_actor(chat_id, text, uid, settings=settings)

    # ------------------------------------------------------------------ recipient resolution

    async def _recipient_chat_ids(
        self,
        owner_id: str | None,
        *,
        settings: TelegramSettings,
    ) -> list[tuple[str, int]]:
        """Return [(user_id, chat_id), ...] de-duplicated, mute-filtered."""
        seen_chat_ids: set[int] = set()
        result: list[tuple[str, int]] = []

        if owner_id is not None:
            owner = self._user_repo.get(owner_id)
            if (
                owner is not None
                and owner.telegram_chat_id is not None
                and owner.telegram_notify_enabled
                and not self._is_muted(owner)
            ):
                seen_chat_ids.add(owner.telegram_chat_id)
                result.append((owner.id, owner.telegram_chat_id))

        if settings.admin_broadcast:
            all_users = self._user_repo.list_all()
            for user in all_users:
                if user.role != 'admin':
                    continue
                if user.telegram_chat_id is None:
                    continue
                if not user.telegram_notify_enabled:
                    continue
                if user.telegram_chat_id in seen_chat_ids:
                    continue
                if self._is_muted(user):
                    continue
                seen_chat_ids.add(user.telegram_chat_id)
                result.append((user.id, user.telegram_chat_id))

        return result

    # ------------------------------------------------------------------ send helpers

    async def _send_with_429_retry(
        self,
        chat_id: int,
        text: str,
        user_id: str,
        *,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Direct send with inline 429 retry — used for 'started' so we capture message_id."""
        last_exc: Exception | None = None
        for attempt in range(_STARTED_429_MAX_RETRIES):
            try:
                return await self._client.send_message(chat_id, text, reply_markup=reply_markup)
            except (TelegramBotBlockedError, TelegramChatNotFoundError) as exc:
                self._handle_permanent_error(exc, user_id, chat_id)
                return None
            except Exception as exc:  # noqa: BLE001
                # Check for 429 specifically.
                from .telegram_client import TelegramApiError

                if isinstance(exc, TelegramApiError) and exc.status_code == 429:
                    last_exc = exc
                    backoff = _STARTED_429_BACKOFF_BASE**attempt
                    await asyncio.sleep(backoff)
                    continue
                self._logger.info(
                    None,
                    'TelegramNotifier',
                    f'傳送通知失敗 (chat_id={chat_id}, user_id={user_id}): {exc}',
                    display=False,
                )
                return None
        if last_exc is not None:
            self._logger.info(
                None,
                'TelegramNotifier',
                f'429 重試已耗盡 (chat_id={chat_id}): {last_exc}',
                display=False,
            )
        return None

    async def _send_via_actor(
        self,
        chat_id: int,
        text: str,
        user_id: str,
        *,
        settings: TelegramSettings,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        """Enqueue a send via the dramatiq actor (fire-and-forget)."""
        from ..tasks.telegram import send_message_actor

        try:
            send_message_actor.send(
                chat_id,
                text,
                bot_token=settings.bot_token,
                reply_markup=reply_markup,
            )
        except (TelegramBotBlockedError, TelegramChatNotFoundError) as exc:
            self._handle_permanent_error(exc, user_id, chat_id)
        except Exception as exc:  # noqa: BLE001
            self._logger.info(
                None,
                'TelegramNotifier',
                f'傳送通知失敗 (chat_id={chat_id}, user_id={user_id}): {exc}',
                display=False,
            )

    async def _edit_via_actor(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        user_id: str,
        *,
        settings: TelegramSettings,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        """Enqueue an edit via the dramatiq actor (fire-and-forget)."""
        from ..tasks.telegram import edit_message_actor

        try:
            edit_message_actor.send(
                chat_id,
                message_id,
                text,
                bot_token=settings.bot_token,
                reply_markup=reply_markup,
            )
        except (TelegramBotBlockedError, TelegramChatNotFoundError) as exc:
            self._handle_permanent_error(exc, user_id, chat_id)
        except Exception as exc:  # noqa: BLE001
            self._logger.info(
                None,
                'TelegramNotifier',
                f'編輯通知失敗 (chat_id={chat_id}, user_id={user_id}): {exc}',
                display=False,
            )

    # ------------------------------------------------------------------ helpers

    def _handle_permanent_error(
        self,
        exc: Exception,
        user_id: str,
        chat_id: int,
    ) -> None:
        self._logger.error(
            None,
            'TelegramNotifier',
            f'清除 user_id={user_id} 的 Telegram 綁定 (chat_id={chat_id}): {exc}',
            display=False,
        )
        self._user_repo.clear_telegram_binding(user_id)

    @staticmethod
    def _is_muted(user: object) -> bool:
        mute_until = getattr(user, 'telegram_mute_until', None)
        if mute_until is None:
            return False
        now = datetime.datetime.now(datetime.UTC)
        # Ensure tz-aware comparison.
        if mute_until.tzinfo is None:
            mute_until = mute_until.replace(tzinfo=datetime.UTC)
        return bool(mute_until > now)


# ---------------------------------------------------------------------------
# Inline keyboard helpers
# ---------------------------------------------------------------------------


def _started_keyboard(sn: int) -> dict[str, object]:
    """Return the inline_keyboard dict with a '❌ 取消' button."""
    return {
        'inline_keyboard': [
            [{'text': '❌ 取消', 'callback_data': f'm:cancel_yes:{sn}'}],
        ]
    }


# ---------------------------------------------------------------------------
# Message formatting helpers
# ---------------------------------------------------------------------------


def build_name_line(
    *,
    bangumi_name: str,
    episode: str | None,
    custom_name: str | None,
    season: int,
    episode_number: int | None,
) -> str:
    """Return the MarkdownV2-escaped name line for a download-event message.

    Format (normal): ``{display_name} 第 {season} 季 - 第 {episode_number} 集``
    Fallback (unparseable episode like SP/OVA): ``{display_name} 第 {season} 季 - {raw_episode}``
    """
    display_name = custom_name or bangumi_name
    name_esc = escape_markdown_v2(display_name)
    season_str = escape_markdown_v2(str(season))

    # MarkdownV2 requires '-' to be escaped as '\-' in literal text.
    dash = escape_markdown_v2('-')

    if episode_number is not None:
        ep_str = escape_markdown_v2(str(episode_number))
        return f'{name_esc} 第 {season_str} 季 {dash} 第 {ep_str} 集'
    else:
        raw_ep = escape_markdown_v2(episode or '')
        return f'{name_esc} 第 {season_str} 季 {dash} {raw_ep}'.rstrip()


def _format_message(
    *,
    event: str,
    bangumi_name: str,
    episode: str | None,
    resolution: str | None,
    file_size_mb: int | None,
    error_message: str | None,
    custom_name: str | None = None,
    season: int = 1,
    episode_number: int | None = None,
) -> str:
    """Build a MarkdownV2-escaped message for the given event."""
    name_line = build_name_line(
        bangumi_name=bangumi_name,
        episode=episode,
        custom_name=custom_name,
        season=season,
        episode_number=episode_number,
    )

    if event == 'started':
        lines = [
            '⏬ *下載中*',
            '',
            name_line,
        ]
        return '\n'.join(lines)

    if event == 'auto_enqueue':
        lines = [
            '📥 *新集數加入佇列*',
            '',
            name_line,
        ]
        return '\n'.join(lines)

    if event == 'completed':
        lines = [
            '✅ *下載完成*',
            '',
            name_line,
        ]
        if resolution is not None:
            res_esc = escape_markdown_v2(_format_resolution(resolution))
            lines.append(f'解析度: {res_esc}')
        if file_size_mb is not None:
            size_esc = escape_markdown_v2(str(file_size_mb))
            lines.append(f'檔案大小: {size_esc} MB')
        return '\n'.join(lines)

    if event == 'failed':
        lines = [
            '❌ *下載失敗*',
            '',
            name_line,
        ]
        if error_message is not None:
            err_esc = escape_markdown_v2(error_message[:200])
            lines.append(f'原因: {err_esc}')
        return '\n'.join(lines)

    # cancelled
    lines = [
        '🛑 *下載取消*',
        '',
        name_line,
    ]
    return '\n'.join(lines)


def _seconds_to_human(seconds: int) -> str:
    """Convert seconds to a human-readable ``Xm Ys`` / ``Xs`` string."""
    if seconds >= 60:
        m, s = divmod(seconds, 60)
        return f'{m}m {s:02d}s'
    return f'{seconds}s'


def _format_resolution(resolution: object) -> str:
    """Append 'p' suffix if missing — '1080' → '1080p', '1080p' → '1080p'."""
    if resolution is None:
        return ''
    s = str(resolution).strip()
    if not s:
        return ''
    if s.lower().endswith('p'):
        return s
    return f'{s}p'


def format_progress_body(entry: object) -> str:
    """Build the MarkdownV2 progress body lines for an in-flight task.

    Kept public so progress_publish_tick can reuse the same formatter without
    duplicating the bar/speed/ETA rendering logic.
    """
    import datetime

    rate: float = getattr(entry, 'rate', 0.0) or 0.0
    speed_mbps: float | None = getattr(entry, 'speed_mbps', None)
    eta_seconds: int | None = getattr(entry, 'eta_seconds', None)
    retries: int = getattr(entry, 'retries', 0)
    cooldown_until: datetime.datetime | None = getattr(entry, 'cooldown_until', None)

    # Cooldown takes priority over normal progress display.
    if cooldown_until is not None:
        now_utc = datetime.datetime.now(datetime.UTC)
        if cooldown_until.tzinfo is None:
            cooldown_until = cooldown_until.replace(tzinfo=datetime.UTC)
        remaining = (cooldown_until - now_utc).total_seconds()
        if remaining > 0:
            remaining_n = escape_markdown_v2(f'{int(remaining)}')
            return f'⏸ 冷卻中 \\(還 {remaining_n}s\\)'

    # Normalise the rate to a 0.0-1.0 fraction.  segment_downloader writes it
    # as 0-100 (done/total*100); ffmpeg_downloader writes it as 0-1.  Detect
    # which by magnitude — values > 1.0 are clearly percentages.  Clamp
    # to [0, 1] so a bug upstream can't blow Telegram's 4096-byte limit
    # with thousands of bar characters.
    if rate > 1.0:
        rate = rate / 100.0
    rate = max(0.0, min(1.0, rate))

    # Progress bar: 10 cells — ▰/▱ render more clearly on mobile Telegram.
    filled = round(rate * 10)
    bar_raw = '▰' * filled + '▱' * (10 - filled)
    pct_raw = f'{int(rate * 100)}%'
    lines = [escape_markdown_v2(f'{bar_raw} {pct_raw}')]

    if speed_mbps is not None:
        speed_str = escape_markdown_v2(f'{speed_mbps:.1f} MB/s')
        lines.append(f'速度: {speed_str}')

    if eta_seconds is not None:
        eta_str = escape_markdown_v2(_seconds_to_human(eta_seconds))
        lines.append(f'剩餘: {eta_str}')

    if retries >= 1:
        retries_str = escape_markdown_v2(str(retries))
        lines.append(f'重試: {retries_str}')

    return '\n'.join(lines)

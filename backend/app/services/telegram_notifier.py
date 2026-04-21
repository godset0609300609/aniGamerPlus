"""Dispatch download lifecycle events to Telegram DMs.

Wires into DownloadWorker on success/failure/cancellation paths.
Sends one DM to the owner (if bound + opted-in), plus one DM to each
admin (ditto), de-duping owner-is-admin.

Errors:
- TelegramBotBlockedError / TelegramChatNotFoundError → clear that
  user's telegram_chat_id in the repo and log. The user must re-bind.
- Any other error → log WARN + swallow. Never let a notification
  failure kill a download or abort the scheduler loop.
"""

from __future__ import annotations

import typing as T

from .telegram_client import TelegramBotBlockedError, TelegramChatNotFoundError, escape_markdown_v2

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..models import TelegramSettings
    from ..persistence.user_repo import UserRepository
    from .telegram_client import TelegramClient


class TelegramNotifier:
    """Send download-event DMs to owner + admins via Telegram."""

    def __init__(
        self,
        client: TelegramClient,
        user_repo: UserRepository,
        settings: TelegramSettings,
        logger: Logger,
    ) -> None:
        self._client = client
        self._user_repo = user_repo
        self._settings = settings
        self._logger = logger

    async def notify_download_event(
        self,
        *,
        event: T.Literal['completed', 'failed', 'cancelled'],
        owner_id: str | None,
        bangumi_name: str,
        episode: str | None,
        resolution: str | None,
        sn: int,
        file_size_mb: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Send DM to owner + all admins.

        If settings.enabled is False OR event is not in settings.notify_on,
        no-op. If owner_id is None, skip owner DM (auto-scan tasks where
        no owner is attached — still notifies admins).
        """
        if not self._settings.enabled:
            return
        if event not in self._settings.notify_on:
            return

        text = _format_message(
            event=event,
            bangumi_name=bangumi_name,
            episode=episode,
            resolution=resolution,
            file_size_mb=file_size_mb,
            error_message=error_message,
        )

        recipients = await self._recipient_chat_ids(owner_id)
        for uid, chat_id in recipients:
            await self._send(chat_id, text, uid)

    async def _recipient_chat_ids(self, owner_id: str | None) -> list[tuple[str, int]]:
        """Return [(user_id, chat_id), ...] — de-duplicated.

        Includes owner if bound + enabled; plus every admin user who is
        bound + enabled. Single DM per unique chat_id.
        """
        seen_chat_ids: set[int] = set()
        result: list[tuple[str, int]] = []

        # Owner first.
        if owner_id is not None:
            owner = self._user_repo.get(owner_id)
            if owner is not None and owner.telegram_chat_id is not None and owner.telegram_notify_enabled:
                seen_chat_ids.add(owner.telegram_chat_id)
                result.append((owner.id, owner.telegram_chat_id))

        # Admins (skip if admin_broadcast is False).
        if self._settings.admin_broadcast:
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
                seen_chat_ids.add(user.telegram_chat_id)
                result.append((user.id, user.telegram_chat_id))

        return result

    async def _send(self, chat_id: int, text: str, user_id: str) -> None:
        """Wrap client.send_message with TelegramBotBlockedError +
        TelegramChatNotFoundError handling: clear binding on 403/chat-
        not-found; swallow + log otherwise.
        """
        try:
            await self._client.send_message(chat_id, text)
        except (TelegramBotBlockedError, TelegramChatNotFoundError) as exc:
            self._logger.error(
                None,
                'TelegramNotifier',
                f'清除 user_id={user_id} 的 Telegram 綁定 (chat_id={chat_id}): {exc}',
                display=False,
            )
            self._user_repo.clear_telegram_binding(user_id)
        except Exception as exc:  # noqa: BLE001
            self._logger.info(
                None,
                'TelegramNotifier',
                f'傳送通知失敗 (chat_id={chat_id}, user_id={user_id}): {exc}',
                display=False,
            )


# ---------------------------------------------------------------------------
# Message formatting helpers
# ---------------------------------------------------------------------------


def _format_message(
    *,
    event: str,
    bangumi_name: str,
    episode: str | None,
    resolution: str | None,
    file_size_mb: int | None,
    error_message: str | None,
) -> str:
    """Build a MarkdownV2-escaped message for the given event."""
    name_esc = escape_markdown_v2(bangumi_name)
    ep_esc = escape_markdown_v2(episode) if episode else None

    if event == 'completed':
        lines = [
            '✅ *下載完成*',
            '',
            f'{name_esc} {ep_esc}' if ep_esc else name_esc,
        ]
        if resolution is not None:
            res_esc = escape_markdown_v2(str(resolution))
            lines.append(f'解析度: {res_esc}')
        if file_size_mb is not None:
            size_esc = escape_markdown_v2(str(file_size_mb))
            lines.append(f'檔案大小: {size_esc} MB')
        return '\n'.join(lines)

    if event == 'failed':
        lines = [
            '❌ *下載失敗*',
            '',
            f'{name_esc} {ep_esc}' if ep_esc else name_esc,
        ]
        if error_message is not None:
            err_esc = escape_markdown_v2(error_message[:200])
            lines.append(f'原因: {err_esc}')
        return '\n'.join(lines)

    # cancelled
    lines = [
        '🛑 *下載取消*',
        '',
        f'{name_esc} {ep_esc}' if ep_esc else name_esc,
    ]
    return '\n'.join(lines)

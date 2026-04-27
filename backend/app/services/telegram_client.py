"""Telegram Bot API async client.

Thin wrapper over ``https://api.telegram.org/bot<TOKEN>/...`` focused on
the endpoints this app needs. Surfaces meaningful exceptions so callers
can branch on 403 (blocked by user), 404 (chat not found), etc.
"""

from __future__ import annotations

import logging
import typing as T

import httpx

_log = logging.getLogger(__name__)


class TelegramApiError(Exception):
    """Non-2xx or ``ok: false`` response from the Telegram Bot API."""

    def __init__(self, status_code: int, description: str, error_code: int | None = None) -> None:
        super().__init__(f'Telegram API {status_code}: {description}')
        self.status_code = status_code
        self.description = description
        self.error_code = error_code


class TelegramBotBlockedError(TelegramApiError):
    """Raised when a user blocks the bot (403). Caller should clear binding."""


class TelegramChatNotFoundError(TelegramApiError):
    """Raised when chat_id no longer exists. Caller should clear binding."""


class TelegramClient:
    """Async Telegram Bot API client."""

    def __init__(self, bot_token: str, *, base_url: str = 'https://api.telegram.org') -> None:
        self._base_url = f'{base_url.rstrip("/")}/bot{bot_token}'
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    # --- messages ---

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = 'MarkdownV2',
        reply_markup: dict[str, object] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, object]:
        """POST /sendMessage."""
        payload: dict[str, object] = {
            'chat_id': chat_id,
            'text': text,
            'disable_web_page_preview': disable_web_page_preview,
        }
        if parse_mode is not None:
            payload['parse_mode'] = parse_mode
        if reply_markup is not None:
            payload['reply_markup'] = reply_markup
        return await self._call('sendMessage', payload)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = 'MarkdownV2',
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """POST /editMessageText."""
        payload: dict[str, object] = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
        }
        if parse_mode is not None:
            payload['parse_mode'] = parse_mode
        if reply_markup is not None:
            payload['reply_markup'] = reply_markup
        return await self._call('editMessageText', payload)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        """POST /answerCallbackQuery."""
        payload: dict[str, object] = {
            'callback_query_id': callback_query_id,
            'show_alert': show_alert,
        }
        if text is not None:
            payload['text'] = text
        await self._call('answerCallbackQuery', payload)

    # --- webhook management ---

    async def set_webhook(
        self,
        url: str,
        *,
        secret_token: str,
        allowed_updates: list[str] | None = None,
    ) -> None:
        """POST /setWebhook."""
        payload: dict[str, object] = {
            'url': url,
            'secret_token': secret_token,
        }
        if allowed_updates is not None:
            payload['allowed_updates'] = allowed_updates
        await self._call('setWebhook', payload)

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        """POST /deleteWebhook."""
        await self._call('deleteWebhook', {'drop_pending_updates': drop_pending_updates})

    async def get_webhook_info(self) -> dict[str, object]:
        """POST /getWebhookInfo."""
        return await self._call('getWebhookInfo')

    async def get_me(self) -> dict[str, object]:
        """GET /getMe — used to verify bot token is valid."""
        return await self._call('getMe')

    # --- bot commands (the "/" menu) ---

    async def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        """POST /setMyCommands — populate the bot's "/" menu."""
        await self._call('setMyCommands', {'commands': commands})

    async def delete_my_commands(self) -> None:
        """POST /deleteMyCommands — clear the bot's "/" menu."""
        await self._call('deleteMyCommands')

    # --- internal ---

    async def _call(self, method: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        """POST /<method> and return the 'result' field. Raises TelegramApiError on failure.

        Maps 403 + 'bot was blocked' → TelegramBotBlockedError.
        Maps 400/404 + 'chat not found' → TelegramChatNotFoundError.
        """
        url = f'{self._base_url}/{method}'
        try:
            response = await self._client.post(url, json=payload or {})
        except httpx.RequestError as exc:
            raise TelegramApiError(0, str(exc)) from exc

        # Telegram always returns JSON; status codes are mostly 200.
        try:
            data: dict[str, object] = response.json()
        except Exception as exc:
            raise TelegramApiError(response.status_code, 'invalid JSON response') from exc

        if not data.get('ok'):
            description = str(data.get('description', 'unknown error'))
            error_code: int | None = T.cast(int | None, data.get('error_code'))
            http_status = response.status_code

            description_lower = description.lower()
            if 'blocked by the user' in description_lower or 'bot was blocked' in description_lower:
                raise TelegramBotBlockedError(http_status, description, error_code)
            if 'chat not found' in description_lower:
                raise TelegramChatNotFoundError(http_status, description, error_code)

            raise TelegramApiError(http_status, description, error_code)

        result = data.get('result')
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        # Some methods (e.g. answerCallbackQuery) return True; wrap for consistent typing.
        return {'result': result}


# MarkdownV2 escape helper
def escape_markdown_v2(text: str) -> str:
    """Escape characters per Telegram MarkdownV2 spec.

    Per https://core.telegram.org/bots/api#markdownv2-style, these
    characters must be escaped outside entities:
    _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    _SPECIAL = r'\_*[]()~`>#+-=|{}.!'
    result = []
    for ch in text:
        if ch in _SPECIAL:
            result.append('\\')
        result.append(ch)
    return ''.join(result)

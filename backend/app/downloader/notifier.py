"""Composite notifier — CoolQ / Telebot / Discord / Plex.

Fires enabled notification channels on task completion. Each channel's
failure is logged and swallowed; the remaining channels still fire.

Port of the four notification blocks at the tail of
``Anime.__send_user_notification`` / ``Anime.download`` in the legacy
module.
"""

from __future__ import annotations

import typing as T
import urllib.parse

import requests

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..models import AppSettings
    from .http_client import AniGamerHttpClient


class CompositeNotifier:
    """Dispatches enabled notification channels on download completion."""

    def __init__(
        self,
        settings: AppSettings,
        client: AniGamerHttpClient,
        logger: Logger,
    ) -> None:
        self._settings = settings
        self._client = client
        self._logger = logger

    # ------------------------------------------------------------------ public

    def notify_completed(
        self,
        filename: str,
        size_mb: int,
        sn: int,
    ) -> None:
        """Dispatch every enabled channel. Never raises."""
        if self._settings.coolq_notify:
            self._send_coolq(filename, size_mb, sn)
        if self._settings.telebot_notify:
            self._send_telebot(filename, size_mb, sn)
        if self._settings.discord_notify:
            self._send_discord(filename, size_mb, sn)
        if self._settings.plex_refresh:
            self._refresh_plex(sn)

    # ------------------------------------------------------------------ channels

    def _send_coolq(self, filename: str, size_mb: int, sn: int) -> None:
        try:
            cq = self._settings.coolq_settings
            base_msg = f'【aniGamerPlus消息】\n《{filename}》下載完成, 本集 {size_mb} MB'
            if cq.message_suffix:
                base_msg = base_msg + '\n\n' + cq.message_suffix
            for query_url in cq.query:
                separator = '&' if '?' in query_url else '?'
                full_url = f'{query_url}{separator}{cq.msg_argument_name}={urllib.parse.quote(base_msg)}'
                self._client.get(full_url, no_cookies=True)
        except Exception as exc:  # noqa: BLE001 — swallow by contract
            self._logger.error(
                sn,
                'CQ NOTIFY ERROR',
                f'coolq notification failed: {exc}',
                display=False,
            )

    def _send_telebot(self, filename: str, size_mb: int, sn: int) -> None:
        try:
            token = self._settings.telebot_token
            if not token:
                return
            msg = f'【aniGamerPlus消息】\n《{filename}》下載完成, 本集 {size_mb} MB'
            if self._settings.telebot_use_chat_id and self._settings.telebot_chat_id:
                chat_id = self._settings.telebot_chat_id
            else:
                updates_url = f'https://api.telegram.org/bot{token}/getUpdates'
                payload = self._client.get_json(updates_url, no_cookies=True)
                try:
                    chat_id = str(payload['result'][0]['message']['chat']['id'])
                except (KeyError, IndexError, TypeError) as exc:
                    self._logger.error(
                        sn,
                        'TG NOTIFY ERROR',
                        f'cannot resolve chat_id: {exc}',
                        display=False,
                    )
                    return
            send_url = (
                f'https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={urllib.parse.quote(msg)}'
            )
            self._client.get(send_url, no_cookies=True)
        except Exception as exc:  # noqa: BLE001 — swallow by contract
            self._logger.error(
                sn,
                'TG NOTIFY ERROR',
                f'telebot notification failed: {exc}',
                display=False,
            )

    def _send_discord(self, filename: str, size_mb: int, sn: int) -> None:
        try:
            url = self._settings.discord_token
            if not url:
                return
            msg = f'【aniGamerPlus消息】\n《{filename}》下載完成，本集 {size_mb} MB'
            payload = {
                'content': None,
                'embeds': [
                    {
                        'title': '下載完成',
                        'description': msg,
                        'color': '5814783',
                        'author': {'name': '🔔 動畫瘋'},
                    }
                ],
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code not in (200, 204):
                self._logger.error(
                    sn,
                    'DISCORD NOTIFY ERROR',
                    f'status={response.status_code} body={response.text}',
                    display=False,
                )
        except Exception as exc:  # noqa: BLE001 — swallow by contract
            self._logger.error(
                sn,
                'DISCORD NOTIFY ERROR',
                f'discord notification failed: {exc}',
                display=False,
            )

    def _refresh_plex(self, sn: int) -> None:
        try:
            plex_url = self._settings.plex_url
            section = self._settings.plex_section
            token = self._settings.plex_token
            if not (plex_url and section and token):
                return
            url = f'https://{plex_url}/library/sections/{section}/refresh?X-Plex-Token={token}'
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                self._logger.error(
                    sn,
                    'PLEX REFRESH ERROR',
                    f'status={response.status_code}',
                    display=False,
                )
        except Exception as exc:  # noqa: BLE001 — swallow by contract
            self._logger.error(
                sn,
                'PLEX REFRESH ERROR',
                f'plex refresh failed: {exc}',
                display=False,
            )

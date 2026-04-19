"""m3u8 acquisition flow — port of ``Anime.__get_m3u8_dict``.

The legacy flow is:

1. Generate a device id (``ajax/getdeviceid.php``).
2. Call the ``token.php`` endpoint to learn whether the user is VIP.
3. If not VIP, start the ad clock (``videoCastcishu.php``), sleep
   ``ads_time`` seconds, then ``skip_ad``.
4. Request the playlist (``ajax/m3u8.php``) — this returns the master
   playlist URL.
5. Parse the master playlist into ``{resolution: chunklist_url}``.

The rewrite drops legacy's adaptive ``check_no_ad`` which wrote ad-time
adjustments back to ``config.json`` — that mutation pattern doesn't fit
the new settings model. Ad-time is a static config value now.
"""

from __future__ import annotations

import random
import re
import threading
import time
import typing as T

from . import exceptions

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..models import AppSettings
    from ..persistence.settings_repo import SettingsRepository
    from .http_client import AniGamerHttpClient


_RES_LINE_RE = re.compile(r'=\d+x\d+\n.+')
_RES_KEY_RE = re.compile(r'=\d+x\d+')
_CHUNKLIST_RE = re.compile(r'.*chunklist.+')


class M3u8Client:
    """Fetches the master m3u8 for an sn and returns a resolution map."""

    def __init__(
        self,
        client: AniGamerHttpClient,
        settings: AppSettings,
        settings_repo: SettingsRepository,
        logger: Logger,
    ) -> None:
        self._client = client
        self._settings = settings
        # Kept for parity with the legacy ``check_no_ad`` call site, which
        # wrote ad-time tweaks back to the settings repo. The rewrite does
        # not mutate settings — this reference is intentionally unused.
        self._settings_repo = settings_repo
        self._logger = logger
        self._device_id: str | None = None
        self._parse_lock = threading.Lock()
        self._last_parse_at: dict[int, float] = {}

    # ------------------------------------------------------------------ public

    def fetch(self, sn: int) -> dict[str, str]:
        """Return ``{resolution: m3u8_url}`` for ``sn``.

        The parse_sn_cd cooldown is enforced per-sn through a shared lock;
        callers hitting the same sn in rapid succession will wait the
        remaining window before the next parse proceeds.
        """
        self._enforce_cooldown(sn)

        device_id = self._ensure_device_id()
        user_info = self._gain_access(sn, device_id)
        if 'error' in user_info:
            err = user_info.get('error', {})
            raise exceptions.NoAvailableStreamError(f'sn={sn} token error: code={err.get("code")} {err.get("message")}')

        if not self._settings.use_mobile_api:
            self._unlock(sn)
            self._check_lock(sn, device_id)
            self._unlock(sn)
            self._unlock(sn)

        is_vip = bool(user_info.get('vip'))
        if is_vip:
            self._logger.info(sn, 'VIP', 'cookie 具備 VIP，跳過廣告等待', display=False)
        else:
            if self._settings.only_use_vip:
                raise exceptions.NoAvailableStreamError(f'sn={sn} requires VIP and only_use_vip is enabled')
            ad_time = int(self._settings.mobile_ads_time if self._settings.use_mobile_api else self._settings.ads_time)
            self._logger.info(
                sn,
                '廣告等待',
                f'cookie 無 VIP，等待 {ad_time} 秒廣告',
                display=False,
            )
            self._start_ad(sn)
            time.sleep(max(0, ad_time))
            self._skip_ad(sn)

        if not self._settings.use_mobile_api:
            self._video_start(sn)

        playlist = self._get_playlist(sn, device_id)
        return self._parse_playlist(sn, playlist)

    # ------------------------------------------------------------------ helpers

    def _enforce_cooldown(self, sn: int) -> None:
        cd = int(self._settings.parse_sn_cd)
        if cd <= 0:
            return
        with self._parse_lock:
            last = self._last_parse_at.get(sn)
            now = time.monotonic()
            if last is not None:
                wait = cd - (now - last)
                if wait > 0:
                    time.sleep(wait)
            self._last_parse_at[sn] = time.monotonic()

    def _ensure_device_id(self) -> str:
        if self._device_id:
            return self._device_id
        data = self._client.get_json('https://ani.gamer.com.tw/ajax/getdeviceid.php')
        device_id = str(data.get('deviceid', ''))
        if not device_id:
            raise exceptions.TryTooManyTimeError('getdeviceid.php returned empty deviceid')
        self._device_id = device_id
        return device_id

    def _gain_access(self, sn: int, device_id: str) -> dict[str, T.Any]:
        if self._settings.use_mobile_api:
            url = f'https://ani.gamer.com.tw/ajax/token.php?adID=0&sn={sn}&device={device_id}'
        else:
            url = f'https://ani.gamer.com.tw/ajax/token.php?adID=0&sn={sn}&device={device_id}&hash={_random_string(12)}'
        data = self._client.get_json(url)
        return data if isinstance(data, dict) else {}

    def _unlock(self, sn: int) -> None:
        self._client.get(f'https://ani.gamer.com.tw/ajax/unlock.php?sn={sn}&ttl=0')

    def _check_lock(self, sn: int, device_id: str) -> None:
        self._client.get(f'https://ani.gamer.com.tw/ajax/checklock.php?device={device_id}&sn={sn}')

    def _start_ad(self, sn: int) -> None:
        if self._settings.use_mobile_api:
            url = f'https://api.gamer.com.tw/mobile_app/anime/v1/stat_ad.php?schedule=-1&sn={sn}'
        else:
            url = f'https://ani.gamer.com.tw/ajax/videoCastcishu.php?sn={sn}&s=194699'
        self._client.get(url)

    def _skip_ad(self, sn: int) -> None:
        if self._settings.use_mobile_api:
            url = f'https://api.gamer.com.tw/mobile_app/anime/v1/stat_ad.php?schedule=-1&ad=end&sn={sn}'
        else:
            url = f'https://ani.gamer.com.tw/ajax/videoCastcishu.php?sn={sn}&s=194699&ad=end'
        self._client.get(url)

    def _video_start(self, sn: int) -> None:
        self._client.get(f'https://ani.gamer.com.tw/ajax/videoStart.php?sn={sn}')

    def _get_playlist(self, sn: int, device_id: str) -> dict[str, T.Any]:
        if self._settings.use_mobile_api:
            url = f'https://api.gamer.com.tw/mobile_app/anime/v3/m3u8.php?videoSn={sn}&device={device_id}'
        else:
            url = f'https://ani.gamer.com.tw/ajax/m3u8.php?sn={sn}&device={device_id}'
        data = self._client.get_json(url)
        return data if isinstance(data, dict) else {}

    def _parse_playlist(self, sn: int, playlist: dict[str, T.Any]) -> dict[str, str]:
        if self._settings.use_mobile_api:
            playlist_url = (playlist.get('data') or {}).get('src', '')
        else:
            playlist_url = playlist.get('src', '')
        if not playlist_url:
            raise exceptions.NoAvailableStreamError(f'sn={sn} playlist missing src')

        response = self._client.get(
            playlist_url,
            no_cookies=True,
            extra_headers={'origin': 'https://ani.gamer.com.tw'},
        )
        text = response.content.decode('utf-8', errors='replace')
        url_prefix = re.sub(r'playlist.+', '', playlist_url)

        out: dict[str, str] = {}
        for line in _RES_LINE_RE.findall(text):
            key_match = _RES_KEY_RE.search(line)
            chunk_match = _CHUNKLIST_RE.search(line)
            if key_match is None or chunk_match is None:
                continue
            # key is ``=WxH`` → take ``H`` as the vertical resolution
            vertical = re.findall(r'x\d+', key_match.group(0))
            if not vertical:
                continue
            key = vertical[0][1:]
            out[key] = url_prefix + chunk_match.group(0)
        return out


def _random_string(length: int) -> str:
    chars = 'abcdefghijklmnopqrstuvwxyz0123456789'
    rnd = random.Random(int(round(time.time() * 1000)))
    return ''.join(chars[rnd.randint(0, len(chars) - 1)] for _ in range(length))

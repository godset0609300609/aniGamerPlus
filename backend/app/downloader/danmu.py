"""Danmu renderer — ``.ass`` output, drop-in replacement for legacy ``Danmu.py``.

Keeps the same rendering conventions (style ids, channel packing, BGR
colour conversion) but drives them through the new DI-friendly HTTP
client and :class:`WorkspacePaths` rather than the module-level globals
the legacy code used.

Danmu download is best-effort: any failure logs a warning and returns
cleanly without surfacing to the caller — the episode is already on disk
by the time this runs.
"""

from __future__ import annotations

import collections.abc
import importlib.resources
import json
import pathlib
import random
import re
import typing as T

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from .http_client import AniGamerHttpClient


_ROLL_SPEED_MIN = 10
_ROLL_SPEED_MAX = 14

# The template is a package-shipped asset (not user data), loaded via
# importlib.resources so it travels with the wheel regardless of cwd.
_TEMPLATE_PACKAGE = 'app.downloader.assets'
_TEMPLATE_RESOURCE = 'danmu_template.ass'


class DanmuRenderer:
    """Fetches the ajax/danmuGet.php JSON and renders an ``.ass`` file."""

    TEMPLATE_RESOURCE: T.ClassVar[str] = _TEMPLATE_RESOURCE

    def __init__(
        self,
        client: AniGamerHttpClient,
        logger: Logger,
    ) -> None:
        self._client = client
        self._logger = logger
        self._cached_header: str | None = None

    # ------------------------------------------------------------------ public

    def render(
        self,
        sn: int,
        full_filename: pathlib.Path,
        ban_words: collections.abc.Sequence[str] = (),
    ) -> None:
        """Render danmu for ``sn`` next to ``full_filename`` (``.ass`` suffix).

        Always a best-effort call — the caller should never have to catch.
        """
        out_path = pathlib.Path(full_filename).with_suffix('.ass')
        try:
            danmu_list = self._fetch_danmu(sn)
            ban_list = list(ban_words) + self._fetch_online_ban_words(sn)
            self._write_ass(out_path, danmu_list, ban_list)
        except Exception as exc:  # noqa: BLE001 — best-effort by contract
            self._logger.error(sn, '彈幕異常', f'danmu render failed: {exc}', display=False)
            return

    # ------------------------------------------------------------------ fetch

    def _fetch_danmu(self, sn: int) -> list[dict[str, T.Any]]:
        # legacy used POST urlencoded; we stick with GET-style JSON via the
        # shared client for consistency. The server accepts either.
        url = 'https://ani.gamer.com.tw/ajax/danmuGet.php?sn=' + str(sn)
        response = self._client.get(url, no_cookies=True)
        if response.status_code != 200:
            self._logger.error(
                sn,
                '彈幕下載失敗',
                f'status_code={response.status_code}',
                display=False,
            )
            return []
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _fetch_online_ban_words(self, sn: int) -> list[str]:
        url = 'https://ani.gamer.com.tw/ajax/keywordGet.php'
        try:
            response = self._client.get(url)
        except Exception:  # noqa: BLE001 — defensive
            return []
        if response.status_code != 200:
            self._logger.error(
                sn,
                '取得線上過濾彈幕失敗',
                f'status_code={response.status_code}',
                display=False,
            )
            return []
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [entry['keyword'] for entry in data if 'keyword' in entry]

    # ------------------------------------------------------------------ render

    def _load_template_header(self) -> str:
        if self._cached_header is not None:
            return self._cached_header
        try:
            resource = importlib.resources.files(_TEMPLATE_PACKAGE).joinpath(_TEMPLATE_RESOURCE)
            self._cached_header = resource.read_text(encoding='utf-8')
        except FileNotFoundError, ModuleNotFoundError, OSError:
            self._cached_header = ''
        return self._cached_header

    def _write_ass(
        self,
        out_path: pathlib.Path,
        danmu_list: list[dict[str, T.Any]],
        ban_words: list[str],
    ) -> None:
        header = self._load_template_header()

        ban_re: re.Pattern[str] | None = None
        if ban_words:
            ban_re = re.compile('|'.join(re.escape(w) for w in ban_words), re.IGNORECASE)

        roll_channel: list[float] = []
        roll_time: list[int] = []
        lines: list[str] = [header]

        rnd = random.Random(0)

        for danmu in danmu_list:
            text = str(danmu.get('text', ''))
            if ban_re is not None and _matches(ban_re, text):
                continue

            raw_time = int(danmu.get('time', 0))
            start_time = raw_time // 10
            hundred_ms = raw_time % 10
            m, s = divmod(start_time, 60)
            h, m = divmod(m, 60)
            start_stamp = f'{h:d}:{m:02d}:{s:02d}.{hundred_ms:d}0'

            colour_field = str(danmu.get('color', '#FFFFFF'))
            bgr = self._rgb_to_bgr(colour_field[1:]) if len(colour_field) >= 7 else 'FFFFFF'

            position = int(danmu.get('position', 0))
            if position == 0:  # Roll
                end_time, height = _allocate_roll(raw_time, start_time, text, roll_channel, roll_time, rnd)
                m2, s2 = divmod(end_time, 60)
                h2, m2 = divmod(m2, 60)
                end_stamp = f'{h2:d}:{m2:02d}:{s2:02d}.{hundred_ms:d}0'
                style = f'Roll,,0,0,0,,{{\\move(1920,{height},-1000,{height})\\1c&H4C{bgr}}}'
            elif position == 1:  # Top
                end_time = start_time + 5
                m2, s2 = divmod(end_time, 60)
                h2, m2 = divmod(m2, 60)
                end_stamp = f'{h2:d}:{m2:02d}:{s2:02d}.{hundred_ms:d}0'
                style = f'Top,,0,0,0,,{{\\1c&H4C{bgr}}}'
            else:  # Bottom
                end_time = start_time + 5
                m2, s2 = divmod(end_time, 60)
                h2, m2 = divmod(m2, 60)
                end_stamp = f'{h2:d}:{m2:02d}:{s2:02d}.{hundred_ms:d}0'
                style = f'Bottom,,0,0,0,,{{\\1c&H4C{bgr}}}'

            lines.append(f'Dialogue: 0,{start_stamp},{end_stamp},{style}{text}\n')

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(''.join(lines), encoding='utf-8')

    @staticmethod
    def _rgb_to_bgr(rgb_hex: str) -> str:
        """Convert ``RRGGBB`` → ``BBGGRR``."""
        if len(rgb_hex) < 6:
            return 'FFFFFF'
        r, g, b = rgb_hex[0:2], rgb_hex[2:4], rgb_hex[4:6]
        return f'{b}{g}{r}'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _matches(pattern: re.Pattern[str], text: str) -> bool:
    match = pattern.search(text)
    return bool(match and match.group(0))


def _allocate_roll(
    raw_time: int,
    start_time: int,
    text: str,
    roll_channel: list[float],
    roll_time: list[int],
    rnd: random.Random,
) -> tuple[int, int]:
    """Legacy's rolling-row packer. Returns ``(end_time, height)``."""
    height = 0
    end_time = 0
    for i, channel_free_at in enumerate(roll_channel):
        if channel_free_at <= raw_time:
            height = i * 54 + 27
            roll_channel[i] = raw_time + (len(text) * roll_time[i]) / 8 + 1
            end_time = start_time + roll_time[i]
            break
    if height == 0:
        roll_channel.append(0.0)
        roll_time.append(rnd.randint(_ROLL_SPEED_MIN, _ROLL_SPEED_MAX))
        roll_channel[-1] = raw_time + (len(text) * roll_time[-1]) / 8 + 1
        height = len(roll_channel) * 54 - 27
        end_time = start_time + roll_time[-1]
    return end_time, height

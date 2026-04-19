"""Metadata extraction — port of legacy ``Anime.__get_*`` helpers.

The metadata layer turns a bare ``sn`` into an :class:`AnimeMetadata`
record that the filename / m3u8 / danmu clients all read from. It owns
the two parser branches (Web HTML and Mobile JSON) and the small amount
of string-cleanup that used to live inline on the ``Anime`` instance.
"""

from __future__ import annotations

import dataclasses
import re
import typing as T

import bs4

from . import exceptions

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..models import AppSettings
    from .http_client import AniGamerHttpClient


_SEASON_TITLE_FILTER = re.compile(r'第[零一二三四五六七八九十]{1,3}季$')
_EXTRA_TITLE_FILTER = re.compile(r'\[(特別篇|中文配音)\]$')


@dataclasses.dataclass(slots=True)
class AnimeMetadata:
    """Every extracted fact about a single sn."""

    sn: int
    title: str  # page title incl. episode info
    bangumi_name: str  # cleaned series name
    bangumi_name_orig: str  # pre-clean series name
    episode: str  # e.g. "01", "特別篇"
    episode_list: dict[str, int]  # {episode_label: sn}
    is_vip_only: bool = False


class MetadataExtractor:
    """Fetches the animeVideo.php page (or mobile API) and parses it."""

    def __init__(
        self,
        client: AniGamerHttpClient,
        settings: AppSettings,
        logger: Logger,
    ) -> None:
        self._client = client
        self._settings = settings
        self._logger = logger

    # ------------------------------------------------------------------ public

    def fetch(self, sn: int) -> AnimeMetadata:
        if self._settings.use_mobile_api:
            return self._fetch_mobile(sn)
        return self._fetch_web(sn)

    # ------------------------------------------------------------------ web

    def _fetch_web(self, sn: int) -> AnimeMetadata:
        url = f'https://ani.gamer.com.tw/animeVideo.php?sn={sn}'
        response = self._client.get(
            url,
            no_cookies=True,
            use_pyhttpx=True,
            extra_headers=self._client.build_web_headers(sn),
        )
        if response.status_code == 404:
            raise exceptions.NoAvailableStreamError(f'sn={sn} returned 404 — deleted or invalid')

        soup = bs4.BeautifulSoup(response.content, 'lxml')
        title = _extract_web_title(soup)
        if title is None:
            body_len = len(response.content) if response.content else 0
            snippet = (response.text or '')[:200].replace('\n', ' ').strip()
            raise exceptions.NoAvailableStreamError(
                f'sn={sn} parse failed '
                f'(status={response.status_code}, body_len={body_len}): '
                f'page structure unexpected. First 200 chars: {snippet!r}'
            )

        episode = _extract_web_episode(soup, title)
        bangumi_name_orig = _strip_episode_marker(title, episode)
        bangumi_name = _clean_bangumi_name(bangumi_name_orig)
        episode_list = _extract_web_episode_list(soup, sn, episode)
        is_vip_only = _web_is_vip_only(soup)

        return AnimeMetadata(
            sn=sn,
            title=title,
            bangumi_name=bangumi_name,
            bangumi_name_orig=bangumi_name_orig,
            episode=episode,
            episode_list=episode_list,
            is_vip_only=is_vip_only,
        )

    # ------------------------------------------------------------------ mobile

    def _fetch_mobile(self, sn: int) -> AnimeMetadata:
        url = f'https://api.gamer.com.tw/mobile_app/anime/v4/video.php?sn={sn}'
        data = self._client.get_json(
            url,
            no_cookies=True,
            extra_headers=self._client.build_mobile_headers(),
        )

        anime = (data or {}).get('data', {}).get('anime')
        if not anime:
            raise exceptions.NoAvailableStreamError(f'sn={sn} mobile API returned no anime payload')

        title = anime.get('title')
        if not title:
            raise exceptions.NoAvailableStreamError(f'sn={sn} mobile payload missing title')

        episode = _extract_mobile_episode(title)
        bangumi_name_orig = _strip_episode_marker(title, episode)
        bangumi_name = _clean_bangumi_name(bangumi_name_orig)
        episode_list = _extract_mobile_episode_list(anime)
        is_vip_only = bool(anime.get('vip_only') or anime.get('isVip'))

        return AnimeMetadata(
            sn=sn,
            title=title,
            bangumi_name=bangumi_name,
            bangumi_name_orig=bangumi_name_orig,
            episode=episode,
            episode_list=episode_list,
            is_vip_only=is_vip_only,
        )


# ---------------------------------------------------------------------------
# helpers — web branch
# ---------------------------------------------------------------------------


def _strip_page_title_suffixes(text: str) -> str:
    """Strip site-specific suffixes from og:title / <title> strings."""
    out = text.strip()
    # Strip "- 巴哈姆特動畫瘋" site suffix (including en-dash variant)
    out = re.sub(r'\s*[-–]\s*巴哈姆特動畫瘋\s*$', '', out)
    # Strip "線上看" tail
    out = re.sub(r'\s*線上看\s*$', '', out)
    return out.strip()


def _extract_web_title(soup: bs4.BeautifulSoup) -> str | None:
    """Extract the episode title using multiple fallback selectors.

    1. Primary: ``<div class="anime_name"><h1>`` (legacy path)
    2. Fallback 1: ``<meta property="og:title">`` — strip site suffixes
    3. Fallback 2: ``<title>`` tag — strip site suffixes
    """
    # 1. Primary
    holder = soup.find('div', class_='anime_name')
    if holder is not None:
        h1 = holder.find('h1') if hasattr(holder, 'find') else None
        if h1 is not None:
            text = h1.string or h1.get_text()
            if text and text.strip():
                return str(text).strip()

    # 2. og:title fallback
    og = soup.find('meta', attrs={'property': 'og:title'})
    if og is not None:
        content = og.get('content', '')
        if content:
            cleaned = _strip_page_title_suffixes(str(content))
            if cleaned:
                return cleaned

    # 3. <title> tag fallback
    if soup.title and soup.title.string:
        cleaned = _strip_page_title_suffixes(str(soup.title.string))
        if cleaned:
            return cleaned

    return None


def _extract_web_episode(soup: bs4.BeautifulSoup, title: str) -> str:
    """Legacy ``__get_episode`` for the web branch."""
    playing = soup.find('li', class_='playing')
    if playing is not None and hasattr(playing, 'find'):
        anchor = playing.find('a')
        if anchor is not None and anchor.string:
            return str(anchor.string).strip()
    return _episode_from_title(title)


def _extract_web_episode_list(soup: bs4.BeautifulSoup, sn: int, current_episode: str) -> dict[str, int]:
    """Legacy ``__get_episode_list`` for the web branch."""
    season = soup.find('section', class_='season')
    if season is None or not hasattr(season, 'find_all'):
        return {current_episode: sn}

    anchors = season.find_all('a')
    paragraphs = season.find_all('p')

    p_labels: list[str] = []
    if paragraphs:
        for p in paragraphs:
            contents = p.contents if hasattr(p, 'contents') else []
            p_labels.append(str(contents[0]) if contents else '')

    result: dict[str, int] = {}
    index_counter: dict[str, int] = {}
    for anchor in anchors:
        href = str(anchor.get('href') or '')
        try:
            a_sn = int(href.replace('?sn=', ''))
        except ValueError:
            continue
        ep = str(anchor.string) if anchor.string else ''
        if not ep:
            continue
        if ep not in index_counter:
            index_counter[ep] = 0
        if ep in result:
            index_counter[ep] += 1
            prefix = p_labels[index_counter[ep]] if index_counter[ep] < len(p_labels) else ''
            ep = prefix + ep
        result[ep] = a_sn
    if not result:
        result[current_episode] = sn
    return result


def _web_is_vip_only(soup: bs4.BeautifulSoup) -> bool:
    """Best-effort guess at whether this episode requires VIP.

    Legacy had no structured indicator — it relied on the m3u8 unlock
    step returning an ``error`` payload. The mobile API and some web
    pages do include a marker in the DOM (``.anime_vip`` / text ``VIP``)
    which we pick up when present.
    """
    return bool(soup.find(attrs={'class': re.compile(r'(?i)vip_only|vip-only|anime_vip')}))


# ---------------------------------------------------------------------------
# helpers — mobile branch
# ---------------------------------------------------------------------------


def _extract_mobile_episode(title: str) -> str:
    return _episode_from_title(title)


def _extract_mobile_episode_list(anime: dict[str, T.Any]) -> dict[str, int]:
    """Legacy ``__get_episode_list`` for the mobile branch."""
    episodes = anime.get('episodes') or {}
    result: dict[str, int] = {}
    for type_key, items in episodes.items():
        for item in items or []:
            video_sn = int(item.get('videoSn', 0))
            ep_label = str(item.get('episode', ''))
            if type_key == '0':  # 本篇
                result[ep_label] = video_sn
            elif type_key == '1':  # 電影
                result['電影'] = video_sn
            elif type_key == '2':  # 特別篇
                result[f'特別篇{ep_label}'] = video_sn
            elif type_key == '3':  # 中文配音
                result[f'中文配音{ep_label}'] = video_sn
            else:
                result['中文電影'] = video_sn
    return result


# ---------------------------------------------------------------------------
# helpers — shared string cleanup
# ---------------------------------------------------------------------------


def _episode_from_title(title: str) -> str:
    """Fallback episode extractor pulled from the bracketed suffix.

    Port of ``Anime.__get_episode.get_ep``.
    """
    numeric: list[str] = re.findall(r'\[\d*\.?\d* *\.?[A-Z,a-z]*(?:電影)?\]', title)
    if numeric:
        return numeric[0][1:-1]
    bracketed: list[str] = re.findall(r'\[.+?\]', title)
    if bracketed:
        return bracketed[0][1:-1]
    return '1'


def _strip_episode_marker(title: str, episode: str) -> str:
    """Remove the ``[episode]`` suffix from a title and squash runs of spaces."""
    without = title.replace(f'[{episode}]', '').strip()
    return re.sub(r'\s+', ' ', without)


def _clean_bangumi_name(bangumi_name_orig: str) -> str:
    """Port the season_title + extra_title regex cleanup."""
    out = _SEASON_TITLE_FILTER.sub('', bangumi_name_orig).strip()
    out = _EXTRA_TITLE_FILTER.sub('', out).strip()
    return re.sub(r'\s+', ' ', out)

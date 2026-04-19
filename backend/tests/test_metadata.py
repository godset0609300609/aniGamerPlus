"""Tests for ``MetadataExtractor``.

No real network access is available in CI, so the HTML fixtures below are
synthesised by hand — they mirror the structure legacy ``Anime.__get_src``
parsed (``<div class="anime_name"><h1>`` for the title, ``<li
class="playing"><a>`` for the current episode, ``<section class="season">``
for the episode list) but are NOT scraped from 動畫瘋. If 動畫瘋 ever
changes its markup shape, these fixtures must be regenerated against a
real page capture; until then they're the best proxy available.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any
from unittest import mock

import pytest

import bs4

from app.downloader import exceptions
from app.downloader.http_client import AniGamerHttpClient
from app.downloader.metadata import (
    AnimeMetadata,
    MetadataExtractor,
    _extract_web_title,
    _strip_page_title_suffixes,
)
from app.logging_ import Logger
from app.models import AppSettings
from app.persistence.cookie_repo import CookieRepository
from app.persistence.paths import WorkspacePaths


@dataclasses.dataclass
class _FakeResponse:
    status_code: int = 200
    text: str = ''
    content: bytes = b''
    cookies: dict[str, str] = dataclasses.field(default_factory=dict)
    headers: dict[str, str] = dataclasses.field(default_factory=dict)

    def json(self) -> Any:
        import json

        return json.loads(self.text or 'null')


def _html(title: str, *, with_list: bool = False, with_playing: bool = True) -> bytes:
    """Return an HTML snippet matching the legacy parser's expected shape."""
    li_playing = '<li class="playing"><a href="?sn=12345">01</a></li>' if with_playing else ''
    season_section = ''
    if with_list:
        season_section = """
        <section class="season">
          <ul>
            <li class="playing"><a href="?sn=12345">01</a></li>
            <a href="?sn=12346">02</a>
            <a href="?sn=12347">03</a>
          </ul>
        </section>
        """
    return (
        f"""
        <html>
          <body>
            <div class="anime_name"><h1>{title}</h1></div>
            {li_playing}
            {season_section}
          </body>
        </html>
        """
    ).encode('utf-8')


@pytest.fixture
def paths(tmp_path: pathlib.Path) -> WorkspacePaths:
    return WorkspacePaths.detect(working_dir=tmp_path)


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture
def http_client(paths: WorkspacePaths, logger: Logger) -> AniGamerHttpClient:
    settings = AppSettings(ua='Mozilla/5.0')
    cookies = CookieRepository(paths, logger)
    return AniGamerHttpClient(settings, cookies, logger)


def _extractor(
    http_client: AniGamerHttpClient,
    logger: Logger,
    *,
    use_mobile_api: bool = False,
) -> MetadataExtractor:
    settings = AppSettings(ua='Mozilla/5.0', use_mobile_api=use_mobile_api)
    return MetadataExtractor(http_client, settings, logger)


def test_web_branch_happy_path(http_client: AniGamerHttpClient, logger: Logger) -> None:
    fake = _FakeResponse(content=_html('測試番劇[01]', with_list=True))
    extractor = _extractor(http_client, logger)

    with mock.patch.object(http_client, 'get', return_value=fake):
        meta = extractor.fetch(12345)

    assert isinstance(meta, AnimeMetadata)
    assert meta.sn == 12345
    assert meta.title == '測試番劇[01]'
    assert meta.bangumi_name == '測試番劇'
    assert meta.bangumi_name_orig == '測試番劇'
    assert meta.episode == '01'
    assert meta.episode_list['01'] == 12345
    assert meta.episode_list['02'] == 12346


def test_web_branch_season_title_filter(http_client: AniGamerHttpClient, logger: Logger) -> None:
    fake = _FakeResponse(content=_html('某某番 第二季[01]'))
    extractor = _extractor(http_client, logger)

    with mock.patch.object(http_client, 'get', return_value=fake):
        meta = extractor.fetch(1)

    assert meta.bangumi_name_orig == '某某番 第二季'
    assert meta.bangumi_name == '某某番'


def test_web_branch_extra_title_filter(http_client: AniGamerHttpClient, logger: Logger) -> None:
    # Playing-li anchors "01"; title has the episode suffix at the end.
    fake = _FakeResponse(content=_html('某番[特別篇][01]'))
    extractor = _extractor(http_client, logger)

    with mock.patch.object(http_client, 'get', return_value=fake):
        meta = extractor.fetch(1)

    assert meta.bangumi_name_orig == '某番[特別篇]'
    assert meta.bangumi_name == '某番'


def test_mobile_branch_happy_path(http_client: AniGamerHttpClient, logger: Logger) -> None:
    payload: dict[str, Any] = {
        'data': {
            'anime': {
                'title': '移動番[01]',
                'episodes': {
                    '0': [
                        {'episode': '01', 'videoSn': 11111},
                        {'episode': '02', 'videoSn': 11112},
                    ],
                    '2': [
                        {'episode': '01', 'videoSn': 22222},
                    ],
                },
            }
        }
    }
    extractor = _extractor(http_client, logger, use_mobile_api=True)

    with mock.patch.object(http_client, 'get_json', return_value=payload):
        meta = extractor.fetch(11111)

    assert meta.title == '移動番[01]'
    assert meta.episode == '01'
    assert meta.bangumi_name == '移動番'
    assert meta.episode_list == {
        '01': 11111,
        '02': 11112,
        '特別篇01': 22222,
    }


def test_web_branch_404_raises_no_available_stream(http_client: AniGamerHttpClient, logger: Logger) -> None:
    fake = _FakeResponse(status_code=404, content=b'<html/>')
    extractor = _extractor(http_client, logger)

    with mock.patch.object(http_client, 'get', return_value=fake):
        with pytest.raises(exceptions.NoAvailableStreamError):
            extractor.fetch(999999)


def test_web_branch_missing_title_raises(http_client: AniGamerHttpClient, logger: Logger) -> None:
    fake = _FakeResponse(content=b'<html><body><p>empty</p></body></html>')
    extractor = _extractor(http_client, logger)

    with mock.patch.object(http_client, 'get', return_value=fake):
        with pytest.raises(exceptions.NoAvailableStreamError):
            extractor.fetch(1)


# ---------------------------------------------------------------------------
# _strip_page_title_suffixes unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'raw, expected',
    [
        ('foo 線上看', 'foo'),
        ('foo - 巴哈姆特動畫瘋', 'foo'),
        ('foo 線上看 - 巴哈姆特動畫瘋', 'foo'),
        ('foo', 'foo'),
        ('拉拉熊 [1] 線上看 - 巴哈姆特動畫瘋', '拉拉熊 [1]'),
    ],
)
def test_strip_page_title_suffixes(raw: str, expected: str) -> None:
    assert _strip_page_title_suffixes(raw) == expected


# ---------------------------------------------------------------------------
# _extract_web_title fallback tests
# ---------------------------------------------------------------------------


def test_extract_web_title_uses_og_title_fallback() -> None:
    """No anime_name div but og:title present — should return cleaned title."""
    html = '<html><head><meta property="og:title" content="拉拉熊 [1] 線上看"/></head><body></body></html>'
    soup = bs4.BeautifulSoup(html, 'lxml')
    assert _extract_web_title(soup) == '拉拉熊 [1]'


def test_extract_web_title_uses_title_tag_fallback() -> None:
    """No anime_name div or og:title — falls back to <title> tag."""
    html = '<html><head><title>拉拉熊 [1] 線上看 - 巴哈姆特動畫瘋</title></head><body></body></html>'
    soup = bs4.BeautifulSoup(html, 'lxml')
    assert _extract_web_title(soup) == '拉拉熊 [1]'


# ---------------------------------------------------------------------------
# _fetch_web diagnostic error message test
# ---------------------------------------------------------------------------


def test_fetch_web_missing_title_gives_diagnostic_error(http_client: AniGamerHttpClient, logger: Logger) -> None:
    """200 response with no title elements should raise NoAvailableStreamError
    with status, body_len, and 'page structure unexpected' in the message."""
    body = b'<html><body></body></html>'
    fake = _FakeResponse(
        status_code=200,
        content=body,
        text=body.decode('utf-8'),
    )
    extractor = _extractor(http_client, logger)

    with mock.patch.object(http_client, 'get', return_value=fake):
        with pytest.raises(exceptions.NoAvailableStreamError) as exc_info:
            extractor.fetch(48430)

    msg = str(exc_info.value)
    assert 'status=200' in msg
    assert 'body_len=' in msg
    assert 'page structure unexpected' in msg
    # Must NOT use the old misleading message
    assert 'episode may be deleted' not in msg

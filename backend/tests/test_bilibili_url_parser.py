"""Tests for bilibili URL parser."""

from __future__ import annotations

import unittest.mock

import pytest

from app.downloader.bilibili.url_parser import _aid_to_bv, _bv_to_aid, parse_bilibili_input


def test_bv_to_aid_roundtrip() -> None:
    bvid = 'BV1xx411c7mD'
    aid = _bv_to_aid(bvid)
    assert _aid_to_bv(aid) == bvid


def test_parse_full_url_bv() -> None:
    bvid, aid, multi = parse_bilibili_input('https://www.bilibili.com/video/BV1xx411c7mD')
    assert bvid == 'BV1xx411c7mD'
    assert aid > 0
    assert multi is False


def test_parse_raw_bvid() -> None:
    bvid, aid, multi = parse_bilibili_input('BV1xx411c7mD')
    assert bvid == 'BV1xx411c7mD'
    assert multi is False


def test_parse_raw_bvid_with_whitespace() -> None:
    bvid, aid, multi = parse_bilibili_input('  BV1xx411c7mD  ')
    assert bvid == 'BV1xx411c7mD'


def test_parse_av_string() -> None:
    bvid, aid, multi = parse_bilibili_input('av170001')
    assert aid == 170001
    assert bvid.startswith('BV')
    assert multi is False


def test_parse_av_url() -> None:
    bvid, aid, multi = parse_bilibili_input('https://www.bilibili.com/video/av170001')
    assert aid == 170001


def test_parse_b23_short_url() -> None:
    final_url = 'https://www.bilibili.com/video/BV1xx411c7mD'
    with unittest.mock.patch('app.downloader.bilibili.url_parser.requests.head') as mock_head:
        mock_resp = unittest.mock.MagicMock()
        mock_resp.url = final_url
        mock_head.return_value = mock_resp
        bvid, aid, multi = parse_bilibili_input('https://b23.tv/AbCdEf')
    assert bvid == 'BV1xx411c7mD'
    mock_head.assert_called_once()


def test_parse_b23_without_scheme() -> None:
    final_url = 'https://www.bilibili.com/video/BV1xx411c7mD'
    with unittest.mock.patch('app.downloader.bilibili.url_parser.requests.head') as mock_head:
        mock_resp = unittest.mock.MagicMock()
        mock_resp.url = final_url
        mock_head.return_value = mock_resp
        bvid, _aid, _multi = parse_bilibili_input('b23.tv/AbCdEf')
    assert bvid == 'BV1xx411c7mD'
    call_args = mock_head.call_args[0][0]
    assert call_args.startswith('https://')


def test_parse_invalid_raises() -> None:
    with pytest.raises(ValueError, match='Cannot extract'):
        parse_bilibili_input('https://www.bilibili.com/garbage')


def test_parse_empty_raises() -> None:
    with pytest.raises(ValueError):
        parse_bilibili_input('')


# ---------------------------------------------------------------------------
# SSRF regression — b23.tv substring-match bypass (fix #5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    's',
    [
        'evil.com/b23.tv/something',
        '169.254.169.254/b23.tv/x',
        'http://169.254.169.254/b23.tv/x',
        'redis/b23.tv/x',
        'https://evil.com/redirect?u=b23.tv/x',
    ],
)
def test_parse_rejects_b23_substring_bypass(s: str) -> None:
    """A ``re.search``-style bypass must not reach ``requests.head`` at all."""
    with (
        unittest.mock.patch('app.downloader.bilibili.url_parser.requests.head') as mock_head,
        pytest.raises(ValueError),
    ):
        parse_bilibili_input(s)
    mock_head.assert_not_called()


def test_parse_b23_userinfo_bypass_rejected() -> None:
    """``b23.tv@evil.com`` must not be treated as a b23.tv short link."""
    with (
        unittest.mock.patch('app.downloader.bilibili.url_parser.requests.head') as mock_head,
        pytest.raises(ValueError),
    ):
        parse_bilibili_input('https://b23.tv@evil.com/x')
    mock_head.assert_not_called()


def test_resolve_b23_rejects_url_blocked_by_ssrf_guard() -> None:
    with (
        unittest.mock.patch(
            'app.downloader.bilibili.url_parser.is_safe_public_url', return_value=(False, 'private IP')
        ),
        unittest.mock.patch('app.downloader.bilibili.url_parser.requests.head') as mock_head,
        pytest.raises(ValueError, match='SSRF guard'),
    ):
        parse_bilibili_input('https://b23.tv/AbCdEf')
    mock_head.assert_not_called()


def test_parse_b23_exact_match_still_resolves() -> None:
    """Regression guard against over-tightening: a genuine b23.tv link still works."""
    final_url = 'https://www.bilibili.com/video/BV1xx411c7mD'
    with unittest.mock.patch('app.downloader.bilibili.url_parser.requests.head') as mock_head:
        mock_resp = unittest.mock.MagicMock()
        mock_resp.url = final_url
        mock_head.return_value = mock_resp
        bvid, _aid, _multi = parse_bilibili_input('https://b23.tv/AbCdEf')
    assert bvid == 'BV1xx411c7mD'
    mock_head.assert_called_once()

"""Tests for FilterMatcher."""

from __future__ import annotations

from app.bt_downloader.filter_matcher import FilterMatcher
from app.models import BtFilter


def _matcher() -> FilterMatcher:
    return FilterMatcher()


def test_match_all_keywords_present_matches() -> None:
    matcher = _matcher()
    filters = [BtFilter(name='f1', keywords=['LoliHouse', '1080'])]
    result = matcher.match('[LoliHouse] Some Show - 01 [1080p]', filters, hanzi_convert=False)
    assert result is not None
    assert result.name == 'f1'


def test_match_missing_one_keyword_does_not_match() -> None:
    matcher = _matcher()
    filters = [BtFilter(name='f1', keywords=['LoliHouse', '720'])]
    result = matcher.match('[LoliHouse] Some Show - 01 [1080p]', filters, hanzi_convert=False)
    assert result is None


def test_match_is_case_insensitive() -> None:
    matcher = _matcher()
    filters = [BtFilter(name='f1', keywords=['lolihouse'])]
    result = matcher.match('[LOLIHOUSE] Some Show - 01', filters, hanzi_convert=False)
    assert result is not None


def test_match_no_filters_returns_none() -> None:
    matcher = _matcher()
    result = matcher.match('Any Title', [], hanzi_convert=False)
    assert result is None


def test_match_empty_keywords_list_does_not_match() -> None:
    """A filter with zero keywords must never match — vacuous AND would
    otherwise make it match every title, which is never the intent."""
    matcher = _matcher()
    filters = [BtFilter(name='empty', keywords=[])]
    result = matcher.match('Any Title At All', filters, hanzi_convert=False)
    assert result is None


def test_match_disabled_filters_are_ignored() -> None:
    matcher = _matcher()
    filters = [BtFilter(name='disabled', keywords=['Show'], enabled=False)]
    result = matcher.match('Some Show - 01', filters, hanzi_convert=False)
    assert result is None


def test_match_sort_order_determines_first_match() -> None:
    matcher = _matcher()
    filters = [
        BtFilter(name='second', keywords=['Show'], sort_order=1),
        BtFilter(name='first', keywords=['Show'], sort_order=0),
    ]
    result = matcher.match('Some Show - 01', filters, hanzi_convert=False)
    assert result is not None
    assert result.name == 'first'


def test_match_falls_through_to_next_filter_when_first_does_not_match() -> None:
    matcher = _matcher()
    filters = [
        BtFilter(name='no-match', keywords=['DoesNotAppear'], sort_order=0),
        BtFilter(name='matches', keywords=['Show'], sort_order=1),
    ]
    result = matcher.match('Some Show - 01', filters, hanzi_convert=False)
    assert result is not None
    assert result.name == 'matches'


def test_match_multiple_keywords_all_must_appear_and_order_agnostic() -> None:
    matcher = _matcher()
    filters = [BtFilter(name='f1', keywords=['1080', 'LoliHouse', '繁體'])]
    title = '[LoliHouse] Some Show - 01 [1080p][繁體内嵌]'
    result = matcher.match(title, filters, hanzi_convert=False)
    assert result is not None


# ---------------------------------------------------------------------------
# hanzi_convert
# ---------------------------------------------------------------------------


def test_hanzi_convert_true_matches_simplified_title_against_traditional_keyword() -> None:
    matcher = _matcher()
    filters = [BtFilter(name='f1', keywords=['測試'])]  # traditional
    title = '简体标题测试内容'  # simplified, contains 简体 for "测试" (simplified "test")
    result = matcher.match(title, filters, hanzi_convert=True)
    assert result is not None


def test_hanzi_convert_false_does_not_match_simplified_title_against_traditional_keyword() -> None:
    matcher = _matcher()
    filters = [BtFilter(name='f1', keywords=['測試'])]  # traditional
    title = '简体标题测试内容'  # simplified
    result = matcher.match(title, filters, hanzi_convert=False)
    assert result is None


def test_hanzi_convert_true_still_matches_when_both_already_traditional() -> None:
    matcher = _matcher()
    filters = [BtFilter(name='f1', keywords=['測試'])]
    title = '繁體標題測試內容'
    result = matcher.match(title, filters, hanzi_convert=True)
    assert result is not None

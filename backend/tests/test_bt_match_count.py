"""Tests for ``BtDownloaderService.count_matching``.

Mirrors :class:`~app.bt_downloader.filter_matcher.FilterMatcher`'s AND-keyword
/ hanzi-convert semantics over a batch of stored entries instead of a single
title. Every collaborator is a hand-written fake; the real ``FilterMatcher``
is used unmodified so the hanzi-convert behaviour is exercised end to end.
"""

from __future__ import annotations

import opencc

from app.models import BtDownloaderSettings, BtFeedEntry
from app.services.bt_downloader_service import BtDownloaderService


class FakeEntryRepo:
    """Stands in for ``BtFeedEntryRepository`` — only ``list_most_recent`` is used."""

    def __init__(self, entries: list[BtFeedEntry]) -> None:
        self._entries = entries
        self.list_most_recent_calls: list[int] = []

    def list_most_recent(self, limit: int) -> list[BtFeedEntry]:
        self.list_most_recent_calls.append(limit)
        return self._entries[:limit]


def _entry(entry_id: int, title: str) -> BtFeedEntry:
    return BtFeedEntry(
        id=entry_id,
        feed_id=1,
        guid=f'guid-{entry_id}',
        title=title,
        link=f'https://link/{entry_id}',
        fetched_at='2026-01-01T00:00:00+00:00',
    )


def _service(entries: list[BtFeedEntry], *, hanzi_convert: bool = True) -> tuple[BtDownloaderService, FakeEntryRepo]:
    entry_repo = FakeEntryRepo(entries)
    service = BtDownloaderService(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        entry_repo,  # type: ignore[arg-type]
        lambda _token: None,  # type: ignore[arg-type,return-value]
        None,  # type: ignore[arg-type]
        BtDownloaderSettings(hanzi_convert=hanzi_convert),
    )
    return service, entry_repo


def test_match_count_empty_keywords_returns_zero() -> None:
    service, entry_repo = _service([_entry(1, 'Some Show - 01')])

    count, over_cap = service.count_matching([])

    assert (count, over_cap) == (0, False)
    assert entry_repo.list_most_recent_calls == []


def test_match_count_all_keywords_substring_case_insensitive() -> None:
    entries = [
        _entry(1, 'LoliHouse 1080p Ep01'),
        _entry(2, 'lolihouse 1080P Ep02'),  # different casing, still matches
        _entry(3, 'LoliHouse 720p Ep01'),  # missing "1080"
        _entry(4, 'SomeOtherGroup 1080p Ep01'),  # missing "LoliHouse"
    ]
    service, _ = _service(entries)

    count, over_cap = service.count_matching(['LoliHouse', '1080'])

    assert count == 2
    assert over_cap is False


def test_match_count_hanzi_convert_normalizes_both_sides() -> None:
    traditional_keyword = '關於我轉生變成史萊姆這檔事'
    simplified_title = opencc.OpenCC('t2s').convert(traditional_keyword) + ' 第01集'
    entries = [_entry(1, simplified_title)]

    enabled, _ = _service(entries, hanzi_convert=True)
    count_enabled, _ = enabled.count_matching([traditional_keyword])
    assert count_enabled == 1

    disabled, _ = _service(entries, hanzi_convert=False)
    count_disabled, _ = disabled.count_matching([traditional_keyword])
    assert count_disabled == 0


def test_match_count_over_cap_flag_set_when_entries_exceed_10000() -> None:
    entries = [_entry(i, 'Matching Show') for i in range(10_001)]
    service, entry_repo = _service(entries)

    count, over_cap = service.count_matching(['Matching'])

    assert over_cap is True
    assert count == 10_000
    assert entry_repo.list_most_recent_calls == [10_001]


def test_match_count_not_over_cap_when_entries_at_cap() -> None:
    entries = [_entry(i, 'Matching Show') for i in range(10_000)]
    service, _ = _service(entries)

    count, over_cap = service.count_matching(['Matching'])

    assert over_cap is False
    assert count == 10_000

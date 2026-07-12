"""Tests for the per-SN bilingual (中文配音) opt-in in :class:`UpdateLoop`.

Mirrors the manual-runner bilingual tests but for the scheduled/auto path:
``UpdateLoop.check_tasks`` -> ``_select_target_sns`` -> ``TaskQueue`` /
``TaskInfo.language_tag``. The scheduled path is a separate code path from
``ManualRunner`` (it goes through ``DownloadWorker`` instead), so it needs
its own coverage to guarantee 中文配音 filtering AND language tagging both
work for auto-tracked series, not just manual downloads.
"""

from __future__ import annotations

import pathlib
import threading
from typing import Any

from app.downloader.metadata import AnimeMetadata
from app.logging_ import Logger
from app.models import AppSettings
from app.persistence.anime_list_repo import AnimeListEntryDTO
from app.persistence.repositories import AnimeRow
from app.scheduler.queue_ import TaskQueue
from app.scheduler.update_loop import UpdateLoop


class _FakeSettingsRepo:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def load(self) -> AppSettings:
        return self._settings


class _FakeSnListRepo:
    def parse_legacy(self, default_mode: str) -> dict[int, dict[str, str]]:
        return {}


class _FakeAnimeListEntryRepo:
    def __init__(self, entries: list[AnimeListEntryDTO] | None = None) -> None:
        self._entries = list(entries or [])

    def list_all(self) -> list[AnimeListEntryDTO]:
        return list(self._entries)

    def update_anime_name(self, sn: int, user_id: str, anime_name: str | None) -> None:
        pass


class _FakeAnimeRepo:
    def __init__(self) -> None:
        self._rows: dict[int, AnimeRow] = {}

    def read(self, sn: int) -> AnimeRow | None:
        return self._rows.get(int(sn))


class _FakeMetadataExtractor:
    def __init__(self, by_sn: dict[int, AnimeMetadata]) -> None:
        self._by_sn = by_sn

    def fetch(self, sn: int) -> AnimeMetadata:
        return self._by_sn[int(sn)]


class _FakeCookieRepo:
    def invalidate(self) -> None:
        pass


class _NoopWorker:
    def run(self, sn: int, **_kwargs: Any) -> None:
        return None


def _meta(sn: int, *, episode_list: dict[str, int]) -> AnimeMetadata:
    return AnimeMetadata(
        sn=sn,
        title='某某 [01]',
        bangumi_name='某某',
        bangumi_name_orig='某某',
        episode='01',
        episode_list=episode_list,
    )


def _build(
    tmp_path: pathlib.Path,
    *,
    anime_list_entries: list[AnimeListEntryDTO],
    metadata_by_sn: dict[int, AnimeMetadata],
) -> tuple[UpdateLoop, TaskQueue]:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(check_frequency=1, download_resolution='1080')
    queue = TaskQueue(max_download=5, max_upload=1)

    loop = UpdateLoop(
        settings_repo=_FakeSettingsRepo(settings),  # type: ignore[arg-type]
        sn_list_repo=_FakeSnListRepo(),  # type: ignore[arg-type]
        anime_list_entry_repo=_FakeAnimeListEntryRepo(anime_list_entries),  # type: ignore[arg-type]
        anime_repo=_FakeAnimeRepo(),  # type: ignore[arg-type]
        queue=queue,
        worker=_NoopWorker(),  # type: ignore[arg-type]
        metadata_extractor=_FakeMetadataExtractor(metadata_by_sn),  # type: ignore[arg-type]
        logger=logger,
        cookie_repo=_FakeCookieRepo(),  # type: ignore[arg-type]
    )
    return loop, queue


def test_all_mode_bilingual_false_drops_dub_variant(tmp_path: pathlib.Path) -> None:
    root_sn = 2000
    episode_list = {'01': 300, '中文配音01': 400}
    entries = [AnimeListEntryDTO(sn=root_sn, enabled=True, mode='all', bilingual=False, user_id='u1')]
    loop, queue = _build(
        tmp_path,
        anime_list_entries=entries,
        metadata_by_sn={root_sn: _meta(root_sn, episode_list=episode_list)},
    )

    sn_dict = loop._load_sn_dict_from_db('latest')
    loop.check_tasks(sn_dict)

    assert queue.contains(300)
    assert not queue.contains(400)


def test_all_mode_bilingual_true_enqueues_both_with_tags(tmp_path: pathlib.Path) -> None:
    root_sn = 2001
    episode_list = {'01': 301, '中文配音01': 401}
    entries = [AnimeListEntryDTO(sn=root_sn, enabled=True, mode='all', bilingual=True, user_id='u1')]
    loop, queue = _build(
        tmp_path,
        anime_list_entries=entries,
        metadata_by_sn={root_sn: _meta(root_sn, episode_list=episode_list)},
    )

    sn_dict = loop._load_sn_dict_from_db('latest')
    loop.check_tasks(sn_dict)

    assert queue.contains(301)
    assert queue.contains(401)
    jp_info = queue.get(301)
    dub_info = queue.get(401)
    assert jp_info is not None and jp_info.language_tag is None
    assert dub_info is not None and dub_info.language_tag == '中'


def test_latest_mode_bilingual_false_does_not_pick_dub_as_latest(
    tmp_path: pathlib.Path,
) -> None:
    """Regression: dub entries are inserted last by the mobile-API extractor,
    so an unfiltered episode_sns[-1] could wrongly pick the dub SN as
    'latest'. With bilingual=False, the dub entry must be filtered out
    BEFORE the 'latest' pick, so sn=300 (日文) is selected, not sn=400 (dub).
    """
    root_sn = 2002
    episode_list = {'01': 300, '中文配音01': 400}
    entries = [AnimeListEntryDTO(sn=root_sn, enabled=True, mode='latest', bilingual=False, user_id='u1')]
    loop, queue = _build(
        tmp_path,
        anime_list_entries=entries,
        metadata_by_sn={root_sn: _meta(root_sn, episode_list=episode_list)},
    )

    sn_dict = loop._load_sn_dict_from_db('latest')
    loop.check_tasks(sn_dict)

    assert queue.contains(300)
    assert not queue.contains(400)


def test_no_entries_defaults_to_dropping_dub(tmp_path: pathlib.Path) -> None:
    """_select_target_sns default (bilingual=False, no kwarg passed) drops
    中文配音 entries — matches legacy/CLI-style behaviour with no DB entry."""
    loop, _queue = _build(tmp_path, anime_list_entries=[], metadata_by_sn={})
    result = loop._select_target_sns(9999, 'all', {'01': 100, '中文配音01': 200})
    assert result == [100]


def test_select_target_sns_bilingual_true_keeps_both(tmp_path: pathlib.Path) -> None:
    loop, _queue = _build(tmp_path, anime_list_entries=[], metadata_by_sn={})
    result = loop._select_target_sns(9999, 'all', {'01': 100, '中文配音01': 200}, bilingual=True)
    assert sorted(result) == [100, 200]


def test_worker_still_spawned_for_each_enqueued_sn(tmp_path: pathlib.Path) -> None:
    """Sanity: with bilingual=True, both sns actually get a worker spawned
    (threading them through the queue is not enough on its own)."""
    root_sn = 2003
    episode_list = {'01': 302, '中文配音01': 402}
    entries = [AnimeListEntryDTO(sn=root_sn, enabled=True, mode='all', bilingual=True, user_id='u1')]

    spawned: list[int] = []
    lock = threading.Lock()

    class _RecordingWorker:
        def run(self, sn: int, **_kwargs: Any) -> None:
            with lock:
                spawned.append(int(sn))

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(check_frequency=1, download_resolution='1080')
    queue = TaskQueue(max_download=5, max_upload=1)
    loop = UpdateLoop(
        settings_repo=_FakeSettingsRepo(settings),  # type: ignore[arg-type]
        sn_list_repo=_FakeSnListRepo(),  # type: ignore[arg-type]
        anime_list_entry_repo=_FakeAnimeListEntryRepo(entries),  # type: ignore[arg-type]
        anime_repo=_FakeAnimeRepo(),  # type: ignore[arg-type]
        queue=queue,
        worker=_RecordingWorker(),  # type: ignore[arg-type]
        metadata_extractor=_FakeMetadataExtractor({root_sn: _meta(root_sn, episode_list=episode_list)}),  # type: ignore[arg-type]
        logger=logger,
        cookie_repo=_FakeCookieRepo(),  # type: ignore[arg-type]
    )

    sn_dict = loop._load_sn_dict_from_db('latest')
    loop.check_tasks(sn_dict)

    for _ in range(30):
        if len(spawned) >= 2:
            break
        threading.Event().wait(0.05)

    assert sorted(spawned) == [302, 402]

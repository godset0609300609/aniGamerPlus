"""Tests for the per-SN bilingual (中文配音) opt-in in :class:`ManualRunner`.

mode='all' must:
- drop 中文配音-labeled SNs entirely when the owning anime-list entry has
  ``bilingual=False`` (the default, including when there is no entry at
  all — e.g. the CLI path with no ``owner_id``).
- keep both variants when ``bilingual=True``, tagging the dub SN's download
  call with ``language_tag='中'`` and leaving the 日文 SN's call untagged.
"""

from __future__ import annotations

import pathlib
from typing import Any

from app.downloader.anime import DownloadResult
from app.logging_ import Logger
from app.models import AppSettings
from app.persistence.anime_list_repo import AnimeListEntryDTO
from app.scheduler.manual_runner import ManualRunner


class _FakeAnime:
    def __init__(
        self,
        sn: int,
        *,
        episode_list: dict[str, int] | None = None,
    ) -> None:
        self.sn = int(sn)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._episode_list = episode_list or {'01': sn}

    def load(self) -> None:
        self.calls.append(('load', {}))

    def get_episode_list(self) -> dict[str, int]:
        return dict(self._episode_list)

    def get_bangumi_name(self) -> str:
        return f'番劇_{self.sn}'

    def get_episode(self) -> str:
        return '01'

    def get_resolution(self) -> int:
        return 1080

    def enable_danmu(self) -> None:
        self.calls.append(('enable_danmu', {}))

    def get_info(self) -> None:
        self.calls.append(('get_info', {}))

    def download(self, **kwargs: Any) -> DownloadResult:
        self.calls.append(('download', kwargs))
        return DownloadResult(success=True, file_path=pathlib.Path(f'/tmp/{self.sn}.mp4'), size_mb=500)


class _FakeAnimeRepo:
    def read(self, sn: int) -> None:
        return None


class _FakeAnimeListRepo:
    """Fake ``AnimeListEntryRepository`` — only ``get_by_user_sn`` is used."""

    def __init__(self, entries: dict[tuple[str, int], AnimeListEntryDTO] | None = None) -> None:
        self._entries = entries or {}

    def get_by_user_sn(self, user_id: str, sn: int) -> AnimeListEntryDTO | None:
        return self._entries.get((user_id, int(sn)))


def _runner(
    tmp_path: pathlib.Path,
    anime_map: dict[int, _FakeAnime],
    *,
    anime_list_repo: Any = None,
) -> ManualRunner:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(download_resolution='1080')
    return ManualRunner(
        anime_factory=lambda sn: anime_map[int(sn)],  # type: ignore[arg-type]
        anime_repo=_FakeAnimeRepo(),  # type: ignore[arg-type]
        settings=settings,
        logger=logger,
        anime_list_repo=anime_list_repo,
    )


def _download_calls(fa: _FakeAnime) -> list[dict[str, Any]]:
    return [kw for name, kw in fa.calls if name == 'download']


def test_mode_all_bilingual_false_drops_dub_variant(tmp_path: pathlib.Path) -> None:
    root_sn = 1000
    episode_list = {'01': 100, '中文配音01': 200}
    fa_root = _FakeAnime(root_sn, episode_list=episode_list)
    fa_jp = _FakeAnime(100)
    fa_dub = _FakeAnime(200)
    fakes = {root_sn: fa_root, 100: fa_jp, 200: fa_dub}

    entry = AnimeListEntryDTO(sn=root_sn, bilingual=False)
    repo = _FakeAnimeListRepo({('u1', root_sn): entry})

    r = _runner(tmp_path, fakes, anime_list_repo=repo)
    r.run(root_sn, mode='all', owner_id='u1')

    assert len(_download_calls(fa_jp)) == 1
    assert _download_calls(fa_dub) == []


def test_mode_all_bilingual_true_downloads_both_with_correct_tags(
    tmp_path: pathlib.Path,
) -> None:
    root_sn = 1001
    episode_list = {'01': 101, '中文配音01': 201}
    fa_root = _FakeAnime(root_sn, episode_list=episode_list)
    fa_jp = _FakeAnime(101)
    fa_dub = _FakeAnime(201)
    fakes = {root_sn: fa_root, 101: fa_jp, 201: fa_dub}

    entry = AnimeListEntryDTO(sn=root_sn, bilingual=True)
    repo = _FakeAnimeListRepo({('u1', root_sn): entry})

    r = _runner(tmp_path, fakes, anime_list_repo=repo)
    r.run(root_sn, mode='all', owner_id='u1')

    jp_calls = _download_calls(fa_jp)
    dub_calls = _download_calls(fa_dub)
    assert len(jp_calls) == 1
    assert len(dub_calls) == 1
    # Untagged (日文) call: language_tag absent or explicitly None.
    assert jp_calls[0].get('language_tag') is None
    assert dub_calls[0].get('language_tag') == '中'


def test_mode_all_no_matching_entry_defaults_to_dropping_dub(
    tmp_path: pathlib.Path,
) -> None:
    """owner_id set but no matching anime-list entry → treat as bilingual=False."""
    root_sn = 1002
    episode_list = {'01': 102, '中文配音01': 202}
    fa_root = _FakeAnime(root_sn, episode_list=episode_list)
    fa_jp = _FakeAnime(102)
    fa_dub = _FakeAnime(202)
    fakes = {root_sn: fa_root, 102: fa_jp, 202: fa_dub}

    repo = _FakeAnimeListRepo({})  # no entries at all

    r = _runner(tmp_path, fakes, anime_list_repo=repo)
    r.run(root_sn, mode='all', owner_id='u1')

    assert len(_download_calls(fa_jp)) == 1
    assert _download_calls(fa_dub) == []


def test_mode_all_cli_path_no_owner_id_drops_dub_without_raising(
    tmp_path: pathlib.Path,
) -> None:
    """True CLI path: owner_id=None and anime_list_repo=None — must not raise
    and must default to dropping 中文配音 (same as bilingual=False)."""
    root_sn = 1003
    episode_list = {'01': 103, '中文配音01': 203}
    fa_root = _FakeAnime(root_sn, episode_list=episode_list)
    fa_jp = _FakeAnime(103)
    fa_dub = _FakeAnime(203)
    fakes = {root_sn: fa_root, 103: fa_jp, 203: fa_dub}

    r = _runner(tmp_path, fakes, anime_list_repo=None)
    r.run(root_sn, mode='all')  # owner_id defaults to None

    assert len(_download_calls(fa_jp)) == 1
    assert _download_calls(fa_dub) == []


# ---------------------------------------------------------------------------
# Explicit ``bilingual`` param (manual task dialog one-shot override) —
# see ManualRunner._resolve_bilingual.
# ---------------------------------------------------------------------------


def test_manual_bilingual_flag_overrides_missing_entry(tmp_path: pathlib.Path) -> None:
    """bilingual=True param + no anime_list entry -> both variants download."""
    root_sn = 1004
    episode_list = {'01': 104, '中文配音01': 204}
    fa_root = _FakeAnime(root_sn, episode_list=episode_list)
    fa_jp = _FakeAnime(104)
    fa_dub = _FakeAnime(204)
    fakes = {root_sn: fa_root, 104: fa_jp, 204: fa_dub}

    repo = _FakeAnimeListRepo({})  # no entries at all

    r = _runner(tmp_path, fakes, anime_list_repo=repo)
    r.run(root_sn, mode='all', owner_id='u1', bilingual=True)

    assert len(_download_calls(fa_jp)) == 1
    assert len(_download_calls(fa_dub)) == 1


def test_manual_bilingual_flag_overrides_entry_false(tmp_path: pathlib.Path) -> None:
    """bilingual=True param + entry has bilingual=False -> explicit param wins."""
    root_sn = 1005
    episode_list = {'01': 105, '中文配音01': 205}
    fa_root = _FakeAnime(root_sn, episode_list=episode_list)
    fa_jp = _FakeAnime(105)
    fa_dub = _FakeAnime(205)
    fakes = {root_sn: fa_root, 105: fa_jp, 205: fa_dub}

    entry = AnimeListEntryDTO(sn=root_sn, bilingual=False)
    repo = _FakeAnimeListRepo({('u1', root_sn): entry})

    r = _runner(tmp_path, fakes, anime_list_repo=repo)
    r.run(root_sn, mode='all', owner_id='u1', bilingual=True)

    assert len(_download_calls(fa_jp)) == 1
    assert len(_download_calls(fa_dub)) == 1


def test_manual_bilingual_flag_false_falls_back_to_entry(tmp_path: pathlib.Path) -> None:
    """bilingual=False param + entry has bilingual=True -> anime_list setting still respected."""
    root_sn = 1006
    episode_list = {'01': 106, '中文配音01': 206}
    fa_root = _FakeAnime(root_sn, episode_list=episode_list)
    fa_jp = _FakeAnime(106)
    fa_dub = _FakeAnime(206)
    fakes = {root_sn: fa_root, 106: fa_jp, 206: fa_dub}

    entry = AnimeListEntryDTO(sn=root_sn, bilingual=True)
    repo = _FakeAnimeListRepo({('u1', root_sn): entry})

    r = _runner(tmp_path, fakes, anime_list_repo=repo)
    r.run(root_sn, mode='all', owner_id='u1', bilingual=False)

    assert len(_download_calls(fa_jp)) == 1
    assert len(_download_calls(fa_dub)) == 1

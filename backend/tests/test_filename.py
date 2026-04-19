"""Tests for ``FilenameBuilder``."""

from __future__ import annotations

import pathlib

import pytest

from app.downloader.filename import FilenameBuilder
from app.downloader.metadata import AnimeMetadata
from app.models import AppSettings


def _meta(
    *,
    title: str = '測試番劇[01]',
    bangumi_name: str = '測試番劇',
    bangumi_name_orig: str | None = None,
    episode: str = '01',
) -> AnimeMetadata:
    return AnimeMetadata(
        sn=1,
        title=title,
        bangumi_name=bangumi_name,
        bangumi_name_orig=bangumi_name_orig or bangumi_name,
        episode=episode,
        episode_list={episode: 1},
    )


def test_build_all_switches_on() -> None:
    # New format: {prefix}{name} - S{season:02d}E{ep:02d} ({res}p){suffix}.ext
    settings = AppSettings(
        customized_video_filename_prefix='【動畫瘋】',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=True,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(_meta(), resolution='1080', season=1)
    assert name == '【動畫瘋】測試番劇 - S01E01 (1080p).mp4'


def test_build_without_resolution_tag() -> None:
    settings = AppSettings(
        customized_video_filename_prefix='',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=False,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    assert fb.build(_meta(), resolution='1080', season=1) == '測試番劇 - S01E01.mp4'


def test_legalize_replaces_reserved_chars() -> None:
    raw = 'a|b?c*d<e>f"g:h\\i/j'
    out = FilenameBuilder.legalize(raw)
    for forbidden in '|?*<>":\\/':
        assert forbidden not in out
    # Full-width equivalents all appear
    for full in '｜？＊＜＞＂：＼／':
        assert full in out


def test_classify_dir_without_classify_returns_bangumi_dir(
    tmp_path: pathlib.Path,
) -> None:
    fb = FilenameBuilder(AppSettings())
    out = fb.classify_dir(
        _meta(),
        bangumi_dir=tmp_path,
        bangumi_tag='',
        season=1,
        classify=False,
    )
    assert out == tmp_path


def test_classify_dir_with_tag(tmp_path: pathlib.Path) -> None:
    fb = FilenameBuilder(AppSettings(classify_season=False))
    out = fb.classify_dir(
        _meta(bangumi_name='我的番劇'),
        bangumi_dir=tmp_path,
        bangumi_tag='本季',
        season=1,
        classify=True,
    )
    assert out == tmp_path / '本季' / '我的番劇'


def test_classify_dir_uses_bangumi_name(tmp_path: pathlib.Path) -> None:
    """classify_dir always uses bangumi_name for the series folder (no rename)."""
    fb = FilenameBuilder(AppSettings(classify_season=False))
    out = fb.classify_dir(
        _meta(bangumi_name='預設名'),
        bangumi_dir=tmp_path,
        bangumi_tag='',
        season=2,
        classify=True,
    )
    assert out == tmp_path / '預設名'


def test_classify_dir_season_folder(tmp_path: pathlib.Path) -> None:
    fb = FilenameBuilder(AppSettings(classify_season=True))
    meta = _meta(
        bangumi_name='番劇',
        bangumi_name_orig='番劇 第二季',
        episode='01',
    )
    out = fb.classify_dir(
        meta,
        bangumi_dir=tmp_path,
        bangumi_tag='',
        season=2,
        classify=True,
    )
    # season_title_filter recognised "第二季", so sub is "Season 2",
    # and root is the title with the season segment stripped + rstripped.
    assert out == tmp_path / '番劇' / 'Season 2'


def test_build_plex_naming_with_season() -> None:
    settings = AppSettings(
        customized_video_filename_prefix='',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=False,
        video_filename_extension='mp4',
        plex_naming=True,
        zerofill=2,
    )
    fb = FilenameBuilder(settings)
    meta = _meta(
        bangumi_name='番劇',
        bangumi_name_orig='番劇 第三季',
        episode='05',
    )
    out = fb.build(meta, resolution='1080')
    # Plex mode: legacy concatenation (no " - " separator); zerofill=2 for season
    assert out == '番劇[S03E05].mp4'


# ---------------------------------------------------------------------------
# New-format tests
# ---------------------------------------------------------------------------


def test_new_format_no_prefix_no_suffix() -> None:
    """Empty prefix/suffix → bare '{name} - S01E{ep} ({res}p).ext'."""
    settings = AppSettings(
        customized_video_filename_prefix='',
        customized_video_filename_suffix='',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=True,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(_meta(bangumi_name='大賢者里德爾的時間逆行', episode='3'), resolution='360', season=1)
    assert name == '大賢者里德爾的時間逆行 - S01E03 (360p).mp4'


def test_new_format_with_prefix_and_suffix() -> None:
    """Custom prefix + suffix wrap the base name correctly."""
    settings = AppSettings(
        customized_video_filename_prefix='【MyPrefix】',
        customized_video_filename_suffix='_abc',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=True,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(_meta(bangumi_name='大賢者里德爾的時間逆行', episode='3'), resolution='360', season=1)
    assert name == '【MyPrefix】大賢者里德爾的時間逆行 - S01E03 (360p)_abc.mp4'


def test_new_format_season_two() -> None:
    """season=2 → S02E05 in the filename."""
    settings = AppSettings(
        customized_video_filename_prefix='',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=False,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(_meta(bangumi_name='測試番劇', episode='5'), resolution='1080', season=2)
    assert name == '測試番劇 - S02E05.mp4'


def test_new_format_non_numeric_episode() -> None:
    """Non-numeric episode (特別篇) appends label without E prefix."""
    settings = AppSettings(
        customized_video_filename_prefix='',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=False,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(_meta(bangumi_name='黃泉使者', episode='特別篇'), resolution='1080', season=1)
    assert name == '黃泉使者 - S01特別篇.mp4'


def test_new_format_no_bangumi_name() -> None:
    """add_bangumi_name_to_video_filename=False → only ep token (+ optional res)."""
    settings = AppSettings(
        customized_video_filename_prefix='',
        add_bangumi_name_to_video_filename=False,
        add_resolution_to_video_filename=True,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(_meta(bangumi_name='測試番劇', episode='5'), resolution='720', season=1)
    assert name == 'S01E05 (720p).mp4'


def test_new_format_no_resolution() -> None:
    """add_resolution_to_video_filename=False → no resolution parenthetical."""
    settings = AppSettings(
        customized_video_filename_prefix='',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=False,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(_meta(bangumi_name='測試番劇', episode='7'), resolution='1080', season=1)
    assert name == '測試番劇 - S01E07.mp4'


def test_default_prefix_is_empty_string() -> None:
    """AppSettings default prefix is '' — no 【動畫瘋】 hardcoded."""
    settings = AppSettings()
    assert settings.customized_video_filename_prefix == ''


# ---------------------------------------------------------------------------
# include_resolution flag tests
# ---------------------------------------------------------------------------


def test_build_with_include_resolution_true_contains_suffix() -> None:
    """Default (include_resolution=True) + setting on → suffix present."""
    settings = AppSettings(
        customized_video_filename_prefix='',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=True,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(
        _meta(bangumi_name='殺手青春', episode='1'),
        resolution='360',
        season=1,
        include_resolution=True,
    )
    assert name == '殺手青春 - S01E01 (360p).mp4'


def test_build_with_include_resolution_false_omits_suffix() -> None:
    """include_resolution=False → no (360p) even when setting is True."""
    settings = AppSettings(
        customized_video_filename_prefix='',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=True,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(
        _meta(bangumi_name='殺手青春', episode='1'),
        resolution='360',
        season=1,
        include_resolution=False,
    )
    assert name == '殺手青春 - S01E01.mp4'


def test_build_temp_still_includes_resolution_for_uniqueness() -> None:
    """build_temp() always embeds resolution — different-res retries don't collide."""
    settings = AppSettings(
        customized_video_filename_prefix='',
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=True,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build_temp(
        _meta(bangumi_name='殺手青春', episode='1'),
        resolution='360',
        temp_suffix='MERGING',
        season=1,
    )
    # Temp filename embeds resolution via the stem path (without_suffix=True)
    # which goes through the standard build() — so it follows the setting flag.
    # Since add_resolution_to_video_filename=True, the temp stem includes (360p).
    assert '(360p)' in name
    assert 'MERGING' in name


# ---------------------------------------------------------------------------
# custom_name override tests
# ---------------------------------------------------------------------------


def test_build_uses_custom_name_when_present() -> None:
    """When custom_name is supplied, it replaces bangumi_name in the filename."""
    settings = AppSettings(
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=False,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(
        _meta(bangumi_name='自動偵測名稱', episode='1'),
        resolution='1080',
        season=1,
        custom_name='自訂覆蓋名稱',
    )
    assert '自訂覆蓋名稱' in name
    assert '自動偵測名稱' not in name
    assert name == '自訂覆蓋名稱 - S01E01.mp4'


def test_build_falls_back_to_bangumi_name_when_custom_name_none() -> None:
    """None custom_name → falls back to meta.bangumi_name."""
    settings = AppSettings(
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=False,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(
        _meta(bangumi_name='自動偵測名稱', episode='2'),
        resolution='1080',
        season=1,
        custom_name=None,
    )
    assert name == '自動偵測名稱 - S01E02.mp4'


def test_build_falls_back_to_bangumi_name_when_custom_name_empty() -> None:
    """Empty-string custom_name → falls back to meta.bangumi_name."""
    settings = AppSettings(
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=False,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(
        _meta(bangumi_name='自動偵測名稱', episode='3'),
        resolution='1080',
        season=1,
        custom_name='',
    )
    assert name == '自動偵測名稱 - S01E03.mp4'


def test_build_trims_whitespace_in_custom_name() -> None:
    """Leading/trailing whitespace in custom_name is stripped."""
    settings = AppSettings(
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=False,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(
        _meta(bangumi_name='原名', episode='1'),
        resolution='1080',
        season=1,
        custom_name='  前後空白  ',
    )
    assert name == '前後空白 - S01E01.mp4'


def test_build_whitespace_only_custom_name_falls_back(tmp_path: pathlib.Path) -> None:
    """Whitespace-only custom_name is treated as empty → falls back."""
    settings = AppSettings(
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=False,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build(
        _meta(bangumi_name='原名', episode='1'),
        resolution='1080',
        season=1,
        custom_name='   ',
    )
    assert name == '原名 - S01E01.mp4'


def test_classify_dir_uses_custom_name_when_present(tmp_path: pathlib.Path) -> None:
    """classify_dir substitutes custom_name for the series folder."""
    fb = FilenameBuilder(AppSettings(classify_season=False))
    out = fb.classify_dir(
        _meta(bangumi_name='偵測名'),
        bangumi_dir=tmp_path,
        bangumi_tag='',
        season=1,
        custom_name='自訂資料夾',
        classify=True,
    )
    assert out == tmp_path / '自訂資料夾'


def test_classify_dir_falls_back_to_bangumi_name_when_no_custom(tmp_path: pathlib.Path) -> None:
    """classify_dir uses bangumi_name when custom_name is None."""
    fb = FilenameBuilder(AppSettings(classify_season=False))
    out = fb.classify_dir(
        _meta(bangumi_name='偵測名'),
        bangumi_dir=tmp_path,
        bangumi_tag='',
        season=1,
        custom_name=None,
        classify=True,
    )
    assert out == tmp_path / '偵測名'


def test_build_temp_uses_custom_name(tmp_path: pathlib.Path) -> None:
    """build_temp propagates custom_name so the temp dir stays consistent."""
    settings = AppSettings(
        add_bangumi_name_to_video_filename=True,
        add_resolution_to_video_filename=True,
        video_filename_extension='mp4',
    )
    fb = FilenameBuilder(settings)
    name = fb.build_temp(
        _meta(bangumi_name='原名', episode='5'),
        resolution='720',
        temp_suffix='MERGING',
        season=2,
        custom_name='自訂',
    )
    assert '自訂' in name
    assert '原名' not in name
    assert 'MERGING' in name

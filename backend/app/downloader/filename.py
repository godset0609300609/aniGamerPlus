"""Filename + directory layout — port of ``Anime.__get_filename`` / ``__classify``.

All the string munging that decides where a downloaded episode ends up on
disk lives here. Pure functions where possible, instance state limited to
the immutable ``AppSettings`` reference.
"""

from __future__ import annotations

import pathlib
import re
import typing as T

if T.TYPE_CHECKING:
    from ..models import AppSettings
    from .metadata import AnimeMetadata


_ZH_DIGITS: dict[str, int] = {
    '零': 0,
    '一': 1,
    '二': 2,
    '兩': 2,
    '三': 3,
    '四': 4,
    '五': 5,
    '六': 6,
    '七': 7,
    '八': 8,
    '九': 9,
    '十': 10,
}


class FilenameBuilder:
    """Owns the legacy filename / classify semantics."""

    season_title_filter: T.ClassVar[re.Pattern[str]] = re.compile(r'第[零一二三四五六七八九十]{1,3}季$')
    extra_title_filter: T.ClassVar[re.Pattern[str]] = re.compile(r'\[(特別篇|中文配音)\]$')

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    # ------------------------------------------------------------------ public

    def build(
        self,
        meta: AnimeMetadata,
        resolution: str,
        *,
        season: int = 1,
        custom_name: str | None = None,
        include_resolution: bool = True,
        without_suffix: bool = False,
    ) -> str:
        """Render the full episode filename.

        When ``without_suffix`` is True the user suffix + extension are
        stripped — callers use that form to build temp filenames.

        ``include_resolution`` gates the ``({res}p)`` parenthetical
        independently of the ``add_resolution_to_video_filename`` setting.
        Pass ``False`` for auto-mode (scheduled scan) downloads so those
        filenames omit the resolution suffix.  The setting flag is still
        checked — both must be True for the suffix to appear.

        ``custom_name``, when non-empty, overrides ``meta.bangumi_name``
        for the name portion of the filename only.
        """
        episode = self._format_episode(meta, season=season)
        name = self._compose_base(meta, episode, custom_name=custom_name)

        if include_resolution and self._settings.add_resolution_to_video_filename:
            name = f'{name} ({resolution}p)'

        if without_suffix:
            return name

        name = f'{name}{self._settings.customized_video_filename_suffix}.{self._settings.video_filename_extension}'
        return self.legalize(name)

    def build_temp(
        self,
        meta: AnimeMetadata,
        resolution: str,
        temp_suffix: str,
        *,
        season: int = 1,
        custom_name: str | None = None,
    ) -> str:
        """Filename used while the download is in flight."""
        stem = self.build(meta, resolution, season=season, custom_name=custom_name, without_suffix=True)
        name = (
            f'{stem}{self._settings.customized_video_filename_suffix}'
            f'.{temp_suffix}.{self._settings.video_filename_extension}'
        )
        return self.legalize(name)

    def classify_dir(
        self,
        meta: AnimeMetadata,
        bangumi_dir: pathlib.Path,
        bangumi_tag: str,
        season: int,
        *,
        custom_name: str | None = None,
        classify: bool,
    ) -> pathlib.Path:
        """Return the directory the final file should live in.

        Mirrors ``Anime.download``'s ``bangumi_tag`` + ``classify`` +
        ``classify_season`` fork. The series folder uses the bare
        ``bangumi_name`` (or ``custom_name`` when supplied); ``season``
        affects only the filename, not the directory structure (unless
        ``classify_season`` is also True, which uses the title-embedded
        season annotation as before).

        ``custom_name``, when non-empty, overrides ``meta.bangumi_name``
        for the series folder name so that the temp → final move stays
        within the same tree.
        """
        out = pathlib.Path(bangumi_dir)
        if bangumi_tag:
            out = out / self.legalize(bangumi_tag)
        if not classify:
            return out

        if self._settings.classify_season:
            root, sub = self._season_root_and_sub(meta)
            return out / self.legalize(root) / sub

        resolved = (custom_name or '').strip() or meta.bangumi_name
        return out / self.legalize(resolved)

    @staticmethod
    def legalize(name: str) -> str:
        """Port of ``Config.legalize_filename`` — full-width the reserved chars."""
        out = re.sub(r'\|+', '｜', name)
        out = re.sub(r'\?+', '？', out)
        out = re.sub(r'\*+', '＊', out)
        out = re.sub(r'<+', '＜', out)
        out = re.sub(r'>+', '＞', out)
        out = re.sub(r'\"+', '＂', out)
        out = re.sub(r':+', '：', out)
        out = re.sub(r'\\', '＼', out)
        out = re.sub(r'/', '／', out)
        return out

    # ------------------------------------------------------------------ internals

    def _format_episode(self, meta: AnimeMetadata, *, season: int = 1) -> str:
        """Format the episode token.

        Standard mode (non-Plex): ``S{season:02d}E{ep:02d}`` when episode is
        numeric.  Non-numeric episodes (e.g. ``特別篇``, ``電影``) fall back to
        ``S{season:02d}{episode}`` to preserve the special-episode label.

        Plex naming mode: unchanged legacy behaviour (uses title-embedded
        season annotation).
        """
        episode = meta.episode
        settings = self._settings

        if settings.plex_naming:
            # Plex mode: retain legacy zero-pad then wrap in [SxxExx].
            if re.match(r'^[+-]?\d+(\.\d+){0,1}$', episode) and settings.zerofill > 1:
                if re.match(r'^\d+\.\d+$', episode):
                    head = re.findall(r'^\d+\.', episode)[0][:-1]
                    tail = re.findall(r'\.\d+$', episode)[0]
                    episode = head.zfill(settings.zerofill) + tail
                else:
                    episode = episode.zfill(settings.zerofill)
            season_tags = self.season_title_filter.findall(meta.bangumi_name_orig)
            extra = self.extra_title_filter.findall(meta.bangumi_name_orig)
            if season_tags:
                season_num = _season_num(season_tags[0].replace('第', '').replace('季', ''))
                return f'[S{str(season_num).zfill(settings.zerofill)}E{episode}]'
            if extra:
                return f'[E{episode}]'
            if episode == '電影':
                return f'[{episode}]'
            return f'[S01E{episode}]'

        # Standard mode: S{season:02d}E{ep:02d} for numeric episodes.
        season_str = f'{season:02d}'
        if re.match(r'^[+-]?\d+(\.\d+)?$', episode):
            # Numeric (possibly decimal) episode — extract integer part for
            # zero-padding, keep decimal suffix if present.
            if re.match(r'^\d+\.\d+$', episode):
                int_part, dec_part = episode.split('.', 1)
                ep_str = int_part.zfill(2) + '.' + dec_part
            else:
                try:
                    ep_str = str(int(episode)).zfill(2)
                except ValueError:
                    ep_str = episode.zfill(2)
            return f'S{season_str}E{ep_str}'

        # Non-numeric episode label (特別篇, 電影, etc.) — append as-is.
        return f'S{season_str}{episode}'

    def _compose_base(
        self,
        meta: AnimeMetadata,
        episode: str,
        *,
        custom_name: str | None = None,
    ) -> str:
        settings = self._settings
        # Plex naming already wraps the episode token in square-bracket Plex
        # notation (e.g. ``[S01E05]``); preserve legacy concatenation for that
        # path.  Standard (non-plex) path uses `` - `` as separator.
        separator = '' if settings.plex_naming else ' - '
        if settings.add_bangumi_name_to_video_filename:
            resolved_name = (custom_name or '').strip() or meta.bangumi_name
            return (
                f'{settings.customized_video_filename_prefix}'
                f'{resolved_name}'
                f'{settings.customized_bangumi_name_suffix}'
                f'{separator}{episode}'
            )
        return f'{settings.customized_video_filename_prefix}{episode}'

    def _season_root_and_sub(self, meta: AnimeMetadata) -> tuple[str, str]:
        """Classify-season folder layout, taken from legacy ``download``."""
        original = meta.bangumi_name_orig
        season = self.season_title_filter.findall(original)
        extra = self.extra_title_filter.findall(original)
        if season:
            season_num = _season_num(season[0].replace('第', '').replace('季', ''))
            root = original.replace(season[0], '').rstrip()
            return root, f'Season {season_num}'
        if extra:
            root = original.replace(f'[{extra[0]}]', '').rstrip()
            return root, 'Specials'
        if meta.episode == '電影':
            root = original.replace('[電影]', '').rstrip()
            return root, 'Movie'
        return original.rstrip(), 'Season 1'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _season_num(zh: str) -> int:
    """Turn a Chinese numeral (``二`` / ``十二`` …) into its integer form."""
    digit_num = 0
    result = 0
    tmp = 0
    while digit_num < len(zh):
        ch = zh[digit_num]
        value = _ZH_DIGITS.get(ch)
        if value is None:
            digit_num += 1
            continue
        if value >= 10:
            if tmp == 0:
                tmp = 1
            result += value * tmp
            tmp = 0
        else:
            tmp = tmp * 10 + value
        digit_num += 1
    return result + tmp

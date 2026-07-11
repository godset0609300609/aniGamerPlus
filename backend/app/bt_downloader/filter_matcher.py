"""Keyword-AND filter matching against RSS entry titles.

Mirrors the legacy ``filter.py`` script's ``list.txt`` semantics 1:1: each
filter is a set of keywords that must *all* appear in the title
(case-insensitive substring match — the legacy script wrote this as
``*keyword*`` globs, which is exactly substring matching once you strip the
``*`` wildcards).
"""

from __future__ import annotations

import collections.abc
import typing as T

import opencc

if T.TYPE_CHECKING:
    from ..models import BtFilter


class FilterMatcher:
    """Matches a title against a list of :class:`~app.models.BtFilter` rules.

    The ``opencc.OpenCC`` converter is expensive to construct (loads a
    dictionary), so it is created lazily and cached on the instance —
    callers should keep one ``FilterMatcher`` around across iterations
    rather than constructing a fresh one per call.
    """

    def __init__(self) -> None:
        self._converter: opencc.OpenCC | None = None

    def match(
        self,
        title: str,
        filters: collections.abc.Sequence[BtFilter],
        hanzi_convert: bool,
    ) -> BtFilter | None:
        """Return the first enabled filter (by ``sort_order`` ascending) whose
        every keyword is a case-insensitive substring of *title*.

        A filter with an empty ``keywords`` list never matches — an
        all-keywords-satisfied check over zero keywords is vacuously true,
        which would otherwise make an empty filter match every title.
        """
        normalized_title = self._normalize(title, hanzi_convert)
        candidates = sorted((f for f in filters if f.enabled), key=lambda f: f.sort_order)
        for candidate in candidates:
            if not candidate.keywords:
                continue
            if all(self._normalize(kw, hanzi_convert) in normalized_title for kw in candidate.keywords):
                return candidate
        return None

    def match_all(
        self,
        title: str,
        keywords: collections.abc.Sequence[str],
        hanzi_convert: bool,
    ) -> bool:
        """Return whether every keyword in *keywords* is a substring of *title*.

        Same normalization / case-insensitive-substring semantics as
        :meth:`match`'s per-filter AND check. An empty *keywords* sequence
        returns ``False`` — mirrors the "empty filter never matches"
        convention documented on :meth:`match`.
        """
        if not keywords:
            return False
        normalized_title = self._normalize(title, hanzi_convert)
        return all(self._normalize(kw, hanzi_convert) in normalized_title for kw in keywords)

    def _normalize(self, text: str, hanzi_convert: bool) -> str:
        if hanzi_convert:
            text = self._get_converter().convert(text)
        return text.lower()

    def _get_converter(self) -> opencc.OpenCC:
        if self._converter is None:
            self._converter = opencc.OpenCC('s2t')
        return self._converter

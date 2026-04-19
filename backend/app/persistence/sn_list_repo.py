"""Repository around ``sn_list.txt``.

Ports the legacy ``Config.read_sn_list`` / ``Config.write_sn_list`` /
``Config.get_sn_list_content`` trio onto a :class:`WorkspacePaths`-driven
class with typed return values.
"""

from __future__ import annotations

import codecs
import re
import typing as T

from .file_utils import atomic_write_text

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from .paths import WorkspacePaths


_LEGAL_MODE_RE = re.compile(r'^(all|latest|largest-sn)$')
_TAG_LINE_RE = re.compile(r'^@.+')
_EMPTY_TAG_LINE_RE = re.compile(r'^@ *$')
_SN_LINE_RE = re.compile(r'^\d+$')
_COMMENT_STRIP_RE = re.compile(r'#.+\n')
_MULTI_SPACE_RE = re.compile(r' +')
_RENAME_RE = re.compile(r'<.*>')
_TRAILING_SPACE_RE = re.compile(r'( )+$')


class SnListRepository:
    def __init__(self, paths: WorkspacePaths, logger: Logger) -> None:
        self._paths = paths
        self._logger = logger

    # ------------------------------------------------------------------ raw

    def read_raw(self) -> str:
        path = self._paths.sn_list_path
        if not path.exists():
            return ''
        data = path.read_bytes()
        if data.startswith(codecs.BOM_UTF8):
            data = data[len(codecs.BOM_UTF8) :]
        return data.decode('utf-8')

    def write_raw(self, content: str) -> None:
        atomic_write_text(self._paths.sn_list_path, content)

    # ------------------------------------------------------------------ parse

    def parse_legacy(self, default_mode: str) -> dict[int, dict[str, str]]:
        """Parse ``sn_list.txt`` into the legacy ``{sn: {mode,tag,rename}}`` shape.

        Mirrors ``Config.read_sn_list`` exactly:

        - Lines starting with ``@`` declare a category (``tag``) that
          applies to subsequent sn rows.
        - ``@`` with trailing whitespace resets the category to ``""``.
        - ``#`` comments are stripped.
        - A row is recognised as ``{sn} [mode] [<rename>]``. The mode must
          match ``all|latest|largest-sn``; anything else falls back to
          ``default_mode``. ``rename`` is the content between ``<`` and ``>``.
        """
        path = self._paths.sn_list_path
        if not path.exists() or path.stat().st_size == 0:
            return {}

        raw = self.read_raw()
        sn_dict: dict[int, dict[str, str]] = {}
        bangumi_tag = ''

        for line in raw.splitlines(keepends=True):
            if _TAG_LINE_RE.match(line) and not _EMPTY_TAG_LINE_RE.match(line):
                # Legacy takes ``line[1:-1]`` — drop leading ``@`` and trailing newline.
                bangumi_tag = line[1:].rstrip('\n').rstrip('\r')
                continue
            if _EMPTY_TAG_LINE_RE.match(line):
                bangumi_tag = ''
                continue

            stripped = _COMMENT_STRIP_RE.sub('', line).strip()
            stripped = _MULTI_SPACE_RE.sub(' ', stripped)
            parts = stripped.split(' ')
            if not parts[0]:
                continue
            if not _SN_LINE_RE.match(parts[0]):
                continue

            sn = int(parts[0])
            rename = ''
            if len(parts) > 1:
                mode = parts[1] if _LEGAL_MODE_RE.match(parts[1]) else default_mode
                rename_match = _RENAME_RE.search(stripped)
                if rename_match:
                    rename = rename_match.group(0)[1:-1]
            else:
                mode = default_mode

            tag_clean = _TRAILING_SPACE_RE.sub('', bangumi_tag)
            sn_dict[sn] = {'mode': mode, 'tag': tag_clean, 'rename': rename}

        return sn_dict

"""Service around the "追番清單" anime watch list.

Two storage backends co-exist during the migration:

* **Legacy flat file** — ``sn_list.txt`` via :class:`SnListRepository`.
  Used by :meth:`AnimeListService.list` / :meth:`AnimeListService.replace_all`
  when called without a user context (e.g. the legacy ``UpdateLoop``).

* **Per-user DB table** — ``anime_list_entries`` via
  :class:`AnimeListEntryRepository`.  Used by
  :meth:`AnimeListService.list_entries` /
  :meth:`AnimeListService.replace_entries` which are the RBAC-aware paths
  called from the API layer.

Permission rules
----------------
* admin: ``list_entries`` returns all entries across all users, each
  annotated with ``owner_username``.  ``replace_entries`` can supply entries
  with an explicit ``owner_id``; entries whose ``owner_id`` is ``None`` are
  assigned to the calling user.
* downloader: ``list_entries`` returns only the caller's own entries (no
  ``owner_username`` set).  ``replace_entries`` rejects any entry whose
  ``owner_id`` differs from the caller's id (returns HTTP 400).
"""

from __future__ import annotations

import re
import typing as T

import fastapi

from ..models import AnimeListEntry, AnimeListMode
from ..persistence.anime_list_repo import AnimeListEntryDTO
from ..persistence.user_repo import UserRow
from ._factory import container_bound

# Alias to avoid name-shadowing inside AnimeListService which defines a
# method also named ``list``.  mypy resolves annotations in class scope and
# picks up the method instead of the built-in without this alias.
_List = list

if T.TYPE_CHECKING:
    from ..persistence.anime_list_repo import AnimeListEntryRepository
    from ..persistence.repositories import AnimeRepository
    from ..persistence.sn_list_repo import SnListRepository
    from ..persistence.user_repo import UserRepository

_VALID_MODES: frozenset[str] = frozenset({'all', 'latest', 'largest-sn', 'single'})


class AnimeListService:
    """Structured view over the anime watch list."""

    def __init__(
        self,
        sn_list_repo: SnListRepository,
        anime_repo: AnimeRepository,
        anime_list_entry_repo: AnimeListEntryRepository | None = None,
        user_repo: UserRepository | None = None,
    ) -> None:
        self._sn_list_repo = sn_list_repo
        self._anime_repo = anime_repo
        self._anime_list_entry_repo = anime_list_entry_repo
        self._user_repo = user_repo

    # -- RBAC-aware read -------------------------------------------------

    def list_entries(self, user: UserRow) -> _List[AnimeListEntry]:
        """Return the anime list for the given user.

        * admin: returns all entries across all users; populates
          ``owner_username`` on each entry.
        * downloader: returns only entries belonging to ``user``; leaves
          ``owner_username`` as ``None``.
        """
        if self._anime_list_entry_repo is None:
            # Fallback to legacy flat-file path (e.g. during migration).
            return self.list()

        if user.role == 'admin':
            dtos = self._anime_list_entry_repo.list_all()
            # Build username cache to avoid N+1 queries.
            user_ids = {dto.user_id for dto in dtos if dto.user_id is not None}
            username_cache: dict[str, str] = {}
            if self._user_repo is not None:
                for uid in user_ids:
                    row = self._user_repo.get(uid)
                    if row is not None:
                        username_cache[uid] = row.username
            entries = [self._dto_to_entry(dto, username_cache) for dto in dtos]
        else:
            dtos = self._anime_list_entry_repo.list_for_user(user.id)
            entries = [self._dto_to_entry(dto, {}) for dto in dtos]

        self._enrich(entries)
        return entries

    def replace_entries(self, user: UserRow, entries: _List[AnimeListEntry]) -> None:
        """Replace the anime list entries according to the caller's role.

        * admin: accepts entries that may carry an explicit ``owner_id``; any
          entry whose ``owner_id`` is ``None`` is assigned to the calling admin.
          Groups entries by owner and replaces each owner's slice atomically.
        * downloader: only the caller's own entries are allowed.  Any entry
          whose ``owner_id`` is set to a different user id raises HTTP 400.
          ``owner_id=None`` entries are silently assigned to the caller.
        """
        if self._anime_list_entry_repo is None:
            # Fallback to legacy flat-file path.
            self.replace_all(entries)
            return

        if user.role == 'admin':
            # Group by owner_id (None → caller's id).
            groups: dict[str, _List[AnimeListEntry]] = {}
            for entry in entries:
                oid = entry.owner_id if entry.owner_id is not None else user.id
                groups.setdefault(oid, []).append(entry)

            # Ensure owners absent from the save payload get their slice
            # cleared.  Also always include the calling admin so that
            # deleting their last entry persists as an empty list.
            existing_owner_ids = self._anime_list_entry_repo.list_all_owner_ids()
            groups.setdefault(user.id, [])
            for oid in existing_owner_ids:
                groups.setdefault(oid, [])

            for owner_id, owner_entries in groups.items():
                dtos = [self._entry_to_dto(e, idx) for idx, e in enumerate(owner_entries)]
                self._anime_list_entry_repo.replace_all_for_user(owner_id, dtos)
        else:
            # Downloader: reject entries with a foreign owner_id.
            for entry in entries:
                if entry.owner_id is not None and entry.owner_id != user.id:
                    raise fastapi.HTTPException(
                        status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                        detail=(f'Entry sn={entry.sn} owner_id={entry.owner_id!r} does not belong to the current user'),
                    )
            dtos = [self._entry_to_dto(e, idx) for idx, e in enumerate(entries)]
            self._anime_list_entry_repo.replace_all_for_user(user.id, dtos)

    # -- legacy flat-file read (UpdateLoop / SnListService compat) -------

    def list(self) -> _List[AnimeListEntry]:
        raw = self._sn_list_repo.read_raw()
        entries = self._parse(raw)
        self._enrich(entries)
        return entries

    # -- legacy flat-file write ------------------------------------------

    def replace_all(self, entries: _List[AnimeListEntry]) -> None:
        text = self._serialize(entries)
        self._sn_list_repo.write_raw(text)

    # -- DTO ↔ model conversion ------------------------------------------

    @staticmethod
    def _dto_to_entry(
        dto: AnimeListEntryDTO,
        username_cache: dict[str, str],
    ) -> AnimeListEntry:
        user_id: str | None = dto.user_id
        owner_username = username_cache.get(user_id) if user_id else None
        return AnimeListEntry(
            sn=dto.sn,
            enabled=dto.enabled,
            mode=dto.mode,  # type: ignore[arg-type]
            tag=dto.tag,
            season=dto.season,
            anime_name=dto.anime_name,
            comment=dto.comment,
            owner_id=user_id,
            owner_username=owner_username,
        )

    @staticmethod
    def _entry_to_dto(entry: AnimeListEntry, sort_order: int) -> AnimeListEntryDTO:
        return AnimeListEntryDTO(
            sn=entry.sn,
            enabled=entry.enabled,
            mode=entry.mode,
            tag=entry.tag,
            season=entry.season,
            comment=entry.comment,
            sort_order=sort_order,
        )

    # -- parsing ---------------------------------------------------------

    @classmethod
    def _parse(cls, raw: str) -> _List[AnimeListEntry]:
        entries: _List[AnimeListEntry] = []
        current_tag = ''
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Category header: ``@tag`` or bare ``@`` / ``@ ``
            if line.startswith('@'):
                tag = line[1:].strip()
                current_tag = tag
                continue

            entry = cls._parse_data_line(line, current_tag)
            if entry is not None:
                entries.append(entry)
                continue

            # Commented-out data row: ``# <sn> ...`` → disabled entry.
            if line.startswith('#'):
                stripped = line[1:].lstrip()
                disabled = cls._parse_data_line(stripped, current_tag)
                if disabled is not None:
                    disabled.enabled = False
                    entries.append(disabled)
                    continue

            # Otherwise it's a pure comment line — skip silently.

        return entries

    # ``<sn>`` possibly followed by a ``mode`` token, possibly followed by
    # an inline ``# comment``.
    _ROW_RE: T.ClassVar[re.Pattern[str]] = re.compile(
        r"""^
        (?P<sn>\d+)
        (?:\s+(?P<mode>[^\s#][^\s#]*))?   # mode token: no leading '#'
        (?:\s*\#\s*(?P<comment>.*?))?
        \s*$
        """,
        re.VERBOSE,
    )

    @classmethod
    def _parse_data_line(cls, line: str, tag: str) -> AnimeListEntry | None:
        """Parse a data row; return None if ``line`` is not a data row."""
        match = cls._ROW_RE.match(line)
        if not match:
            return None
        sn_text = match.group('sn')
        mode_text = match.group('mode')
        comment = (match.group('comment') or '').strip()

        mode: AnimeListMode | None
        if mode_text is None:
            mode = None
        elif mode_text in _VALID_MODES:
            mode = T.cast('AnimeListMode', mode_text)
        else:
            # Unknown token: treat as "no mode specified" so the settings
            # default applies, matching the behaviour of ``read_sn_list``.
            mode = None

        return AnimeListEntry(
            sn=int(sn_text),
            enabled=True,
            mode=mode,
            tag=tag,
            comment=comment,
        )

    # -- serialization ---------------------------------------------------

    @staticmethod
    def _serialize(entries: _List[AnimeListEntry]) -> str:
        # Group by tag, preserving first-occurrence order.
        groups: dict[str, _List[AnimeListEntry]] = {}
        for entry in entries:
            groups.setdefault(entry.tag, []).append(entry)

        lines: list[str] = []
        for tag, group in groups.items():
            header = f'@{tag}' if tag else '@'
            lines.append(header)
            for entry in group:
                lines.append(AnimeListService._format_entry(entry))

        return '\n'.join(lines) + ('\n' if lines else '')

    @staticmethod
    def _format_entry(entry: AnimeListEntry) -> str:
        prefix = '' if entry.enabled else '# '
        mode_part = f' {entry.mode}' if entry.mode else ''
        comment_part = f'  # {entry.comment}' if entry.comment else ''
        return f'{prefix}{entry.sn}{mode_part}{comment_part}'

    # -- enrichment ------------------------------------------------------

    def _enrich(self, entries: _List[AnimeListEntry]) -> None:
        """Populate ``anime_name`` + counts by querying the DB per sn.

        When ``entry.anime_name`` is already set (cached by UpdateLoop),
        we skip the anime_repo.read() lookup for the name and go straight
        to count_by_anime_name. This means the UI shows the title before
        any episode has finished downloading.

        Best-effort: if the DB has no row for an sn, the fields stay at
        their defaults. An underlying DB error propagates — that's a real
        bug the caller should surface, not a transient condition to hide.
        """
        if not entries:
            return

        for entry in entries:
            # Prefer the cached name stored on the list entry itself.
            resolved_name: str | None = entry.anime_name

            if not resolved_name:
                # Fall back to the downloaded-episodes table.
                row = self._anime_repo.read(entry.sn)
                if row is None:
                    continue
                resolved_name = row.anime_name
                if not resolved_name:
                    continue
                entry.anime_name = resolved_name

            known, downloaded = self._anime_repo.count_by_anime_name(resolved_name)
            entry.known_episodes = known
            entry.downloaded_episodes = downloaded


get_animelist_service = container_bound(
    lambda c: AnimeListService(
        c.sn_list_repo,
        c.anime_repo,
        c.anime_list_entry_repo,
        c.user_repo,
    )
)
"""FastAPI dependency resolver for :class:`AnimeListService`."""

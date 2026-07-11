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
* downloader: ``list_entries`` returns only the caller's own entries — a
  downloader must not be able to see another user's watchlist.
  ``replace_entries`` rejects any entry whose ``owner_id`` differs from the
  caller's id (returns HTTP 400).  Creating new entries always assigns
  ``owner_id = current_user.id``.
"""

from __future__ import annotations

import functools
import re
import typing as T

import anyio.to_thread
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


def _update_duplicate_flag(
    repo: AnimeListEntryRepository,
    row_id: int,
    duplicate_of_entry_id: int | None,
    enabled: bool,
) -> None:
    """Sync helper: update ``duplicate_of_entry_id`` and ``enabled`` on one row."""
    import sqlalchemy as _sa

    from ..persistence.models import AnimeListEntryRow

    with repo._db.session() as session:
        session.execute(
            _sa.update(AnimeListEntryRow)
            .where(AnimeListEntryRow.id == row_id)
            .values(duplicate_of_entry_id=duplicate_of_entry_id, enabled=enabled)
        )


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

    async def list_entries(self, user: UserRow) -> _List[AnimeListEntry]:
        """Return the anime list visible to the given user.

        * admin — every entry across all users, each annotated with
          ``owner_id`` / ``owner_username``.
        * downloader — only the caller's own entries.  Returning every
          user's watchlist to any downloader would leak other users'
          private anime lists (RBAC hygiene), so the result is scoped to
          ``owner_id == user.id``.

        The username cache and duplicate-resolution lookup are still built
        from the *full* entry set regardless of role: a downloader's own
        entry may be flagged as a duplicate of another user's entry, and
        that cross-reference (bangumi name + owner username, not the full
        watchlist) must still resolve correctly for the UI tooltip.
        """
        if self._anime_list_entry_repo is None:
            # Fallback to legacy flat-file path (e.g. during migration).
            return await self.list()

        entry_repo = self._anime_list_entry_repo
        all_dtos = await anyio.to_thread.run_sync(entry_repo.list_all)

        # Build username cache to avoid N+1 queries.
        user_ids = {dto.user_id for dto in all_dtos if dto.user_id is not None}
        username_cache: dict[str, str] = {}
        user_repo = self._user_repo
        if user_repo is not None:
            for uid in user_ids:
                row = await anyio.to_thread.run_sync(functools.partial(user_repo.get, uid))
                if row is not None:
                    username_cache[uid] = row.username

        # Also build a cache from entry id → (anime_name, owner_username) for
        # resolving duplicate_of_entry_id pointers.
        id_to_dto: dict[int, AnimeListEntryDTO] = {dto.id: dto for dto in all_dtos if dto.id is not None}

        visible_dtos = all_dtos if user.role == 'admin' else [dto for dto in all_dtos if dto.user_id == user.id]

        entries = [self._dto_to_entry(dto, username_cache, id_to_dto) for dto in visible_dtos]

        await self._enrich(entries)
        return entries

    async def replace_entries(self, user: UserRow, entries: _List[AnimeListEntry]) -> None:
        """Replace the anime list entries according to the caller's role.

        * admin: accepts entries that may carry an explicit ``owner_id``; any
          entry whose ``owner_id`` is ``None`` is assigned to the calling admin.
          Groups entries by owner and replaces each owner's slice atomically.
        * downloader: only the caller's own entries are allowed.  Any entry
          whose ``owner_id`` is set to a different user id raises HTTP 400.
          ``owner_id=None`` entries are silently assigned to the caller.

        Duplicate ``anime_name`` detection (Feature B) is applied to every
        entry whose ``anime_name`` is set: if another entry with the same
        name (case-insensitive trim) already exists, the new / updated entry
        is forced ``enabled=False`` and ``duplicate_of_entry_id`` is set.
        """
        entry_repo_rw = self._anime_list_entry_repo
        if entry_repo_rw is None:
            # Fallback to legacy flat-file path.
            await self.replace_all(entries)
            return

        # Feature B: if any entry explicitly sets enabled=True while having a
        # duplicate_of_entry_id, reject with 400.
        for entry in entries:
            if entry.duplicate_of_entry_id is not None and entry.enabled:
                raise fastapi.HTTPException(
                    status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                    detail='cannot_enable_duplicate',
                )

        if user.role == 'admin':
            # Group by owner_id (None → caller's id).
            groups: dict[str, _List[AnimeListEntry]] = {}
            for entry in entries:
                oid = entry.owner_id if entry.owner_id is not None else user.id
                groups.setdefault(oid, []).append(entry)

            # Ensure owners absent from the save payload get their slice
            # cleared.  Also always include the calling admin so that
            # deleting their last entry persists as an empty list.
            existing_owner_ids = await anyio.to_thread.run_sync(entry_repo_rw.list_all_owner_ids)
            groups.setdefault(user.id, [])
            for oid in existing_owner_ids:
                groups.setdefault(oid, [])

            for owner_id, owner_entries in groups.items():
                dtos = [self._entry_to_dto(e, idx) for idx, e in enumerate(owner_entries)]
                await anyio.to_thread.run_sync(functools.partial(entry_repo_rw.replace_all_for_user, owner_id, dtos))
        else:
            # Downloader: reject entries with a foreign owner_id.
            for entry in entries:
                if entry.owner_id is not None and entry.owner_id != user.id:
                    raise fastapi.HTTPException(
                        status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                        detail=(f'Entry sn={entry.sn} owner_id={entry.owner_id!r} does not belong to the current user'),
                    )
            dtos = [self._entry_to_dto(e, idx) for idx, e in enumerate(entries)]
            await anyio.to_thread.run_sync(functools.partial(entry_repo_rw.replace_all_for_user, user.id, dtos))

        # Feature B: after all slices are persisted, apply duplicate detection.
        # We do this as a second pass so every entry's id is now known and
        # cross-user name collisions can be detected correctly.
        await self._apply_duplicate_flags(entry_repo_rw)

    # -- duplicate-enable guard ------------------------------------------

    async def check_enable_allowed(self, entry: AnimeListEntry) -> None:
        """Raise HTTP 400 if the caller attempts to enable a duplicate entry."""
        if entry.duplicate_of_entry_id is not None and entry.enabled:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail='cannot_enable_duplicate',
            )

    # -- duplicate detection pass ----------------------------------------

    async def _apply_duplicate_flags(
        self,
        entry_repo: AnimeListEntryRepository,
    ) -> None:
        """Scan all entries and set/clear ``duplicate_of_entry_id`` as needed.

        For each entry with a non-empty ``anime_name``:
        - The earliest entry (by id) with that name is the "source" — its
          ``duplicate_of_entry_id`` is cleared.
        - All later entries with the same name get ``duplicate_of_entry_id``
          set to the source id and are forced ``enabled=False``.
        - Entries without an ``anime_name`` are untouched.
        """
        all_dtos = await anyio.to_thread.run_sync(entry_repo.list_all)

        # Group by normalised name, preserving insertion order (already sorted
        # by user_id then sort_order from list_all, but we sort by id to find
        # the canonical "earliest" entry).
        name_groups: dict[str, _List[AnimeListEntryDTO]] = {}
        for dto in all_dtos:
            if dto.anime_name and dto.anime_name.strip():
                key = dto.anime_name.strip().lower()
                name_groups.setdefault(key, []).append(dto)

        for dtos_for_name in name_groups.values():
            # Sort by row id so the lowest id is always the "source".
            dtos_for_name.sort(key=lambda d: d.id or 0)
            source_id = dtos_for_name[0].id

            for idx, dto in enumerate(dtos_for_name):
                if dto.id is None:
                    continue
                if idx == 0:
                    # This is the source — clear any duplicate flag.
                    if dto.duplicate_of_entry_id is not None:
                        await anyio.to_thread.run_sync(
                            functools.partial(_update_duplicate_flag, entry_repo, dto.id, None, dto.enabled)
                        )
                else:
                    # Duplicate — force disabled and set pointer.
                    if dto.duplicate_of_entry_id != source_id or dto.enabled:
                        await anyio.to_thread.run_sync(
                            functools.partial(_update_duplicate_flag, entry_repo, dto.id, source_id, False)
                        )

    # -- legacy flat-file read (UpdateLoop / SnListService compat) -------

    async def list(self) -> _List[AnimeListEntry]:
        raw = await anyio.to_thread.run_sync(self._sn_list_repo.read_raw)
        entries = self._parse(raw)
        await self._enrich(entries)
        return entries

    # -- legacy flat-file write ------------------------------------------

    async def replace_all(self, entries: _List[AnimeListEntry]) -> None:
        text = self._serialize(entries)
        await anyio.to_thread.run_sync(functools.partial(self._sn_list_repo.write_raw, text))

    # -- DTO ↔ model conversion ------------------------------------------

    @staticmethod
    def _dto_to_entry(
        dto: AnimeListEntryDTO,
        username_cache: dict[str, str],
        id_to_dto: dict[int, AnimeListEntryDTO] | None = None,
    ) -> AnimeListEntry:
        user_id: str | None = dto.user_id
        owner_username = username_cache.get(user_id) if user_id else None

        # Resolve duplicate_of fields for the UI tooltip.
        dup_bangumi_name: str | None = None
        dup_owner_username: str | None = None
        if dto.duplicate_of_entry_id is not None and id_to_dto is not None:
            source = id_to_dto.get(dto.duplicate_of_entry_id)
            if source is not None:
                dup_bangumi_name = source.anime_name
                dup_owner_username = username_cache.get(source.user_id) if source.user_id else None

        return AnimeListEntry(
            sn=dto.sn,
            enabled=dto.enabled,
            mode=dto.mode,  # type: ignore[arg-type]
            tag=dto.tag,
            season=dto.season,
            custom_name=dto.custom_name,
            bilingual=dto.bilingual,
            anime_name=dto.anime_name,
            comment=dto.comment,
            owner_id=user_id,
            owner_username=owner_username,
            duplicate_of_entry_id=dto.duplicate_of_entry_id,
            duplicate_of_bangumi_name=dup_bangumi_name,
            duplicate_of_owner_username=dup_owner_username,
        )

    @staticmethod
    def _entry_to_dto(entry: AnimeListEntry, sort_order: int) -> AnimeListEntryDTO:
        return AnimeListEntryDTO(
            sn=entry.sn,
            enabled=entry.enabled,
            mode=entry.mode,
            tag=entry.tag,
            season=entry.season,
            anime_name=entry.anime_name,  # preserve cached name sent back from frontend
            custom_name=entry.custom_name,
            bilingual=entry.bilingual,
            comment=entry.comment,
            sort_order=sort_order,
            duplicate_of_entry_id=entry.duplicate_of_entry_id,
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

    async def _enrich(self, entries: _List[AnimeListEntry]) -> None:
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
                row = await anyio.to_thread.run_sync(functools.partial(self._anime_repo.read, entry.sn))
                if row is None:
                    continue
                resolved_name = row.anime_name
                if not resolved_name:
                    continue
                entry.anime_name = resolved_name

            known, downloaded = await anyio.to_thread.run_sync(
                functools.partial(self._anime_repo.count_by_anime_name, resolved_name)
            )
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

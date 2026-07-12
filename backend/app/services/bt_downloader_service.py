"""Orchestrates one feed-tick iteration of the BT downloader pipeline.

``run_iteration`` is the pseudocode from the approved plan:

    for feed in bt_feed_repo.list_enabled():
        raw = FeedFetcher.fetch(feed.url)
        entries = FeedFetcher.map_entries(raw, feed)
        for e in entries:
            row = bt_feed_entry_repo.insert_if_new(e)
            if row is None: continue
            matched = FilterMatcher.match(row.title, filters, hanzi_convert)
            if matched:
                transfer = PutioClient.add_transfer(row.link)
                bt_feed_entry_repo.mark_dispatched(row.id, matched.id, transfer.id)

Called synchronously from the ``bt_feed_tick`` dramatiq actor (via
``asyncio.to_thread``), same pattern as every other sync runner in this
codebase.
"""

from __future__ import annotations

import collections.abc
import contextlib
import typing as T

import opencc

from ..bt_downloader.feed_fetcher import FeedFetcher, FeedFetchError
from ..bt_downloader.filter_matcher import FilterMatcher
from ..bt_downloader.putio_client import PutioAuthError, PutioClientError

if T.TYPE_CHECKING:
    from ..bt_downloader.putio_client import PutioClient
    from ..downloader.progress import ProgressBus
    from ..logging_ import Logger
    from ..models import BtDownloaderSettings, BtFeedEntry, BtFilter
    from ..persistence.bt_feed_entry_repo import BtFeedEntryRepository
    from ..persistence.bt_feed_repo import BtFeedRepository
    from ..persistence.bt_filter_repo import BtFilterRepository
    from ..persistence.putio_token_repo import PutioTokenRepository
    from ..persistence.task_history_repo import TaskHistoryRepository
    from ..persistence.task_id_map_repo import TaskIdMapRepository

_LOG_TAG = 'BT下載器'

# task_history source tag for BT-originated rows — see TaskIdMapRepository,
# which allocates a collision-free task_sn (BASE_OFFSET + row_id) for
# (source, external_id) pairs so a BT entry_id can never collide with an
# animad Bahamut episode sn.
_TASK_HISTORY_SOURCE = 'bt'

# Hard cap on how many stored entries ``count_matching`` will scan — see
# BtDownloaderService.count_matching for the "most recent N" semantics.
_MATCH_COUNT_CAP = 10_000

# Hard cap on how many Put.io dispatches one run_iteration() tick will fire
# (fix #8) — a feed that suddenly matches thousands of entries must not
# fire thousands of Put.io API calls in a single tick. Entries beyond the
# cap are persisted as "matched but not dispatched" (via
# BtFeedEntryRepository.mark_matched) and picked up first on the next tick
# via list_pending_dispatch.
_MAX_PUTIO_DISPATCH_PER_TICK = 20


def _safe_int(value: object) -> int | None:
    """Best-effort int coercion for an untyped Put.io transfer JSON field."""
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except TypeError, ValueError:
        return None


def _bytes_to_mb(value: object) -> int | None:
    """Best-effort bytes->MB coercion for a Put.io transfer's ``size`` field."""
    raw = _safe_int(value)
    if raw is None:
        return None
    return int(raw / (1024 * 1024))


class BtDownloaderService:
    """Runs one feed-fetch + filter-match + Put.io-dispatch iteration."""

    def __init__(
        self,
        bt_feed_repo: BtFeedRepository,
        bt_filter_repo: BtFilterRepository,
        bt_feed_entry_repo: BtFeedEntryRepository,
        putio_client_factory: collections.abc.Callable[[str], PutioClient],
        putio_token_repo: PutioTokenRepository,
        settings: BtDownloaderSettings,
        *,
        feed_fetcher: FeedFetcher | None = None,
        filter_matcher: FilterMatcher | None = None,
        logger: Logger | None = None,
        notify_event_send: collections.abc.Callable[..., None] | None = None,
        task_history_repo: TaskHistoryRepository | None = None,
        task_id_map_repo: TaskIdMapRepository | None = None,
        progress_bus: ProgressBus | None = None,
    ) -> None:
        self._bt_feed_repo = bt_feed_repo
        self._bt_filter_repo = bt_filter_repo
        self._bt_feed_entry_repo = bt_feed_entry_repo
        self._putio_client_factory = putio_client_factory
        self._putio_token_repo = putio_token_repo
        self._settings = settings
        self._feed_fetcher = feed_fetcher if feed_fetcher is not None else FeedFetcher()
        self._filter_matcher = filter_matcher if filter_matcher is not None else FilterMatcher()
        self._logger = logger
        self._notify_event_send = notify_event_send
        self._task_history_repo = task_history_repo
        self._task_id_map_repo = task_id_map_repo
        self._progress_bus = progress_bus
        # Lazily constructed + cached (like FilterMatcher._get_converter) —
        # opencc.OpenCC() loads a dictionary, so it must not be recreated
        # once per iteration.
        self._hanzi_converter: opencc.OpenCC | None = None

    def run_iteration(self) -> None:
        has_token = self._putio_token_repo.exists_and_nonempty()
        putio_client: PutioClient | None = None
        filters: list[BtFilter] = []
        filters_by_id: dict[int, BtFilter] = {}
        # Sticky within this iteration: once the token proves invalid, stop
        # spending API calls dispatching further matches — new entries are
        # still fetched and inserted so nothing is lost once the token is fixed.
        auth_failed = False
        # Tick-wide dispatch budget (fix #8) — shared between the pending-
        # dispatch drain below and the newly-matched entries processed in
        # the per-feed loop.
        dispatch_count = 0
        deferred_count = 0

        if has_token:
            filters = self._bt_filter_repo.list_all()
            filters_by_id = {f.id: f for f in filters if f.id is not None}
            putio_client = self._putio_client_factory(self._putio_token_repo.read())

        # Pick up entries a previous tick matched but could not dispatch
        # because it hit the cap — oldest match first — before spending any
        # of this tick's budget on newly fetched entries.
        if putio_client is not None and not auth_failed:
            pending = self._bt_feed_entry_repo.list_pending_dispatch(_MAX_PUTIO_DISPATCH_PER_TICK)
            for row in pending:
                if auth_failed or dispatch_count >= _MAX_PUTIO_DISPATCH_PER_TICK:
                    break
                filter_id = row.matched_filter_id
                if filter_id is None:
                    continue  # defensive — the query guarantees this
                filter_row = filters_by_id.get(filter_id)
                filter_name = filter_row.name if filter_row is not None else None
                feed = self._bt_feed_repo.get(row.feed_id)
                feed_name = feed.name if feed is not None else ''
                try:
                    dispatched = self._try_dispatch(
                        row, putio_client, filter_id=filter_id, filter_name=filter_name, feed_name=feed_name
                    )
                except PutioAuthError as exc:
                    auth_failed = True
                    self._log(f'Put.io token 已失效: {exc}')
                    self._emit(
                        'bt_failed', row=row, feed_name=feed_name, filter_name=filter_name, error_message=str(exc)
                    )
                    break
                if dispatched:
                    dispatch_count += 1

        # Rescan entries fetched within the retention window that never
        # matched any filter — a filter added AFTER an entry was fetched
        # would otherwise never get a chance to match it, since the
        # new-entry pass below only evaluates the filter list against
        # entries at insert time. Runs before the new-entry pass so a
        # long-orphaned entry is not further starved by this tick's fresh
        # dispatch budget.
        if putio_client is not None and not auth_failed:
            rescan = self._bt_feed_entry_repo.list_unmatched_within(self._settings.entry_retention_days)
            for row in rescan:
                if auth_failed or dispatch_count >= _MAX_PUTIO_DISPATCH_PER_TICK:
                    break
                matched = self._filter_matcher.match(row.title, filters, self._settings.hanzi_convert)
                if matched is None or matched.id is None:
                    continue

                feed_for_row = self._bt_feed_repo.get(row.feed_id)
                feed_name = feed_for_row.name if feed_for_row is not None else ''
                outcome, dispatched = self._dispatch_matched_entry(
                    row,
                    putio_client,
                    filter_id=matched.id,
                    filter_name=matched.name,
                    feed_name=feed_name,
                    dispatch_count=dispatch_count,
                )
                if outcome == 'auth_failed':
                    auth_failed = True
                    break
                if outcome == 'deferred':
                    deferred_count += 1
                    continue
                if dispatched:
                    dispatch_count += 1

        for feed in self._bt_feed_repo.list_enabled():
            try:
                raw = self._feed_fetcher.fetch(feed.url)
            except FeedFetchError as exc:
                self._log(f'RSS 抓取失敗 ({feed.name}): {exc}')
                continue

            for mapped in self._feed_fetcher.map_entries(raw, feed):
                title = T.cast('str', mapped['title'])
                if self._settings.hanzi_convert:
                    title = self._get_hanzi_converter().convert(title)
                # mypy narrowed `row` to BtFeedEntry via the outer `for row in rescan:`
                # loop above; reassigning to Optional here would widen it, so ignore
                # the assignment error — the None case is handled immediately below.
                row = self._bt_feed_entry_repo.insert_if_new(  # type: ignore[assignment]
                    feed.id,
                    T.cast('str', mapped['guid']),
                    title,
                    T.cast('str', mapped['link']),
                    mapped.get('author'),
                    mapped.get('published_at'),
                )
                if row is None:
                    continue  # already seen — UNIQUE(feed_id, guid) collision

                if not has_token or auth_failed or putio_client is None:
                    continue

                matched = self._filter_matcher.match(row.title, filters, self._settings.hanzi_convert)
                if matched is None or matched.id is None:
                    continue

                outcome, dispatched = self._dispatch_matched_entry(
                    row,
                    putio_client,
                    filter_id=matched.id,
                    filter_name=matched.name,
                    feed_name=feed.name,
                    dispatch_count=dispatch_count,
                )
                if outcome == 'auth_failed':
                    auth_failed = True
                    continue
                if outcome == 'deferred':
                    deferred_count += 1
                    continue
                if dispatched:
                    dispatch_count += 1

        if deferred_count > 0:
            self._log(f'BT tick hit dispatch cap; {deferred_count} entries deferred to next tick')

    def _dispatch_matched_entry(
        self,
        row: BtFeedEntry,
        putio_client: PutioClient,
        *,
        filter_id: int,
        filter_name: str | None,
        feed_name: str,
        dispatch_count: int,
    ) -> tuple[str, bool]:
        """Dispatch (or defer) an entry that just matched *filter_id*.

        Shared by the new-entry pass and the rescan pass of ``run_iteration``
        so the cap-handling / auth-failure branching lives in exactly one
        place. ``dispatch_count`` is this tick's running dispatch count,
        passed in rather than read off ``self`` because it is tick-local
        state owned by ``run_iteration``. Takes the same ``filter_id`` /
        ``filter_name`` split as :meth:`_try_dispatch` (rather than a full
        ``BtFilter``) so callers narrow ``BtFilter.id: int | None`` to
        ``int`` once, at the match site, instead of here.

        Returns ``(outcome, dispatched)``:

        * ``('deferred', False)`` — the per-tick dispatch cap was already
          hit; the match is persisted via ``mark_matched`` so
          ``list_pending_dispatch`` picks the entry up first next tick.
        * ``('auth_failed', False)`` — ``add_transfer`` raised
          :class:`PutioAuthError`; already logged/emitted. The caller should
          set its tick-sticky ``auth_failed`` flag and stop dispatching.
        * ``('dispatched', True)`` / ``('skipped', False)`` — mirrors
          :meth:`_try_dispatch`'s return value for a non-auth failure (e.g.
          :class:`PutioClientError`), which is itself already logged/emitted
          and is not tick-fatal.
        """
        if dispatch_count >= _MAX_PUTIO_DISPATCH_PER_TICK:
            # Cap hit — persist the match so list_pending_dispatch picks
            # this entry up first on the next tick.
            self._bt_feed_entry_repo.mark_matched(row.id, filter_id)
            return 'deferred', False

        try:
            dispatched = self._try_dispatch(
                row, putio_client, filter_id=filter_id, filter_name=filter_name, feed_name=feed_name
            )
        except PutioAuthError as exc:
            self._log(f'Put.io token 已失效: {exc}')
            self._emit('bt_failed', row=row, feed_name=feed_name, filter_name=filter_name, error_message=str(exc))
            return 'auth_failed', False

        return ('dispatched', True) if dispatched else ('skipped', False)

    def _try_dispatch(
        self,
        row: BtFeedEntry,
        putio_client: PutioClient,
        *,
        filter_id: int,
        filter_name: str | None,
        feed_name: str,
    ) -> bool:
        """Add *row* as a Put.io transfer and record the dispatch.

        Raises :class:`PutioAuthError` unchanged so the caller can set its
        tick-sticky ``auth_failed`` flag and stop dispatching further
        entries. A :class:`PutioClientError` (or a transfer response missing
        ``id``) is treated as a per-entry failure: logged, a ``bt_failed``
        event is emitted, and ``False`` is returned so the caller moves on
        to the next entry.
        """
        try:
            transfer = putio_client.add_transfer(row.link)
        except PutioAuthError:
            raise
        except PutioClientError as exc:
            self._log(f'Put.io 派送失敗 ({row.title}): {exc}')
            self._emit('bt_failed', row=row, feed_name=feed_name, filter_name=filter_name, error_message=str(exc))
            return False

        transfer_id = transfer.get('id')
        if transfer_id is None:
            return False
        self._bt_feed_entry_repo.mark_dispatched(row.id, filter_id, T.cast('int', transfer_id))
        # percent_done/size are usually 0/absent at dispatch time (the
        # transfer has barely started) but are forwarded for payload-shape
        # symmetry with the 'bt_status_update' events LandingWorker later
        # fires for the same entry — _format_bt_message's 'bt_dispatched'
        # branch doesn't render either field today.
        percent_done = _safe_int(transfer.get('percent_done'))
        file_size_mb = _bytes_to_mb(transfer.get('size'))
        self._emit(
            'bt_dispatched',
            row=row,
            feed_name=feed_name,
            filter_name=filter_name,
            putio_transfer_id=T.cast('int', transfer_id),
            percent_done=percent_done,
            file_size_mb=file_size_mb,
        )
        self._record_task_history_start(row, filter_name=filter_name, feed_name=feed_name)
        self._start_progress(row, filter_name=filter_name, feed_name=feed_name)
        return True

    def count_matching(self, keywords: list[str]) -> tuple[int, bool]:
        """Count stored entries whose title matches every keyword in *keywords*.

        Mirrors :meth:`FilterMatcher.match`'s AND-keyword / hanzi-convert
        semantics for a not-yet-persisted candidate filter. An empty
        *keywords* list matches nothing (same "empty filter never matches"
        convention as ``FilterMatcher``), returning ``(0, False)`` without
        touching the database.

        The scan is capped at :data:`_MATCH_COUNT_CAP` most-recently-fetched
        entries; the second return value is ``True`` when more than that many
        entries exist, so the caller knows the count only covers the recent
        subset.
        """
        if not keywords:
            return 0, False

        entries = self._bt_feed_entry_repo.list_most_recent(_MATCH_COUNT_CAP + 1)
        over_cap = len(entries) > _MATCH_COUNT_CAP
        if over_cap:
            entries = entries[:_MATCH_COUNT_CAP]

        hanzi_convert = self._settings.hanzi_convert
        count = sum(1 for entry in entries if self._filter_matcher.match_all(entry.title, keywords, hanzi_convert))
        return count, over_cap

    def _get_hanzi_converter(self) -> opencc.OpenCC:
        if self._hanzi_converter is None:
            self._hanzi_converter = opencc.OpenCC('s2t')
        return self._hanzi_converter

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)

    def _emit(
        self,
        event: str,
        *,
        row: BtFeedEntry,
        feed_name: str,
        filter_name: str | None,
        putio_transfer_id: int | None = None,
        error_message: str | None = None,
        percent_done: int | None = None,
        file_size_mb: int | None = None,
    ) -> None:
        """Fire a BT lifecycle event to Telegram (best-effort, never raises)."""
        if self._notify_event_send is None:
            return
        with contextlib.suppress(Exception):
            payload: dict[str, object] = {
                'event': event,
                'title': row.title,
                'feed_name': feed_name,
                'filter_name': filter_name,
                'putio_transfer_id': putio_transfer_id,
                'entry_id': row.id,
            }
            if error_message is not None:
                payload['error_message'] = error_message[:200]
            if percent_done is not None:
                payload['percent_done'] = percent_done
            if file_size_mb is not None:
                payload['file_size_mb'] = file_size_mb
            self._notify_event_send(kwargs=payload)

    # ------------------------------------------------------------------ progress bus

    def _start_progress(self, row: BtFeedEntry, *, filter_name: str | None, feed_name: str) -> None:
        """Register the freshly-dispatched entry with the live ProgressBus (best-effort).

        Reuses the same ``TaskIdMapRepository``-derived ``sn`` as
        :meth:`_record_task_history_start` so the ProgressBus entry and the
        task_history row are linked via the same sn / (source, external_id)
        pairing the frontend uses to identify BT rows in the monitor UI.
        """
        if self._progress_bus is None or self._task_id_map_repo is None:
            return
        with contextlib.suppress(Exception):
            sn = self._task_id_map_repo.allocate(_TASK_HISTORY_SOURCE, str(row.id))
            self._progress_bus.start(
                sn,
                row.title,
                status='等待 Put.io',
                bangumi_name=filter_name or feed_name,
                source=_TASK_HISTORY_SOURCE,
                external_id=str(row.id),
            )

    # ------------------------------------------------------------------ task_history

    def _record_task_history_start(self, row: BtFeedEntry, *, filter_name: str | None, feed_name: str) -> None:
        """Open a task_history row for a freshly-dispatched BT entry (best-effort).

        Mirrors ``BilibiliRunner``'s use of ``TaskIdMapRepository`` to derive
        a collision-free ``task_sn`` (``BASE_OFFSET + row_id``) rather than
        using the raw ``entry_id`` — animad sn values are Bahamut episode
        ids and BT's own ``entry_id`` sequence starts at 1, so a raw-id
        collision is a real (if unlikely) risk this sidesteps entirely.
        Wrapped in ``suppress`` so a task_history write failure never breaks
        the dispatch flow, same as ``_emit``'s Telegram best-effort send.
        """
        if self._task_history_repo is None or self._task_id_map_repo is None:
            return
        with contextlib.suppress(Exception):
            task_sn = self._task_id_map_repo.allocate(_TASK_HISTORY_SOURCE, str(row.id))
            self._task_history_repo.record_start(
                task_sn,
                row.title,
                owner_id=None,
                bangumi_name=filter_name or feed_name,
                source=_TASK_HISTORY_SOURCE,
                external_id=str(row.id),
            )

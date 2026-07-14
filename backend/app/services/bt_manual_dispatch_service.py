"""Manual Put.io dispatch for a single BtFeedEntry, invoked from bt_api.

Separate from BtDownloaderService.run_iteration's automated dispatch
path so the two don't need to share mutable state; wires the same
PutioClient + notify_bt_dispatched-style callback.
"""

from __future__ import annotations

import collections.abc
import contextlib
import typing as T

from ..bt_downloader.putio_client import PutioAuthError, PutioClientError, PutioTransferAlreadyAddedError

if T.TYPE_CHECKING:
    from ..bt_downloader.putio_client import PutioClient
    from ..downloader.progress import ProgressBus
    from ..logging_ import Logger
    from ..models import BtFeedEntry
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


class ManualDispatchError(Exception):
    """Base class for every typed failure :meth:`BtManualDispatchService.dispatch` raises."""


class EntryNotFound(ManualDispatchError):
    """No ``bt_feed_entry`` row exists for the given ``entry_id`` — maps to HTTP 404."""


class PutioTokenMissing(ManualDispatchError):
    """No Put.io OAuth token is configured — maps to HTTP 400."""


class PutioAuthFailed(ManualDispatchError):
    """Put.io rejected the configured token (401) — maps to HTTP 502."""


class PutioApiError(ManualDispatchError):
    """Put.io returned a non-2xx/non-401 response, or the request failed — maps to HTTP 502."""


class BtManualDispatchService:
    """Dispatches (or re-dispatches) a single :class:`BtFeedEntry` to Put.io on demand.

    Mirrors the dispatch logic of ``BtDownloaderService._try_dispatch`` but is
    driven by an explicit ``entry_id`` from the API rather than a filter
    match, and surfaces failures as typed exceptions instead of swallowing
    them — a manual dispatch request needs a real HTTP error back to the
    admin who clicked the button, unlike the best-effort automated tick.
    """

    def __init__(
        self,
        bt_feed_entry_repo: BtFeedEntryRepository,
        putio_client_factory: collections.abc.Callable[[str], PutioClient],
        putio_token_repo: PutioTokenRepository,
        *,
        bt_feed_repo: BtFeedRepository | None = None,
        bt_filter_repo: BtFilterRepository | None = None,
        notify_event_send: collections.abc.Callable[..., None] | None = None,
        logger: Logger | None = None,
        task_history_repo: TaskHistoryRepository | None = None,
        task_id_map_repo: TaskIdMapRepository | None = None,
        progress_bus: ProgressBus | None = None,
    ) -> None:
        self._bt_feed_entry_repo = bt_feed_entry_repo
        self._putio_client_factory = putio_client_factory
        self._putio_token_repo = putio_token_repo
        self._bt_feed_repo = bt_feed_repo
        self._bt_filter_repo = bt_filter_repo
        self._notify_event_send = notify_event_send
        self._logger = logger
        self._task_history_repo = task_history_repo
        self._task_id_map_repo = task_id_map_repo
        self._progress_bus = progress_bus

    def dispatch(self, entry_id: int, user_id: str) -> dict[str, object]:
        """Send *entry_id* to Put.io, overwriting any previous ``putio_transfer_id``.

        Returns ``{'transfer_id': int, 'status': str}`` on success —
        including the benign case where Put.io reports the link is already
        an active transfer on the account (``status`` is ``'ALREADY_ADDED'``
        and ``transfer_id`` is this entry's previously-known transfer id, or
        ``0`` if it was never itself dispatched locally). Raises
        :class:`EntryNotFound`, :class:`PutioTokenMissing`,
        :class:`PutioAuthFailed`, or :class:`PutioApiError` on a real
        failure — callers (``bt_api``) translate these to the matching HTTP
        status.
        """
        row = self._bt_feed_entry_repo.get(entry_id)
        if row is None:
            raise EntryNotFound(f'entry_id={entry_id} not found')

        if not self._putio_token_repo.exists_and_nonempty():
            raise PutioTokenMissing('Put.io token 未設定')

        putio_client = self._putio_client_factory(self._putio_token_repo.read())

        try:
            transfer = putio_client.add_transfer(row.link)
        except PutioAuthError as exc:
            self._log_error(f'Put.io token 已失效: {exc}')
            self._emit('bt_failed', row=row, error_message=str(exc))
            raise PutioAuthFailed(str(exc)) from exc
        except PutioTransferAlreadyAddedError as exc:
            # Benign: the link is already an active transfer on Put.io
            # (e.g. the user clicked 重新派送 while the earlier dispatch is
            # still in flight, or a different entry already dispatched the
            # same underlying link). Not a failure — no bt_failed
            # notification and no PutioApiError/502: return a friendly
            # "already remote" outcome so the API responds 200.
            self._log_info(f'Put.io transfer 已存在，略過重複派送 (entry_id={entry_id}): {exc}')
            return {'transfer_id': row.putio_transfer_id or 0, 'status': 'ALREADY_ADDED'}
        except PutioClientError as exc:
            self._log_error(f'Put.io 派送失敗 ({row.title}): {exc}')
            self._emit('bt_failed', row=row, error_message=str(exc))
            raise PutioApiError(str(exc)) from exc

        transfer_id = transfer.get('id')
        if transfer_id is None:
            error_message = 'Put.io 回應缺少 transfer id'
            self._log_error(f'{error_message} (entry_id={entry_id})')
            self._emit('bt_failed', row=row, error_message=error_message)
            raise PutioApiError(error_message)
        transfer_id = T.cast('int', transfer_id)

        self._bt_feed_entry_repo.mark_dispatched_manual(entry_id, transfer_id)
        self._log_info(f'手動派送 entry_id={entry_id} user={user_id} transfer_id={transfer_id}')
        self._emit('bt_dispatched', row=row, putio_transfer_id=transfer_id)
        self._record_task_history_start(row)
        self._start_progress(row)

        return {'transfer_id': transfer_id, 'status': 'IN_QUEUE'}

    # ------------------------------------------------------------------ telegram

    def _emit(
        self,
        event: str,
        *,
        row: BtFeedEntry,
        putio_transfer_id: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Fire a BT lifecycle event to Telegram (best-effort, never raises).

        Same payload shape as ``BtDownloaderService._emit`` / ``LandingWorker._emit``
        so the Telegram-side rendering doesn't need to special-case a
        manually-triggered dispatch.
        """
        if self._notify_event_send is None:
            return
        with contextlib.suppress(Exception):
            payload: dict[str, object] = {
                'event': event,
                'title': row.title,
                'feed_name': self._feed_name(row.feed_id),
                'filter_name': self._filter_name(row.matched_filter_id),
                'putio_transfer_id': putio_transfer_id,
                'entry_id': row.id,
            }
            if error_message is not None:
                payload['error_message'] = error_message[:200]
            self._notify_event_send(kwargs=payload)

    # ------------------------------------------------------------------ progress bus

    def _start_progress(self, row: BtFeedEntry) -> None:
        """Register the manually-dispatched entry with the live ProgressBus (best-effort).

        Same sn derivation and payload shape as
        ``BtDownloaderService._start_progress`` — see that docstring.
        """
        if self._progress_bus is None or self._task_id_map_repo is None:
            return
        with contextlib.suppress(Exception):
            filter_name = self._filter_name(row.matched_filter_id)
            feed_name = self._feed_name(row.feed_id)
            sn = self._task_id_map_repo.allocate(_TASK_HISTORY_SOURCE, str(row.id))
            self._progress_bus.start(
                sn,
                row.title,
                status='等待 Put.io',
                bangumi_name=filter_name or feed_name,
                source=_TASK_HISTORY_SOURCE,
                external_id=str(row.id),
            )

    def _feed_name(self, feed_id: int) -> str:
        if self._bt_feed_repo is not None:
            feed = self._bt_feed_repo.get(feed_id)
            if feed is not None:
                return feed.name
        return str(feed_id)

    def _filter_name(self, filter_id: int | None) -> str | None:
        if filter_id is None or self._bt_filter_repo is None:
            return None
        filt = self._bt_filter_repo.get(filter_id)
        return filt.name if filt is not None else None

    # ------------------------------------------------------------------ task_history

    def _record_task_history_start(self, row: BtFeedEntry) -> None:
        """Open a task_history row for a manually-dispatched BT entry (best-effort).

        Same collision-free ``task_sn`` derivation as
        ``BtDownloaderService._record_task_history_start`` — see that
        docstring. Wrapped in ``suppress`` so a task_history write failure
        never breaks the dispatch flow.
        """
        if self._task_history_repo is None or self._task_id_map_repo is None:
            return
        with contextlib.suppress(Exception):
            task_sn = self._task_id_map_repo.allocate(_TASK_HISTORY_SOURCE, str(row.id))
            filter_name = self._filter_name(row.matched_filter_id)
            feed_name = self._feed_name(row.feed_id)
            self._task_history_repo.record_start(
                task_sn,
                row.title,
                owner_id=None,
                bangumi_name=filter_name or feed_name,
                source=_TASK_HISTORY_SOURCE,
                external_id=str(row.id),
            )

    # ------------------------------------------------------------------ logging

    def _log_error(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)

    def _log_info(self, message: str) -> None:
        if self._logger is not None:
            self._logger.info(None, _LOG_TAG, message, display=False)

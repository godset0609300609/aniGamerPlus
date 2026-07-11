"""Per-user Telegram chat watcher — the actual "download new media" loop.

Uses ``hydrogram.Client.add_handler(MessageHandler(...))`` (real-time push,
not polling) so new messages in a watched chat are downloaded the moment
they arrive, without a scheduler tick delay. One handler is registered per
bound user's client (via :class:`~app.tg_downloader.client_pool.TgClientPool`),
filtered to that user's watched ``chat_id`` set.

Every task_history / ProgressBus / telegram-notify write is wrapped in
``contextlib.suppress(Exception)`` — mirrors
``app.bt_downloader.landing_worker.LandingWorker``'s "a notification/history
failure must never break the download" contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import os
import pathlib
import time
import typing as T

import hydrogram
import hydrogram.filters
import hydrogram.handlers
import hydrogram.types

from ..downloader.filename import FilenameBuilder
from ..security.log_scrub import scrub_exception_for_log

if T.TYPE_CHECKING:
    import collections.abc

    from ..downloader.progress import ProgressBus
    from ..logging_ import Logger
    from ..models import TgWatchedChat
    from ..persistence.task_history_repo import TaskHistoryRepository
    from ..persistence.task_id_map_repo import TaskIdMapRepository
    from ..persistence.tg_downloaded_media_repo import TgDownloadedMediaRepository
    from ..persistence.tg_watched_chat_repo import TgWatchedChatRepository

_TASK_HISTORY_SOURCE = 'tg'
_LOG_TAG = 'TG下載'

# Same time-based cadence as BT LandingWorker's landing-progress throttle,
# but a tighter percent-jump threshold (5 vs BT's 10) — TG-watched media is
# typically much smaller than a BT-landed file, so a 10-point jump would
# often mean only one or two updates for the whole download.
_PROGRESS_MIN_INTERVAL_SECONDS = 5.0
_PROGRESS_MIN_PERCENT_JUMP = 5

#: Per-user cap on concurrently in-flight ``client.download_media`` calls
#: (MEDIUM-6 of the security audit) — without this, N watched chats all
#: matching at once (e.g. a channel dumping a whole season) would fan out
#: into N unbounded concurrent downloads for the same user, competing for
#: disk/network with no backpressure, unlike every other download path in
#: this app (BT's landing loop is single-threaded per tick; the classic
#: scheduler has ``multi_thread``). One process-wide env var — simple
#: knob, no per-user override needed for a self-hosted single-operator app.
_MAX_CONCURRENT_DOWNLOADS_PER_USER_ENV_VAR = 'ANIGAMERPLUS_TG_MAX_CONCURRENT_DOWNLOADS_PER_USER'
_DEFAULT_MAX_CONCURRENT_DOWNLOADS_PER_USER = 3


class TgSavePathEscapesLandingRootError(Exception):
    """Raised when a watched chat's ``save_path`` would resolve outside the TG landing root.

    See :meth:`TgDownloadWatcher._confine` — the runtime half of HIGH-1's
    landing-root confinement guard. Caught by :meth:`TgDownloadWatcher.enqueue_message`,
    which logs and skips the download rather than letting it propagate and
    kill the message-handler loop.
    """


def _env_max_concurrent_downloads() -> int:
    """Read :data:`_MAX_CONCURRENT_DOWNLOADS_PER_USER_ENV_VAR`, defaulting to 3.

    Read once per :class:`TgDownloadWatcher` (at construction), not live —
    this is a startup-time concurrency knob, same treatment as e.g.
    ``segment_max_retry``, not a live-reloadable rate limit.
    """
    raw = os.environ.get(_MAX_CONCURRENT_DOWNLOADS_PER_USER_ENV_VAR, '')
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_CONCURRENT_DOWNLOADS_PER_USER
    return value if value > 0 else _DEFAULT_MAX_CONCURRENT_DOWNLOADS_PER_USER


#: Media-object attribute name on a hydrogram Message, keyed by our own
#: media-type vocabulary (matches ``tg_watched_chat.media_types``).
_MEDIA_ATTR_BY_TYPE: dict[str, str] = {
    'video': 'video',
    'document': 'document',
    'audio': 'audio',
    'photo': 'photo',
}


@dataclasses.dataclass(slots=True)
class _MatchedMedia:
    media_type: str
    file_id: str
    file_unique_id: str
    file_name: str
    file_size: int


def _extract_media(message: hydrogram.types.Message) -> _MatchedMedia | None:
    """Return the first matching media object on *message*, or ``None`` if it carries none we track."""
    for media_type, attr in _MEDIA_ATTR_BY_TYPE.items():
        media = getattr(message, attr, None)
        if media is None:
            continue
        file_name = getattr(media, 'file_name', None) or f'{media.file_unique_id}.{_guess_ext(media_type)}'
        return _MatchedMedia(
            media_type=media_type,
            file_id=media.file_id,
            file_unique_id=media.file_unique_id,
            file_name=file_name,
            file_size=getattr(media, 'file_size', 0) or 0,
        )
    return None


def _guess_ext(media_type: str) -> str:
    return {'video': 'mp4', 'audio': 'mp3', 'photo': 'jpg', 'document': 'bin'}.get(media_type, 'bin')


def _passes_filters(media: _MatchedMedia, watched: TgWatchedChat) -> bool:
    if media.media_type not in watched.media_types:
        return False
    size_mb = media.file_size / (1024 * 1024)
    if watched.size_min_mb is not None and size_mb < watched.size_min_mb:
        return False
    if watched.size_max_mb is not None and size_mb > watched.size_max_mb:
        return False
    if watched.format_whitelist:
        ext = pathlib.Path(media.file_name).suffix.lstrip('.').lower()
        if ext not in {e.lstrip('.').lower() for e in watched.format_whitelist}:
            return False
    return True


class TgDownloadWatcher:
    """Registers per-user message handlers and executes matched downloads."""

    def __init__(
        self,
        watched_chat_repo: TgWatchedChatRepository,
        downloaded_media_repo: TgDownloadedMediaRepository,
        bangumi_dir: pathlib.Path,
        *,
        landing_root: pathlib.Path | None = None,
        task_history_repo: TaskHistoryRepository | None = None,
        task_id_map_repo: TaskIdMapRepository | None = None,
        progress_bus: ProgressBus | None = None,
        notify_event_send: collections.abc.Callable[..., None] | None = None,
        logger: Logger | None = None,
        max_concurrent_downloads_per_user: int | None = None,
    ) -> None:
        self._watched_chat_repo = watched_chat_repo
        self._downloaded_media_repo = downloaded_media_repo
        # HIGH-1: every TG download — default per-chat directory or a
        # user-supplied ``save_path`` override alike — must resolve inside
        # this root (see ``_confine``). Defaults to *bangumi_dir* so a
        # deployment that never sets ``ANIGAMERPLUS_TG_LANDING_ROOT``
        # (see app.core) keeps its exact pre-fix directory layout.
        self._landing_root = landing_root if landing_root is not None else bangumi_dir
        self._task_history_repo = task_history_repo
        self._task_id_map_repo = task_id_map_repo
        self._progress_bus = progress_bus
        self._notify_event_send = notify_event_send
        self._logger = logger
        # user_id -> registered hydrogram.handlers.MessageHandler, so a
        # refresh (chat list changed) can remove the stale one first.
        self._handlers: dict[str, hydrogram.handlers.MessageHandler] = {}
        # MEDIUM-6: per-user cap on concurrently in-flight downloads — see
        # module docstring constants above. Lazily created per user (rather
        # than pre-allocated for every known user) since the user set isn't
        # known upfront.
        self._max_concurrent_downloads_per_user = max_concurrent_downloads_per_user or _env_max_concurrent_downloads()
        self._download_semaphores: dict[str, asyncio.Semaphore] = {}

    # ------------------------------------------------------------------ registration

    def register(self, user_id: str, client: hydrogram.Client) -> None:
        """(Re-)register the message handler for *user_id* on *client*.

        Safe to call repeatedly (e.g. after the user edits their watched-chat
        list) — the previous handler, if any, is removed first so chat_id
        filters never go stale.
        """
        self.unregister(user_id, client)

        watched = self._watched_chat_repo.list_enabled_by_user(user_id)
        if not watched:
            return
        chat_ids = [w.chat_id for w in watched]

        async def _on_message(_client: hydrogram.Client, message: hydrogram.types.Message) -> None:
            await self.handle_message(user_id, _client, message)

        handler = hydrogram.handlers.MessageHandler(_on_message, hydrogram.filters.chat(chat_ids))
        client.add_handler(handler)
        self._handlers[user_id] = handler

    def unregister(self, user_id: str, client: hydrogram.Client) -> None:
        handler = self._handlers.pop(user_id, None)
        if handler is not None:
            with contextlib.suppress(Exception):
                client.remove_handler(handler)

    # ------------------------------------------------------------------ core handler

    async def handle_message(self, user_id: str, client: hydrogram.Client, message: hydrogram.types.Message) -> None:
        """Evaluate one incoming (real-time) message against *user_id*'s watched-chat filters and download on match."""
        watched = self._watched_chat_repo.get(user_id, message.chat.id)
        if watched is None or not watched.enabled:
            return
        await self.enqueue_message(user_id, client, message, watched)

    async def enqueue_message(
        self,
        user_id: str,
        client: hydrogram.Client,
        message: hydrogram.types.Message,
        watched: TgWatchedChat,
    ) -> bool:
        """Evaluate *message* against *watched*'s filters and download it on match.

        Shared entry point for the real-time handler (``handle_message``,
        above) and :class:`~app.tg_downloader.backfill.TgBackfillService`'s
        historical scan — both need the exact same
        filter-match / dedup / download / bookkeeping pipeline, just
        triggered from different sources (a live hydrogram push vs. a
        ``get_chat_history`` walk). Keeping it here (rather than duplicating
        it in the backfill service) means a filter/notification/history
        change only has to be made once.

        Returns ``True`` if *message* passed every filter and a download was
        attempted (regardless of whether it ultimately succeeded — see the
        ``except`` branch below), ``False`` if it carries no tracked media,
        fails *watched*'s filters, is already recorded in
        ``tg_downloaded_media`` (``UNIQUE(user_id, chat_id, message_id)``
        dedup), or *watched*'s resolved ``save_path`` escapes the TG
        landing root (HIGH-1 security guard — logged and skipped rather
        than raised, so one misconfigured watched chat can't take down the
        whole message-handler loop).
        """
        media = _extract_media(message)
        if media is None:
            return False
        if not _passes_filters(media, watched):
            return False
        if self._downloaded_media_repo.exists(user_id, message.chat.id, message.id):
            return False

        try:
            dest_dir = self._resolve_save_dir(user_id, watched)
        except TgSavePathEscapesLandingRootError as exc:
            self._log_error(
                f'watched chat 的 save_path 逃逸 landing root，已跳過下載 '
                f'user_id={user_id} chat_id={message.chat.id} message_id={message.id}: '
                f'{scrub_exception_for_log(exc)}'
            )
            return False
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / FilenameBuilder.legalize(media.file_name)
        try:
            dest_path = self._confine(dest_path)
        except TgSavePathEscapesLandingRootError as exc:
            # Defense-in-depth: dest_dir was already confined above, and
            # FilenameBuilder.legalize neutralises path separators in the
            # filename, so this should be unreachable in practice — but the
            # actual bytes handed to hydrogram's download_media get one last
            # check right before the call, mirroring PutioClient.download_file's
            # own belt-and-suspenders re-check at its actual write site.
            self._log_error(
                f'解析後的下載路徑逃逸 landing root，已跳過下載 '
                f'user_id={user_id} chat_id={message.chat.id} message_id={message.id}: '
                f'{scrub_exception_for_log(exc)}'
            )
            return False

        sn = self._allocate_sn(message.chat.id, message.id)
        self._start_progress(sn, media.file_name, user_id=user_id, chat_title=watched.chat_title)
        history_row_id = self._start_history(sn, media.file_name, user_id, watched, message)
        self._emit('tg_started', watched, media, message)

        try:
            async with self._get_semaphore(user_id):
                downloaded = await client.download_media(
                    message,
                    file_name=str(dest_path),
                    progress=self._make_progress_callback(sn, watched, media, message),
                )
        except Exception as exc:  # noqa: BLE001 — surfaced via notify/history, loop keeps running
            self._log_error(
                f'下載失敗 user_id={user_id} chat_id={message.chat.id} message_id={message.id}: '
                f'{scrub_exception_for_log(exc)}'
            )
            self._emit('tg_failed', watched, media, message, error_message=str(exc))
            self._finish_history(history_row_id, final_status='下載失敗')
            self._finish_progress(sn, status='失敗')
            return True

        local_path = str(downloaded) if downloaded else str(dest_path)
        entry = self._downloaded_media_repo.insert_if_new(
            user_id,
            chat_id=message.chat.id,
            message_id=message.id,
            file_id=media.file_unique_id,
            file_name=media.file_name,
            file_size=media.file_size,
            local_path=local_path,
            progress_sn=sn,
        )
        self._log_info(f'已下載 {media.file_name}（user_id={user_id}, chat_id={message.chat.id}, entry={entry})')
        self._emit('tg_landed', watched, media, message, local_path=local_path)
        self._finish_history(history_row_id, final_status='下載完成', filename=media.file_name)
        self._finish_progress(sn, status='下載完成', filename=media.file_name)
        return True

    # ------------------------------------------------------------------ paths

    def _resolve_save_dir(self, user_id: str, watched: TgWatchedChat) -> pathlib.Path:
        """Resolve *watched*'s download directory, confined to the TG landing root.

        HIGH-1 security fix: previously a user-supplied ``watched.save_path``
        was used verbatim with no confinement whatsoever, letting a
        malicious/mistaken value (an absolute path elsewhere on disk, or a
        ``..``-relative escape) direct downloaded file content anywhere the
        process can write — an arbitrary file write. Every branch below now
        resolves through :meth:`_confine`, which raises
        :class:`TgSavePathEscapesLandingRootError` rather than returning a
        path outside :attr:`_landing_root`.
        """
        if watched.save_path:
            candidate = pathlib.Path(watched.save_path)
            if not candidate.is_absolute():
                candidate = self._landing_root / candidate
            return self._confine(candidate)
        safe_title = FilenameBuilder.legalize(watched.chat_title)
        return self._confine(self._landing_root / 'tg' / user_id / safe_title)

    def _confine(self, path: pathlib.Path) -> pathlib.Path:
        """Resolve *path* and confirm it lands inside :attr:`_landing_root`.

        Mirrors ``app.bt_downloader.putio_client.PutioClient.download_file``'s
        ``landing_dir`` escape guard — ``resolve()`` (not just string/``..``
        inspection) so a symlink planted inside the landing root that points
        outside it is caught too, not just a textual ``..`` traversal.
        """
        resolved = path.resolve()
        landing_root_resolved = self._landing_root.resolve()
        if not resolved.is_relative_to(landing_root_resolved):
            raise TgSavePathEscapesLandingRootError(
                f'resolved path {resolved} escapes TG landing root {landing_root_resolved}'
            )
        return resolved

    # ------------------------------------------------------------------ concurrency

    def _get_semaphore(self, user_id: str) -> asyncio.Semaphore:
        """Return (creating on first use) *user_id*'s download concurrency semaphore.

        MEDIUM-6 security fix: an unbounded fan-out of concurrent
        ``client.download_media`` calls for one user (e.g. a channel
        dumping many matching files at once) had no backpressure at all.
        Lazily created per user rather than pre-sized for every known user,
        since the user set isn't known upfront and most users never
        download concurrently in practice.
        """
        semaphore = self._download_semaphores.get(user_id)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._max_concurrent_downloads_per_user)
            self._download_semaphores[user_id] = semaphore
        return semaphore

    # ------------------------------------------------------------------ progress / task_history

    def _allocate_sn(self, chat_id: int, message_id: int) -> int | None:
        if self._task_id_map_repo is None:
            return None
        with contextlib.suppress(Exception):
            return self._task_id_map_repo.allocate(_TASK_HISTORY_SOURCE, f'{chat_id}:{message_id}')
        return None

    def _start_progress(self, sn: int | None, filename: str, *, user_id: str, chat_title: str) -> None:
        if self._progress_bus is None or sn is None:
            return
        with contextlib.suppress(Exception):
            self._progress_bus.start(
                sn,
                filename,
                status='下載中',
                bangumi_name=chat_title,
                owner_id=user_id,
                source=_TASK_HISTORY_SOURCE,
                external_id=str(sn),
            )

    def _finish_progress(self, sn: int | None, *, status: str, filename: str | None = None) -> None:
        if self._progress_bus is None or sn is None:
            return
        with contextlib.suppress(Exception):
            if filename is not None:
                self._progress_bus.update_metadata(sn, filename=filename)
            self._progress_bus.update_status(sn, status)
            self._progress_bus.finish(sn)

    def _start_history(
        self,
        sn: int | None,
        filename: str,
        user_id: str,
        watched: TgWatchedChat,
        message: hydrogram.types.Message,
    ) -> int | None:
        if self._task_history_repo is None or sn is None:
            return None
        with contextlib.suppress(Exception):
            return self._task_history_repo.record_start(
                sn,
                filename,
                owner_id=user_id,
                bangumi_name=watched.chat_title,
                source=_TASK_HISTORY_SOURCE,
                external_id=f'{message.chat.id}:{message.id}',
            )
        return None

    def _finish_history(self, history_row_id: int | None, *, final_status: str, filename: str | None = None) -> None:
        if self._task_history_repo is None or history_row_id is None:
            return
        with contextlib.suppress(Exception):
            self._task_history_repo.record_finish(
                history_row_id,
                final_status=final_status,
                finished_at=datetime.datetime.now(datetime.UTC),
                filename=filename,
            )

    # ------------------------------------------------------------------ progress callback

    def _make_progress_callback(
        self,
        sn: int | None,
        watched: TgWatchedChat,
        media: _MatchedMedia,
        message: hydrogram.types.Message,
    ) -> collections.abc.Callable[[int, int], None]:
        """Throttled progress callback passed to ``Client.download_media``.

        Mirrors BT LandingWorker's ``_make_landing_progress_callback``: at
        most once every :data:`_PROGRESS_MIN_INTERVAL_SECONDS` seconds, or
        sooner on a >=:data:`_PROGRESS_MIN_PERCENT_JUMP`-point percent jump
        (tighter than BT's 10 — see that constant's comment for why). The
        very first callback always fires (``last_edit_at`` starts ``None``)
        so the user sees the 0% state transition immediately.

        Speed/ETA are computed from the delta against the *previous callback
        invocation* (not the previous throttled emit) so an emitted sample
        always reflects the freshest chunk, even though it's only published
        on the throttled cadence — same rationale as BT's version.

        The whole body runs under ``contextlib.suppress(Exception)`` —
        hydrogram calls this synchronously from inside its own download
        loop, so a bug here (or a ProgressBus/notify failure) must never
        propagate and abort an otherwise-healthy download.
        """
        state: dict[str, float | int | None] = {
            'last_edit_at': None,
            'last_percent': None,
            'prev_time': None,
            'prev_bytes': 0,
        }

        def _on_progress(current: int, total: int) -> None:
            with contextlib.suppress(Exception):
                now = time.monotonic()
                percent = int(current / total * 100) if total else 0

                prev_time = state['prev_time']
                prev_bytes = T.cast('int', state['prev_bytes'])
                speed_mbps: float | None = None
                eta_seconds: int | None = None
                if prev_time is not None:
                    dt = now - T.cast('float', prev_time)
                    if dt > 0:
                        bytes_per_sec = (current - prev_bytes) / dt
                        if bytes_per_sec > 0:
                            speed_mbps = bytes_per_sec / 1_000_000
                            remaining = max(total - current, 0)
                            eta_seconds = int(remaining / bytes_per_sec)
                state['prev_time'] = now
                state['prev_bytes'] = current

                last_edit_at = state['last_edit_at']
                last_percent = state['last_percent']
                should_emit = (
                    last_edit_at is None
                    or (now - T.cast('float', last_edit_at)) >= _PROGRESS_MIN_INTERVAL_SECONDS
                    or last_percent is None
                    or (percent - T.cast('int', last_percent)) >= _PROGRESS_MIN_PERCENT_JUMP
                )
                if not should_emit:
                    return
                state['last_edit_at'] = now
                state['last_percent'] = percent

                self._emit('tg_progress', watched, media, message, bytes_written=current, total_bytes=total)
                if self._progress_bus is not None and sn is not None:
                    fraction = current / total if total else 0.0
                    self._progress_bus.update_stats(
                        sn, rate=fraction, speed_mbps=speed_mbps, eta_seconds=eta_seconds
                    )

        return _on_progress

    # ------------------------------------------------------------------ notifications

    def _emit(
        self,
        event: str,
        watched: TgWatchedChat,
        media: _MatchedMedia,
        message: hydrogram.types.Message,
        *,
        local_path: str | None = None,
        error_message: str | None = None,
        bytes_written: int | None = None,
        total_bytes: int | None = None,
    ) -> None:
        if self._notify_event_send is None:
            return
        with contextlib.suppress(Exception):
            payload: dict[str, object] = {
                'event': event,
                'chat_title': watched.chat_title,
                'chat_id': message.chat.id,
                'message_id': message.id,
                'file_name': media.file_name,
                'file_size': media.file_size,
            }
            if local_path is not None:
                payload['local_path'] = local_path
            if error_message is not None:
                payload['error_message'] = error_message[:200]
            if bytes_written is not None:
                payload['bytes_written'] = bytes_written
            if total_bytes is not None:
                payload['total_bytes'] = total_bytes
            self._notify_event_send(kwargs=payload)

    # ------------------------------------------------------------------ logging

    def _log_info(self, message: str) -> None:
        if self._logger is not None:
            self._logger.info(None, _LOG_TAG, message, display=False)

    def _log_error(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)

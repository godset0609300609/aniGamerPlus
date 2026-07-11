"""Historical backfill for a newly-watched Telegram chat.

``app.tg_downloader.downloader.TgDownloadWatcher`` only reacts to *new*
messages via ``hydrogram.Client.add_handler(MessageHandler(...))`` — a chat
just added to a user's watch list never gets its pre-existing media
downloaded that way. This service closes that gap: dispatched on-demand
(see ``app.tasks.tg_backfill_tick.tg_backfill_actor``), it walks a chat's
history newest-to-oldest via ``Client.get_chat_history``, stopping once it
reaches a message older than the requested cutoff, and runs every message
through the exact same filter/dedup/download pipeline as the real-time
handler (``TgDownloadWatcher.enqueue_message`` — see that method's
docstring for why the logic lives there and not here).

``tg_downloaded_media``'s ``UNIQUE(user_id, chat_id, message_id)``
constraint is what makes this safe to re-run and safe to race against the
real-time handler: if the live handler downloads a message while a backfill
scan is still walking past it (or vice versa), whichever of the two calls
``insert_if_new`` second is a silent no-op (``enqueue_message``'s
pre-download ``exists()`` check narrows the window further, but the
constraint itself is the actual safety net) — never a double-download.
"""

from __future__ import annotations

import contextlib
import datetime
import typing as T

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..persistence.tg_watched_chat_repo import TgWatchedChatRepository
    from .client_pool import TgClientPool
    from .downloader import TgDownloadWatcher

_LOG_TAG = 'TG回填'

#: How often (in scanned messages) to persist scan/match progress to
#: ``tg_watched_chat`` so the frontend's polling ``GET /api/tg/chats`` sees
#: live movement during a long-running scan.
_PROGRESS_UPDATE_EVERY = 50


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class TgBackfillService:
    """Runs one historical backfill scan for a single watched chat."""

    def __init__(
        self,
        client_pool: TgClientPool,
        watched_chat_repo: TgWatchedChatRepository,
        downloader: TgDownloadWatcher,
        *,
        logger: Logger | None = None,
    ) -> None:
        self._client_pool = client_pool
        self._watched_chat_repo = watched_chat_repo
        self._downloader = downloader
        self._logger = logger

    async def run(self, user_id: str, chat_id: int, days: int) -> None:
        """Backfill the last *days* days of *chat_id* for *user_id*.

        No-ops silently if the chat is no longer watched (deleted or
        disabled between dispatch and execution — the dramatiq actor can sit
        behind a busy queue for a while before it runs).
        """
        watched = self._watched_chat_repo.get(user_id, chat_id)
        if watched is None or not watched.enabled:
            return

        self._watched_chat_repo.mark_backfill_running(user_id, watched.id, started_at=_now_iso())

        client = await self._client_pool.get(user_id)
        if client is None:
            self._log_error(
                f'user_id={user_id} chat_id={chat_id} 回填失敗：Telegram session 無法連線（session 已撤銷或過期）'
            )
            self._watched_chat_repo.mark_backfill_failed(user_id, watched.id, finished_at=_now_iso())
            return

        # NOTE: hydrogram builds ``Message.date`` via
        # ``hydrogram.utils.timestamp_to_datetime``, i.e.
        # ``datetime.fromtimestamp(ts)`` with NO tz argument — a *naive*
        # datetime in the local system timezone, not UTC. The cutoff must be
        # computed the same way (naive, local time) or every comparison
        # below raises "can't compare offset-naive and offset-aware
        # datetimes". This is unrelated to backfill_started_at/finished_at
        # above, which stay ISO-8601 UTC per this table's usual convention.
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
        scanned = 0
        matched = 0

        try:
            # Warm up hydrogram's peer cache before the history walk below.
            # TgClientPool always builds clients with ``in_memory=True``
            # (see that module), so the peer cache starts empty on every
            # process restart — if this is the first time this client
            # instance has ever touched *chat_id*, ``get_chat_history``
            # would otherwise have no cached {id: access_hash} pair to build
            # a DC-scoped InputPeer from. A ``get_chat()`` roundtrip is
            # cheap and populates that cache; it also doubles as an
            # up-front "does this client still have access to this chat"
            # check before committing to a potentially-long history scan.
            await client.get_chat(chat_id)

            # contextlib.aclosing — breaking out of `async for` early (the
            # cutoff branch below) does NOT call the underlying async
            # generator's aclose(); left to normal GC, that trips a
            # "PytestUnraisableExceptionWarning" under the trio backend (and
            # is generally bad hygiene against a live MTProto connection).
            # This guarantees get_chat_history()'s generator is closed on
            # every exit path — break, exception, or normal completion.
            async with contextlib.aclosing(client.get_chat_history(chat_id)) as history:
                async for message in history:
                    message_date = getattr(message, 'date', None)
                    if message_date is not None and message_date < cutoff:
                        # Newest-first ordering — once we're past the cutoff
                        # every subsequent message is even older, so stop.
                        break

                    scanned += 1
                    if await self._downloader.enqueue_message(user_id, client, message, watched):
                        matched += 1

                    if scanned % _PROGRESS_UPDATE_EVERY == 0:
                        self._watched_chat_repo.mark_backfill_progress(
                            user_id, watched.id, scanned_count=scanned, matched_count=matched
                        )
        except Exception as exc:  # noqa: BLE001 — surfaced via backfill_status; dramatiq logs the traceback
            self._log_error(f'user_id={user_id} chat_id={chat_id} 回填失敗: {exc}')
            self._watched_chat_repo.mark_backfill_progress(
                user_id, watched.id, scanned_count=scanned, matched_count=matched
            )
            self._watched_chat_repo.mark_backfill_failed(user_id, watched.id, finished_at=_now_iso())
            raise

        self._watched_chat_repo.mark_backfill_progress(
            user_id, watched.id, scanned_count=scanned, matched_count=matched
        )
        self._watched_chat_repo.mark_backfill_done(user_id, watched.id, finished_at=_now_iso())

    def _log_error(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)

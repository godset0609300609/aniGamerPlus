"""Periodic cursor-based catch-up scan for watched Telegram chats.

``app.tg_downloader.downloader.TgDownloadWatcher`` only reacts to *live*
messages via ``hydrogram.Client.add_handler(MessageHandler(...))`` — if the
process restarts, a user's client disconnects, or the handler simply hasn't
been (re)registered yet, every message that arrives in that gap is lost
forever with no automatic recovery. ``TgBackfillService`` (``backfill.py``)
closes an *analogous* gap for a chat's pre-existing history, but only on
demand (adding a watched chat, or a manual retry) — nothing revisits an
*already*-watched chat periodically to catch messages missed while nothing
was listening.

This service is dispatched every ``ANIGAMERPLUS_TG_POLL_SECONDS`` seconds
(see ``app.tasks.tg_poll_tick``) and, for every enabled watched chat, walks
``Client.get_chat_history`` newest-first exactly like ``TgBackfillService``
does — down to the same peer-cache-warmup call, ``contextlib.aclosing``
generator hygiene, and shared ``TgDownloadWatcher.enqueue_message`` pipeline
(see that method's docstring for why the filter/dedup/download logic lives
there and not here) — but stops at a *persisted per-chat message-id cursor*
(``tg_watched_chat.last_scanned_message_id``) instead of a fixed day window.
This is a deliberate design choice over a fixed-lookback rescan: a
lookback-window rescan either has to be wide enough to always cover the
worst-case downtime (wasteful — re-walking the same messages every tick
forever) or risks missing a gap wider than the window. A durable cursor
means each tick only ever looks at messages genuinely new since the last
*successful* scan of that chat, however long that gap actually was.

``tg_downloaded_media``'s ``UNIQUE(user_id, chat_id, message_id)`` constraint
is what makes re-walking safe to overlap with the real-time handler (or a
concurrent backfill scan) — see ``TgBackfillService``'s module docstring,
the same reasoning applies unchanged here.

Resuming a capped sweep — why a single scalar cursor is not enough
--------------------------------------------------------------------
``_MAX_MESSAGES_PER_SCAN`` bounds how many messages one run processes, so a
chat with a huge backlog (e.g. after extended downtime) can't monopolise a
tick. An earlier version of this module tried to express "capped run, more
to do" by simply *not* advancing ``last_scanned_message_id`` when the cap
fired. That livelocks: the next run walks from the newest message again,
re-hits the exact same cap boundary, and the backlog below it is never
reached — proven by a 2500-message / cap-1000 repro that flatlined at 1000
enqueued forever.

The real problem is representational: one scalar high-water mark cannot
express "a contiguous range at the top of the chat is fully handled, but
there is still an unprocessed gap below it." Two more columns fix that:

* ``scan_resume_offset_id`` — the lowest ``message.id`` the *most recent*
  capped run of an in-progress sweep actually processed. The next run
  passes this straight to ``Client.get_chat_history(chat_id,
  offset_id=...)``, which (per hydrogram's own pagination — see
  ``get_chat_history``'s internal ``offset_id = messages[-1].id`` chunk
  loop, and Telegram's ``messages.getHistory`` semantics) yields only
  messages with ``id < offset_id`` — i.e. exactly the unprocessed
  continuation, no re-walk of the already-handled top range, no gap.
* ``scan_pending_cursor`` — the ``newest_seen`` id captured by the *first*
  run of the sweep, carried unchanged across every subsequent capped run,
  and only ever committed into ``last_scanned_message_id`` once the sweep
  finally reaches its true stop condition (old cursor, or the first-scan
  time cutoff) without hitting the cap again.

Both are ``NULL`` whenever no sweep is in progress for a chat, and
``last_scanned_message_id`` itself is left completely untouched for the
entire duration of an in-progress sweep — see the "Cursor selection"
comment in :meth:`TgCatchupService.run_one` for why touching it mid-sweep
(even to a supposedly-safe value) is exactly the bug above in disguise.

A consequence worth calling out explicitly: messages that arrive in the
chat *after* a sweep's ``scan_pending_cursor`` was captured are not picked
up by that sweep (resumed runs only ever walk older, via ``offset_id``) —
they are picked up by the *next* regular (non-resuming) tick instead, which
starts a fresh walk from the chat's actual current top. Nothing is lost,
it just lands one tick later than messages that arrived before the sweep
started.
"""

from __future__ import annotations

import contextlib
import datetime
import typing as T

from ..security.log_scrub import scrub_exception_for_log

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..persistence.tg_watched_chat_repo import TgWatchedChatRepository
    from .client_pool import TgClientPool
    from .downloader import TgDownloadWatcher

_LOG_TAG = 'TG補抓'

#: Hard cap on how many messages a single chat's catch-up scan will walk
#: through (i.e. run through ``enqueue_message``) in one run. Without this,
#: one chat that accumulated an enormous backlog while nothing was watching
#: it (e.g. a very active channel during an extended outage) could tie up
#: the whole periodic tick — and every *other* watched chat waits behind it
#: — for however long that walk takes. 1000 gives a lot of headroom for a
#: normal outage-sized backlog (the real-time handler is expected to catch
#: everything under normal operation; this tick only ever has work to do
#: after a genuine gap) while still bounding one tick's worst case.
#:
#: A run that hits this cap does NOT lose its place: it persists
#: ``scan_resume_offset_id``/``scan_pending_cursor`` (see the module
#: docstring's "Resuming a capped sweep" section) so the next run continues
#: the downward walk exactly where this one stopped, via
#: ``Client.get_chat_history``'s ``offset_id`` parameter, instead of
#: re-starting from the top. A chat whose genuine backlog exceeds this cap
#: simply takes multiple ticks to fully catch up — bounded, convergent
#: progress, not a re-scan of the same top slice forever.
_MAX_MESSAGES_PER_SCAN = 1000


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class TgCatchupService:
    """Runs the periodic catch-up scan across every enabled watched chat."""

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

    async def run_all(self, catchup_hours: int) -> None:
        """Catch-up-scan every enabled watched chat, across every user.

        *catchup_hours* only matters for a chat that has never been scanned
        before (see :meth:`run_one`). Each chat's scan is isolated: a chat
        whose scan raises (network error, revoked session, malformed
        history, ...) is logged and skipped — it must never take down the
        sweep for every other watched chat in the same tick.
        """
        for user_id, watched in self._watched_chat_repo.list_all_enabled():
            try:
                await self.run_one(user_id, watched.chat_id, catchup_hours)
            except Exception as exc:  # noqa: BLE001 — defensive backstop only.
                # run_one() already catches every failure from its own scan
                # (session/connect errors, get_chat_history blowing up,
                # enqueue_message raising, ...) and returns without
                # re-raising — see its try/except below. This branch is a
                # last-resort net for anything that could still slip past
                # that (a bug in run_one's own bookkeeping, or the
                # list_all_enabled()/get() repo calls themselves failing)
                # so that, no matter what breaks, one bad chat never stops
                # the rest of the sweep.
                self._log_error(
                    f'user_id={user_id} chat_id={watched.chat_id} 補抓迴圈發生未預期錯誤: '
                    f'{scrub_exception_for_log(exc)}'
                )
                continue

    async def run_one(self, user_id: str, chat_id: int, catchup_hours: int) -> None:
        """Catch-up-scan a single watched chat for *user_id*.

        No-ops silently if the chat is no longer watched (deleted or
        disabled since the last tick) or if *user_id* has no connectable
        Telegram session. On any scan failure, logs and returns WITHOUT
        raising and WITHOUT touching any persisted cursor state — the next
        tick simply retries the exact same range (safe/idempotent thanks to
        ``enqueue_message``'s dedup, same as a re-run backfill).
        """
        watched = self._watched_chat_repo.get(user_id, chat_id)
        if watched is None or not watched.enabled:
            return

        scan_state = self._watched_chat_repo.get_scan_cursor_state(user_id, watched.id)
        if scan_state is None:
            # Extremely unlikely race: the chat was deleted between the two
            # reads above. Same "gone" treatment as the watched is None
            # check.
            return

        client = await self._client_pool.get(user_id)
        if client is None:
            self._log_error(
                f'user_id={user_id} chat_id={chat_id} 補抓失敗：Telegram session 無法連線（session 已撤銷或過期）'
            )
            return

        cursor = scan_state.last_scanned_message_id
        resume_offset = scan_state.scan_resume_offset_id
        pending_cursor = scan_state.scan_pending_cursor
        # Both resume-state columns are always written/cleared together
        # (see update_scan_cursor_state) — but if that invariant were ever
        # violated (a bug, or manual DB surgery), only trusting "resuming"
        # when BOTH are present self-heals: the run below is treated as a
        # fresh, non-resuming sweep, which recomputes newest_seen normally
        # and rewrites both columns consistently on its next commit.
        resuming = resume_offset is not None and pending_cursor is not None

        # NOTE: hydrogram builds ``Message.date`` via
        # ``hydrogram.utils.timestamp_to_datetime``, i.e.
        # ``datetime.fromtimestamp(ts)`` with NO tz argument — a *naive*
        # datetime in the local system timezone, not UTC (see
        # ``TgBackfillService.run``'s identical note). The cutoff below must
        # be computed the same way (naive, local time) or every comparison
        # against it raises "can't compare offset-naive and offset-aware
        # datetimes". This only matters on a chat's very first scan (no
        # cursor yet) — every scan after that compares plain integer message
        # ids instead, which sidesteps the whole naive/aware trap entirely.
        cutoff = None if cursor is not None else datetime.datetime.now() - datetime.timedelta(hours=catchup_hours)

        scanned = 0
        # The id of the very first message a FRESH (non-resuming) walk
        # yields, captured unconditionally before any stop-condition check
        # below — see the cursor-selection comment after the loop for why
        # this must be recorded even on a run that ends up processing zero
        # messages. Deliberately NOT captured while resuming: a resumed
        # walk's first message is just a mid-backlog continuation point, not
        # the chat's current top, and the sweep's real high-water mark
        # (scan_pending_cursor) was already captured by the run that started
        # it.
        newest_seen: int | None = None
        lowest_processed: int | None = None
        cap_hit = False

        try:
            # Warm up hydrogram's peer cache before the history walk below —
            # see TgBackfillService.run's identical call for the full
            # reasoning (TgClientPool always builds in_memory=True clients,
            # so a process restart means an empty peer cache).
            await client.get_chat(chat_id)

            # contextlib.aclosing — see TgBackfillService.run's identical
            # comment: breaking out of `async for` early does not otherwise
            # close the underlying async generator, which is bad hygiene
            # against a live MTProto connection and trips pytest's
            # unraisable-exception warning under the trio backend.
            #
            # offset_id: when resuming, this is the lowest id the previous
            # capped run processed. Telegram's messages.getHistory (which
            # get_chat_history wraps) treats offset_id as EXCLUSIVE — it
            # returns only messages with id < offset_id, never re-yielding
            # offset_id itself. hydrogram's own internal pagination in
            # get_chat_history relies on exactly this (it re-invokes with
            # offset_id = <last message of the previous chunk>.id and never
            # sees that message twice), which is the source-level proof this
            # is exclusive, not inclusive — resuming here can neither skip
            # nor double-process the boundary message.
            history_gen = client.get_chat_history(chat_id, offset_id=resume_offset or 0)
            if history_gen is None:
                raise RuntimeError(f'get_chat_history 回傳 None（chat_id={chat_id}）')
            async with contextlib.aclosing(history_gen) as history:
                async for message in history:
                    if not resuming and newest_seen is None:
                        newest_seen = message.id

                    if cursor is not None:
                        if message.id <= cursor:
                            # Newest-first ordering — once we're back down
                            # to (or past) the last scan's high-water mark,
                            # every subsequent message was already handled
                            # by a previous run.
                            break
                    else:
                        message_date = getattr(message, 'date', None)
                        if message_date is not None and message_date < cutoff:
                            break

                    if scanned >= _MAX_MESSAGES_PER_SCAN:
                        cap_hit = True
                        break

                    await self._downloader.enqueue_message(user_id, client, message, watched)
                    scanned += 1
                    lowest_processed = message.id
        except Exception as exc:  # noqa: BLE001 — logged below; must not propagate, see run_one's docstring.
            self._log_error(f'user_id={user_id} chat_id={chat_id} 補抓失敗: {scrub_exception_for_log(exc)}')
            return

        # The sweep's real high-water mark: either the newest_seen this run
        # just captured (a fresh, non-resuming sweep) or the one an earlier
        # run of an in-progress sweep already captured (resuming) — carried
        # through untouched.
        effective_pending = pending_cursor if resuming else newest_seen

        if cap_hit:
            # Persist resume state so the NEXT run continues from exactly
            # where this one stopped, instead of re-walking from the top —
            # see the module docstring's "Resuming a capped sweep" section
            # for the full reasoning and the livelock this replaces.
            #
            # Cursor selection: last_scanned_message_id is passed through
            # COMPLETELY UNCHANGED here — including staying None if this is
            # the first-ever capped run of a brand-new sweep. Coalescing it
            # to some concrete placeholder (0, or anything else) the moment
            # a sweep is merely in progress would be the same class of bug
            # the resume mechanism exists to fix: it would flip `cursor is
            # not None` to True for every subsequent resumed run of THIS
            # SAME sweep, silently switching that run's stop condition from
            # the intended time-cutoff comparison to an id comparison
            # against a fabricated threshold — discarding the
            # catchup_hours bound the sweep was supposed to honour for as
            # long as it stays in progress. last_scanned_message_id only
            # ever moves in the "sweep completes" branch below.
            new_resume_offset = lowest_processed if lowest_processed is not None else (resume_offset or 0)
            self._log_info(
                f'user_id={user_id} chat_id={chat_id} 補抓觸及單次掃描上限'
                f'（{_MAX_MESSAGES_PER_SCAN}），已記錄續傳位置 message_id={new_resume_offset}，'
                f'下輪將由此繼續（不重新掃描本輪已處理範圍）'
            )
            self._watched_chat_repo.update_scan_cursor_state(
                user_id,
                watched.id,
                last_scanned_message_id=cursor,
                scan_resume_offset_id=new_resume_offset,
                scan_pending_cursor=effective_pending,
                scanned_at=_now_iso(),
            )
            return

        # Reached a natural stop condition (old cursor, or the first-scan
        # cutoff) without hitting the cap this run — the sweep (if one was
        # in progress) is now fully done. Commit the real high-water mark
        # and clear both resume columns.
        #
        # * effective_pending is not None: advance to it (with a regression
        #   guard — a deleted top message could otherwise make a fresh
        #   sweep's newest_seen come back lower than what a previous,
        #   already-committed sweep established).
        # * effective_pending is None: nothing was ever seen across the
        #   whole sweep (chat has no history, or nothing newer than the
        #   previous cursor) — keep the existing cursor, or fall back to the
        #   0 sentinel on this chat's first-ever scan (an empty chat still
        #   needs *some* non-None cursor so the next run uses id-cursor mode
        #   instead of re-running the catchup_hours cutoff forever — real
        #   message ids are always >= 1, so 0 correctly means "everything is
        #   new").
        if effective_pending is not None:
            new_last_scanned = effective_pending
            if cursor is not None:
                new_last_scanned = max(new_last_scanned, cursor)
        else:
            new_last_scanned = cursor if cursor is not None else 0

        self._watched_chat_repo.update_scan_cursor_state(
            user_id,
            watched.id,
            last_scanned_message_id=new_last_scanned,
            scan_resume_offset_id=None,
            scan_pending_cursor=None,
            scanned_at=_now_iso(),
        )

    def _log_info(self, message: str) -> None:
        if self._logger is not None:
            self._logger.info(None, _LOG_TAG, message, display=False)

    def _log_error(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)

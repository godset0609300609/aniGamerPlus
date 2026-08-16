"""On-demand forced re-download of a single already-recorded Telegram media entry.

Backs the per-item "強制重新下載" action in the Downloads tab (see
``app.api.tg_api``'s ``POST /api/tg/downloads/{id}/redownload`` and
``app.tasks.tg_redownload_tick.tg_redownload_actor``, the dramatiq actor
that calls :meth:`TgRedownloadService.run`). Sibling to
``app.tg_downloader.backfill.TgBackfillService`` /
``app.tg_downloader.catchup.TgCatchupService`` in shape — resolve a live
client, touch the Telegram chat/message, hand off to
``TgDownloadWatcher`` — but scoped to exactly one already-downloaded
message instead of walking a chat's history.

Every failure mode this method can hit (missing/revoked session, the chat
no longer being reachable, the original message having been deleted) is
*expected*, user-triggerable behaviour, not a bug: each is logged with a
specific, actionable Traditional-Chinese message and the method returns
quietly rather than raising. There is no dramatiq retry to feed
(``max_retries=0`` on the actor) and, since the HTTP request that queued
this already returned before any of this runs, the log line + the existing
task_history/ProgressBus/telegram-notify "download failed" surfacing (done
by ``TgDownloadWatcher.force_redownload`` itself once a message is in hand)
*is* the user-facing error — the same channel a live-push download failure
already uses today.
"""

from __future__ import annotations

import typing as T

import hydrogram.errors

from ..security.log_scrub import scrub_exception_for_log

if T.TYPE_CHECKING:
    import hydrogram.types

    from ..logging_ import Logger
    from ..persistence.tg_downloaded_media_repo import TgDownloadedMediaRepository
    from .client_pool import TgClientPool
    from .downloader import TgDownloadWatcher

_LOG_TAG = 'TG強制重新下載'


class TgRedownloadService:
    """Runs one force-redownload for a single ``tg_downloaded_media`` row."""

    def __init__(
        self,
        client_pool: TgClientPool,
        downloaded_media_repo: TgDownloadedMediaRepository,
        downloader: TgDownloadWatcher,
        *,
        logger: Logger | None = None,
    ) -> None:
        self._client_pool = client_pool
        self._downloaded_media_repo = downloaded_media_repo
        self._downloader = downloader
        self._logger = logger

    async def run(self, user_id: str, entry_id: int) -> None:
        """Force re-download *entry_id* — must already belong to *user_id*.

        No-ops (logged, not raised) when: the row no longer exists or
        never belonged to *user_id* (ownership itself is already enforced
        at the synchronous API layer via
        ``TgDownloadedMediaRepository.get_by_id_for_user`` — reaching this
        branch here means a race with a concurrent delete, not an auth
        bypass); the user has no connectable Telegram session; the chat is
        no longer reachable; or the original message has been deleted from
        Telegram. The actual download/replace/DB-update — including its
        own failure bookkeeping if the download itself fails after a
        message is successfully fetched — is fully delegated to
        :meth:`TgDownloadWatcher.force_redownload`.
        """
        entry = self._downloaded_media_repo.get_by_id_for_user(user_id, entry_id)
        if entry is None:
            self._log_error(f'user_id={user_id} entry_id={entry_id} 強制重新下載失敗：找不到下載紀錄')
            return

        client = await self._client_pool.get(user_id)
        if client is None:
            self._log_error(
                f'user_id={user_id} entry_id={entry_id} 強制重新下載失敗：'
                f'Telegram session 無法連線（session 已撤銷或過期）'
            )
            return

        try:
            # Warm the peer cache / confirm the chat is still reachable
            # before fetching the message — mirrors TgBackfillService.run's
            # and TgCatchupService.run_one's identical get_chat() warmup
            # (TgClientPool always builds in_memory=True clients, so the
            # peer cache starts empty on every process restart). A chat the
            # account can no longer access (kicked, channel deleted, or a
            # ChannelForbidden-shaped channel — see hydrogram_compat.py's
            # docstring) raises here.
            await client.get_chat(entry.chat_id)
            # get_messages is typed to return Message | list[Message]
            # because message_ids also accepts an iterable of ids — passing
            # a single int (as here) always returns a single Message (or
            # None) at runtime; the cast just tells mypy what the hydrogram
            # stub can't express from the argument type alone.
            message = T.cast(
                'hydrogram.types.Message | None',
                await client.get_messages(entry.chat_id, entry.message_id),
            )
        except (
            hydrogram.errors.ChannelPrivate,
            hydrogram.errors.ChannelInvalid,
            hydrogram.errors.PeerIdInvalid,
        ) as exc:
            self._log_error(
                f'user_id={user_id} entry_id={entry_id} 強制重新下載失敗：'
                f'聊天已無法存取（可能已被移除，或帳號已失去存取權）: {scrub_exception_for_log(exc)}'
            )
            return
        except hydrogram.errors.RPCError as exc:
            self._log_error(
                f'user_id={user_id} entry_id={entry_id} 強制重新下載失敗：'
                f'讀取原始訊息時發生 Telegram 錯誤: {scrub_exception_for_log(exc)}'
            )
            return
        except Exception as exc:  # noqa: BLE001 — any other read-message failure, logged and dropped (see docstring)
            self._log_error(
                f'user_id={user_id} entry_id={entry_id} 強制重新下載失敗：'
                f'讀取原始訊息時發生未預期錯誤: {scrub_exception_for_log(exc)}'
            )
            return

        # A deleted message comes back as a near-empty Message(empty=True)
        # rather than raising or returning None outright (Telegram
        # represents it as raw.types.MessageEmpty — see hydrogram's
        # utils.parse_messages) — both shapes mean "nothing left to
        # download".
        if message is None or getattr(message, 'empty', False):
            self._log_error(f'user_id={user_id} entry_id={entry_id} 強制重新下載失敗：原始訊息已在 Telegram 中被刪除')
            return

        await self._downloader.force_redownload(user_id, client, message, entry)

    def _log_error(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)

"""Single-sn download worker.

Port of legacy ``aniGamerPlus.worker`` — runs one sn through the full
pipeline: build Anime → download → update DB → (optional) upload →
update DB → notify → progress.finish → queue.unmark_processing.
"""

from __future__ import annotations

import collections.abc
import typing as T

from ..downloader import exceptions

if T.TYPE_CHECKING:
    from ..downloader.anime import Anime
    from ..downloader.progress import ProgressBus
    from ..logging_ import Logger
    from ..models import AppSettings
    from ..persistence.repositories import AnimeRepository
    from .queue_ import TaskQueue


class DownloadWorker:
    """Runs one sn through the full pipeline and releases the slot when done."""

    def __init__(
        self,
        *,
        queue: TaskQueue,
        anime_factory: collections.abc.Callable[[int], Anime],
        anime_repo: AnimeRepository,
        progress: ProgressBus,
        settings_provider: collections.abc.Callable[[], AppSettings],
        logger: Logger,
    ) -> None:
        self._queue = queue
        self._anime_factory = anime_factory
        self._anime_repo = anime_repo
        self._progress = progress
        self._settings_provider = settings_provider
        self._logger = logger

    # ------------------------------------------------------------------ entry

    def run(self, sn: int, *, realtime_show_file_size: bool = False) -> None:
        """Pull ``sn`` from the queue, execute the pipeline, release slot.

        ``TryTooManyTimeError`` and ``NoAvailableStreamError`` are caught
        per legacy behaviour; any other exception propagates.

        Progress lifecycle: the pipeline owns all ``progress.finish(sn)``
        calls. Terminal paths (success, ``NoAvailableStreamError``, or any
        unexpected exception bubbling out of the pipeline) MUST finish the
        entry so the monitor drops it within one polling tick. The single
        exception is ``TryTooManyTimeError``, which is recoverable — the
        entry is left visible with status ``'失敗! 重啓中'`` so users
        see the failure until the UpdateLoop's next pass.
        """
        sn = int(sn)
        info = self._queue.get(sn)
        if info is None:
            self._logger.info(
                sn,
                '任務啟動',
                'sn missing from queue; aborting worker',
                display=False,
            )
            self._queue.unmark_processing(sn)
            return

        settings = self._settings_provider()

        # Fast path: row exists, status==1, and we don't need to upload.
        existing = self._anime_repo.read(sn)
        if (
            existing is not None
            and existing.status == 1
            and (not settings.upload_to_server or existing.remote_status == 1)
        ):
            self._logger.info(
                sn,
                '任務跳過',
                '已下載且無待上傳任務',
                display=False,
            )
            self._progress.finish(sn)
            self._queue.pop(sn)
            self._queue.unmark_processing(sn)
            return

        # Belt-and-suspenders: ensure ``finish(sn)`` runs in every terminal
        # path, including an unexpected exception escaping ``_run_pipeline``.
        # ``TryTooManyTimeError`` is the lone recoverable branch and unsets
        # this flag so the entry survives for the user to see.
        finish_on_exit = True
        with self._queue.download_slot():
            try:
                finish_on_exit = self._run_pipeline(
                    sn,
                    info=info,
                    settings=settings,
                    realtime_show_file_size=realtime_show_file_size,
                )
            finally:
                if finish_on_exit:
                    self._progress.finish(sn)

    # ------------------------------------------------------------------ pipeline

    def _run_pipeline(
        self,
        sn: int,
        *,
        info: object,  # TaskInfo — kept loose to avoid runtime import cycle
        settings: AppSettings,
        realtime_show_file_size: bool,
    ) -> bool:
        """Run the per-sn pipeline.

        Returns ``True`` iff the caller should invoke ``progress.finish(sn)``
        in its ``finally`` block. The only path returning ``False`` is the
        recoverable ``TryTooManyTimeError`` branch, where the entry must
        stay visible showing ``'失敗! 重啓中'``.
        """
        from .queue_ import TaskInfo  # local to keep typing happy

        assert isinstance(info, TaskInfo)

        try:
            anime = self._anime_factory(sn)
            anime.load()
        except exceptions.NoAvailableStreamError as exc:
            self._logger.error(
                sn,
                '任務失敗',
                f'無可用影片源: {exc}',
                display=False,
            )
            self._progress.update_status(sn, '失敗')
            self._queue.pop(sn)
            self._queue.unmark_processing(sn)
            return True
        except exceptions.TryTooManyTimeError as exc:
            self._logger.error(
                sn,
                '任務失敗',
                f'抓取資訊失敗: {exc}',
                display=False,
            )
            self._progress.mark_retry(sn)
            self._queue.unmark_processing(sn)
            return False

        # Download
        try:
            result = anime.download(
                resolution=str(settings.download_resolution),
                bangumi_tag=info.tag,
                season=info.season,
                custom_name=info.custom_name,
                realtime_show_file_size=realtime_show_file_size,
                classify=settings.classify_bangumi,
                include_resolution_in_filename=False,  # auto-mode excludes resolution
            )
        except exceptions.TaskCancelledError:
            # Not a retriable error — the user explicitly cancelled.
            # ProgressBus.cancel() has already set status='已取消' and scheduled
            # finish(); we just need to clean up the queue slot.
            self._logger.info(
                sn,
                '任務取消',
                '使用者取消了下載任務',
                display=False,
            )
            self._queue.pop(sn)
            self._queue.unmark_processing(sn)
            return False  # do NOT call finish() — cancel() already scheduled it
        except exceptions.NoAvailableStreamError as exc:
            self._logger.error(
                sn,
                '任務失敗',
                f'無可用影片源: {exc}',
                display=False,
            )
            # Anime.download() already set status to '失敗'; ensure it's set
            # here too in case load() raised before download() was entered.
            self._progress.update_status(sn, '失敗')
            self._queue.pop(sn)
            self._queue.unmark_processing(sn)
            return True
        except exceptions.TryTooManyTimeError as exc:
            self._logger.error(
                sn,
                '任務失敗',
                f'下載重試已耗盡: {exc}',
                display=False,
            )
            self._progress.mark_retry(sn)
            self._queue.unmark_processing(sn)
            return False

        if not result.success or result.file_path is None or result.size_mb < 5:
            self._logger.error(
                sn,
                '任務失敗',
                '下載結果尺寸異常',
                display=False,
            )
            self._progress.mark_retry(sn)
            self._queue.unmark_processing(sn)
            return False

        # DB: insert or update.
        self._persist_download(sn, anime=anime, result=result)

        # Optional upload.
        if settings.upload_to_server:
            self._run_upload(sn, anime=anime, info=info)

        # Terminal success: caller's ``finally`` finishes the progress entry.
        self._queue.pop(sn)
        self._queue.unmark_processing(sn)
        return True

    # ------------------------------------------------------------------ helpers

    def _persist_download(
        self,
        sn: int,
        *,
        anime: Anime,
        result: object,
    ) -> None:
        from ..downloader.anime import DownloadResult

        assert isinstance(result, DownloadResult)
        assert result.file_path is not None

        meta_title = anime.get_title()
        bangumi_name = anime.get_bangumi_name()
        episode = anime.get_episode()

        existing = self._anime_repo.read(sn)
        local_path = str(result.file_path)
        resolution = int(anime.get_resolution())

        if existing is None:
            self._anime_repo.insert(
                sn=sn,
                title=meta_title,
                anime_name=bangumi_name,
                episode=episode,
                resolution=resolution,
                file_size=int(result.size_mb),
                local_file_path=local_path,
            )
            self._anime_repo.update(sn, status=1)
        else:
            self._anime_repo.update(
                sn,
                status=1,
                title=meta_title,
                anime_name=bangumi_name,
                episode=episode,
                resolution=resolution,
                file_size=int(result.size_mb),
                local_file_path=local_path,
            )

    def _run_upload(
        self,
        sn: int,
        *,
        anime: Anime,
        info: object,
    ) -> None:
        from .queue_ import TaskInfo

        assert isinstance(info, TaskInfo)

        self._queue.upload_limiter.acquire()
        self._progress.update_status(sn, '正在上傳')
        try:
            ok = anime.upload(bangumi_tag=info.tag)
        except exceptions.TryTooManyTimeError as exc:
            self._logger.error(
                sn,
                '上傳失敗',
                f'重試已耗盡: {exc}',
                display=False,
            )
            ok = False
        finally:
            self._queue.upload_limiter.release()

        if ok:
            self._anime_repo.update(sn, remote_status=1)
        else:
            self._logger.error(
                sn,
                '上傳失敗',
                'FTP 上傳失敗',
                display=False,
            )

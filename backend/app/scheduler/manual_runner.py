"""Manual-mode CLI entry point.

Replaces legacy ``__cui`` + ``__download_only`` + ``__get_info_only``
+ ``__get_danmu_only``. Call signature is kept compatible with the
``_aniGamerPlus__cui`` shim used by ``app/services/task_service.py`` so
Batch 6 can swap in :meth:`ManualRunner.run` without touching the
service layer.
"""

from __future__ import annotations

import collections.abc
import concurrent.futures
import pathlib
import typing as T

from ..downloader import exceptions

if T.TYPE_CHECKING:
    from ..downloader.anime import Anime
    from ..downloader.metadata import MetadataExtractor
    from ..downloader.progress import ProgressBus
    from ..logging_ import Logger
    from ..models import AppSettings
    from ..persistence.repositories import AnimeRepository
    from ..scheduler.cd_counter import DownloadCooldown

# Maximum number of concurrent pre-parse workers (lightweight HTTP only).
_PRE_PARSE_MAX_WORKERS = 5


class ManualRunner:
    """Entry point for manual CLI commands + ``/api/tasks/manual``."""

    def __init__(
        self,
        *,
        anime_factory: collections.abc.Callable[[int], Anime],
        anime_repo: AnimeRepository,
        settings: AppSettings,
        logger: Logger,
        progress_bus: ProgressBus | None = None,
        metadata_extractor: MetadataExtractor | None = None,
        parse_cooldown: DownloadCooldown | None = None,
    ) -> None:
        self._anime_factory = anime_factory
        self._anime_repo = anime_repo
        self._settings = settings
        self._logger = logger
        self._progress_bus = progress_bus
        self._metadata_extractor = metadata_extractor
        self._parse_cooldown = parse_cooldown

    # ------------------------------------------------------------------ entry

    def run(
        self,
        sn: int | None,
        *,
        resolution: str = '',
        mode: str = 'single',
        thread_limit: int = 1,
        ep_range: list[str] | None = None,
        save_dir: pathlib.Path | None = None,
        classify: bool = True,
        get_info: bool = False,
        user_cmd: bool = False,
        realtime_show: bool = True,
        cui_danmu: bool = False,
        owner_id: str | None = None,
    ) -> None:
        """Dispatch by mode. Matches legacy ``__cui`` shape.

        ``owner_id`` is the user id of the caller who triggered this task.
        It is forwarded to :meth:`ProgressBus.start` so the RBAC layer can
        filter in-flight tasks per user.
        """
        ep_range = list(ep_range or [])
        realtime_show_file_size = realtime_show and (thread_limit == 1 or mode in ('single', 'latest', 'largest-sn'))

        if mode == 'single':
            if sn is None:
                raise ValueError("mode='single' requires sn")
            self._download_one(
                int(sn),
                resolution=resolution,
                save_dir=save_dir,
                classify=classify,
                get_info=get_info,
                cui_danmu=cui_danmu,
                realtime_show_file_size=realtime_show_file_size,
                owner_id=owner_id,
            )
            return

        if mode in ('latest', 'largest-sn'):
            if sn is None:
                raise ValueError(f"mode='{mode}' requires sn")
            anime = self._anime_factory(int(sn))
            anime.load()
            episode_sns = list(anime.get_episode_list().values())
            if mode == 'largest-sn':
                episode_sns.sort()
            target_sn = episode_sns[-1] if episode_sns else int(sn)
            self._download_one(
                int(target_sn),
                resolution=resolution,
                save_dir=save_dir,
                classify=classify,
                get_info=get_info,
                cui_danmu=cui_danmu,
                realtime_show_file_size=realtime_show_file_size,
                owner_id=owner_id,
            )
            return

        if mode == 'all':
            if sn is None:
                raise ValueError("mode='all' requires sn")
            anime = self._anime_factory(int(sn))
            anime.load()
            episode_sns = sorted(anime.get_episode_list().values())
            self._download_many(
                episode_sns,
                thread_limit=thread_limit,
                resolution=resolution,
                save_dir=save_dir,
                classify=classify,
                get_info=get_info,
                cui_danmu=cui_danmu,
                realtime_show_file_size=realtime_show_file_size,
                owner_id=owner_id,
            )
            return

        if mode == 'range':
            if sn is None:
                raise ValueError("mode='range' requires sn")
            anime = self._anime_factory(int(sn))
            anime.load()
            episode_dict = anime.get_episode_list()
            wanted = self._expand_range(ep_range)
            range_sns: list[int] = []
            for ep_label, ep_sn in episode_dict.items():
                if ep_label in wanted:
                    range_sns.append(ep_sn)
            self._download_many(
                range_sns,
                thread_limit=thread_limit,
                resolution=resolution,
                save_dir=save_dir,
                classify=classify,
                get_info=get_info,
                cui_danmu=cui_danmu,
                realtime_show_file_size=realtime_show_file_size,
                owner_id=owner_id,
            )
            return

        if mode == 'multi':
            # Direct sn list — each item of ep_range is already a sn.
            target_sns = [int(item) for item in ep_range if str(item).isdigit()]
            if sn is not None and int(sn) not in target_sns:
                target_sns.append(int(sn))
            self._download_many(
                target_sns,
                thread_limit=thread_limit,
                resolution=resolution,
                save_dir=save_dir,
                classify=classify,
                get_info=get_info,
                cui_danmu=cui_danmu,
                realtime_show_file_size=realtime_show_file_size,
                owner_id=owner_id,
            )
            return

        if mode == 'list':
            # ``ep_range`` is the flattened sn list coming from sn_list.txt.
            target_sns = [int(item) for item in ep_range if str(item).isdigit()]
            self._download_many(
                target_sns,
                thread_limit=thread_limit,
                resolution=resolution,
                save_dir=save_dir,
                classify=classify,
                get_info=get_info,
                cui_danmu=cui_danmu,
                realtime_show_file_size=realtime_show_file_size,
                owner_id=owner_id,
            )
            return

        raise ValueError(f'unknown mode: {mode!r}')

    # ------------------------------------------------------------------ helpers

    def _announce_waiting(self, sn: int, *, owner_id: str | None = None) -> None:
        """Seed a ``'等待下載'`` entry on the progress bus if one is wired.

        Manual tasks typically start downloading immediately, so this is a
        short-lived hint; it exists to keep UX consistent with scheduled
        tasks and to surface the task for the span between dispatch and
        the first network round-trip inside ``Anime.load``.

        ``owner_id`` is forwarded to :meth:`ProgressBus.start` so the RBAC
        layer can filter the entry by caller identity.
        """
        if self._progress_bus is None:
            return
        existing = self._anime_repo.read(int(sn))
        preview_source = existing.anime_name if existing is not None else str(sn)
        self._progress_bus.start(
            int(sn),
            f'《{preview_source}》',
            status='等待下載',
            owner_id=owner_id,
        )

    def _download_one(
        self,
        sn: int,
        *,
        resolution: str,
        save_dir: pathlib.Path | None,
        classify: bool,
        get_info: bool,
        cui_danmu: bool,
        realtime_show_file_size: bool,
        owner_id: str | None = None,
    ) -> None:
        # Fix: check if the task was cancelled while waiting in the thread-pool
        # queue.  This can happen when _download_many submits N futures but the
        # user cancels one before a worker slot opens.  Without this guard,
        # _announce_waiting → progress.start would re-create a '等待下載' card
        # for the cancelled sn and it would reappear in the Monitor UI.
        if self._progress_bus is not None:
            cancel_event = self._progress_bus.get_cancel_event(sn)
            if cancel_event is not None and cancel_event.is_set():
                self._logger.info(sn, 'ManualTask', '任務已取消，跳過執行', display=False)
                self._progress_bus.finish(sn)
                return
        self._announce_waiting(int(sn), owner_id=owner_id)
        self._logger.info(sn, '任務開始', f'開始處理 sn={sn}', display=False)
        try:
            anime = self._anime_factory(int(sn))
            anime.load()
            self._logger.info(sn, '解析完成', '已取得影片資訊', display=False)
        except exceptions.NoAvailableStreamError as exc:
            self._logger.error(sn, '任務失敗', f'無可用影片源: {exc}', display=False)
            if self._progress_bus is not None:
                self._progress_bus.update_status(sn, '失敗')
                self._progress_bus.finish(sn)
            return
        except exceptions.TryTooManyTimeError as exc:
            self._logger.error(sn, '任務失敗', f'抓取失敗: {exc}', display=False)
            if self._progress_bus is not None:
                self._progress_bus.update_status(sn, '失敗')
                self._progress_bus.finish(sn)
            return
        except Exception as exc:  # noqa: BLE001
            self._logger.error(sn, '任務失敗', f'初始化失敗: {exc}', display=False)
            if self._progress_bus is not None:
                self._progress_bus.update_status(sn, '失敗')
                self._progress_bus.finish(sn)
            return

        if cui_danmu:
            anime.enable_danmu()

        if get_info:
            anime.get_info()
            if self._progress_bus is not None:
                self._progress_bus.update_status(sn, '任務完成')
                self._progress_bus.finish(sn)
            return

        try:
            anime.download(
                resolution=resolution or str(self._settings.download_resolution),
                save_dir=save_dir,
                classify=classify,
                realtime_show_file_size=realtime_show_file_size,
            )
        except exceptions.TaskCancelledError:
            self._logger.info(sn, '任務取消', '使用者取消了下載任務', display=False)
            # cancel() already scheduled finish() via Timer — the finally below is
            # a no-op because finish() is idempotent.
        except exceptions.TryTooManyTimeError as exc:
            self._logger.error(sn, '下載失敗', f'重試已耗盡: {exc}', display=False)
            if self._progress_bus is not None:
                self._progress_bus.update_status(sn, '失敗')
        except exceptions.NoAvailableStreamError as exc:
            self._logger.error(sn, '下載失敗', f'無可用影片源: {exc}', display=False)
            if self._progress_bus is not None:
                self._progress_bus.update_status(sn, '失敗')
        except Exception as exc:  # noqa: BLE001
            # Catch-all: unexpected errors should never silently leak without
            # closing the progress entry.  Log and continue so the thread pool
            # can complete remaining tasks.
            self._logger.error(sn, '下載失敗', f'未預期的錯誤: {exc}', display=False)
            if self._progress_bus is not None:
                self._progress_bus.update_status(sn, '失敗')
        finally:
            # Safety net: ensure finish() is always called so the DB row is
            # closed and the UI card transitions out of the active state.
            # finish() is idempotent — calling it after cancel() is a no-op.
            if self._progress_bus is not None:
                self._progress_bus.finish(sn)

    def _pre_parse(self, sn: int) -> None:
        """Best-effort metadata fetch that updates the progress UI before download.

        Runs concurrently with other pre-parses in pool A (up to
        ``_PRE_PARSE_MAX_WORKERS``), non-blocking with respect to pool B
        (the actual download pool).

        On success, calls ``ProgressBus.update_metadata`` so the UI shows the
        bangumi name and episode label while the task is still in the
        '等待下載' queue.

        On any failure, logs a warning and returns — the real download pipeline
        will re-fetch the same data and surface any real error through the
        normal path.
        """
        if self._progress_bus is None or self._metadata_extractor is None:
            return
        if self._parse_cooldown is not None:
            self._parse_cooldown.wait(progress_bus=self._progress_bus, sn=sn)
        try:
            meta = self._metadata_extractor.fetch(sn)
            self._progress_bus.update_metadata(
                sn,
                bangumi_name=meta.bangumi_name or None,
                episode=meta.episode or None,
            )
            self._logger.info(
                sn,
                '預解析',
                f'取得 {meta.bangumi_name} EP{meta.episode}',
                display=False,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.error(
                sn,
                'PreParse',
                f'解析失敗（將在下載時重試）: {exc}',
                display=False,
            )

    def _download_many(
        self,
        sns: list[int],
        *,
        thread_limit: int,
        resolution: str,
        save_dir: pathlib.Path | None,
        classify: bool,
        get_info: bool,
        cui_danmu: bool,
        realtime_show_file_size: bool,
        owner_id: str | None = None,
    ) -> None:
        if not sns:
            return

        # Announce all episodes as "等待下載" BEFORE submitting to the thread
        # pool so the Monitor UI shows N waiting cards immediately, not just the
        # first N that happen to get a worker slot right away.  The second
        # start() call inside _download_one (via Anime.download) is idempotent
        # and will only update the in-memory status without inserting a duplicate
        # DB row.
        for sn in sns:
            self._announce_waiting(int(sn), owner_id=owner_id)

        # Pool A: pre-parse all queued sns concurrently so the UI shows bangumi
        # names while tasks are waiting.  This pool is deliberately NOT joined
        # before pool B starts — pre-parse is best-effort and must not delay
        # the first actual download.  shutdown(wait=False) means the parse
        # threads will finish on their own; if they're still running when pool B
        # completes that is fine — update_metadata is idempotent and thread-safe.
        if self._metadata_extractor is not None and self._progress_bus is not None:
            parse_workers = min(_PRE_PARSE_MAX_WORKERS, len(sns))
            parse_pool = concurrent.futures.ThreadPoolExecutor(max_workers=parse_workers)
            for sn in sns:
                parse_pool.submit(self._pre_parse, int(sn))
            parse_pool.shutdown(wait=False)

        max_workers = max(1, int(thread_limit))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(
                    self._download_one,
                    int(sn),
                    resolution=resolution,
                    save_dir=save_dir,
                    classify=classify,
                    get_info=get_info,
                    cui_danmu=cui_danmu,
                    realtime_show_file_size=realtime_show_file_size,
                    owner_id=owner_id,
                )
                for sn in sns
            ]
            for fut in concurrent.futures.as_completed(futures):
                # Re-raise will propagate the first exception; we deliberately
                # log + swallow per-sn failures inside ``_download_one`` so
                # the pool completes every task.
                fut.result()

    def _expand_range(self, ep_range: list[str]) -> set[str]:
        """Expand ``["2-4", "6"]`` into ``{"2", "3", "4", "6"}``."""
        out: set[str] = set()
        for item in ep_range:
            text = str(item)
            if '-' in text:
                try:
                    start_s, end_s = text.split('-', 1)
                    start, end = int(start_s), int(end_s)
                except ValueError:
                    out.add(text)
                    continue
                if start > end:
                    start, end = end, start
                for i in range(start, end + 1):
                    out.add(str(i))
            else:
                out.add(text)
        return out

"""BilibiliRunner — orchestrates a single Bilibili download task."""

from __future__ import annotations

import collections.abc
import concurrent.futures
import contextlib
import typing as T

import yt_dlp.utils

from ..ffmpeg import resolve_ffmpeg_path

if T.TYPE_CHECKING:
    from ...downloader.progress import ProgressBus
    from ...logging_ import Logger
    from ...models import AppSettings
    from ...persistence.anime_list_repo import AnimeListEntryRepository
    from ...persistence.task_id_map_repo import TaskIdMapRepository
    from .ytdlp_downloader import YtdlpDownloader


class BilibiliRunner:
    """Orchestrates one Bilibili download: start → extract_info → download → finish.

    For multi-part BVs (info['entries'] has > 1 element) the parent_sn is
    used only as a cancel-propagation anchor; one child_sn is allocated per
    part and each part gets its own Monitor UI card.
    """

    def __init__(
        self,
        ytdlp_downloader: YtdlpDownloader,
        progress_bus: ProgressBus,
        logger: Logger,
        settings: AppSettings,
        notify_event_send: collections.abc.Callable[..., None] | None = None,
        anime_list_repo: AnimeListEntryRepository | None = None,
        task_id_map_repo: TaskIdMapRepository | None = None,
    ) -> None:
        self._downloader = ytdlp_downloader
        self._progress_bus = progress_bus
        self._logger = logger
        self._settings = settings
        self._notify_event_send = notify_event_send
        self._anime_list_repo = anime_list_repo
        self._task_id_map_repo = task_id_map_repo

    def run(
        self,
        task_sn: int,
        *,
        bvid: str,
        resolution: str,
        classify: bool,
        owner_id: str | None = None,
    ) -> None:
        ffmpeg_path = resolve_ffmpeg_path()
        if ffmpeg_path is None:
            self._logger.error(
                None,
                'BilibiliRunner',
                '未安裝 ffmpeg；請將 ffmpeg.exe 放到 backend/ 目錄下，或加入系統 PATH。'
                'Bilibili 1080P 影片下載需要 ffmpeg 合成音訊與影像。',
                display=False,
            )
            self._progress_bus.start(
                task_sn,
                bvid,
                status='失敗',
                owner_id=owner_id,
                source='bilibili',
                external_id=bvid,
            )
            self._progress_bus.update_status(task_sn, '失敗')
            self._emit_telegram_event(
                event='failed',
                task_sn=task_sn,
                title=bvid,
                episode=None,
                resolution=resolution,
                owner_id=owner_id,
                error_message='ffmpeg 未安裝，無法處理 Bilibili DASH 影片串流',
            )
            self._progress_bus.finish(task_sn)
            return

        info = self._downloader.extract_info(bvid)
        entries = info.get('entries')

        # ── Multi-part divergence decision point ──────────────────────────────
        if entries and len(entries) > 1:
            self._run_multipart(
                parent_sn=task_sn,
                bvid=bvid,
                info=info,
                entries=entries,
                resolution=resolution,
                classify=classify,
                owner_id=owner_id,
            )
            return
        # ── Single-part (original) flow ───────────────────────────────────────
        self._run_singlepart(
            task_sn=task_sn,
            bvid=bvid,
            info=info,
            resolution=resolution,
            classify=classify,
            owner_id=owner_id,
        )

    # ------------------------------------------------------------------ single-part

    def _run_singlepart(
        self,
        *,
        task_sn: int,
        bvid: str,
        info: dict[str, T.Any],
        resolution: str,
        classify: bool,
        owner_id: str | None,
    ) -> None:
        self._progress_bus.start(
            task_sn,
            bvid,
            status='等待下載',
            owner_id=owner_id,
            source='bilibili',
            external_id=bvid,
        )

        title = info.get('title') or bvid
        episode: str | None = None
        file_size_mb: int | None = None

        try:
            self._progress_bus.update_metadata(task_sn, bangumi_name=title, filename=title)

            self._emit_telegram_event(
                event='started',
                task_sn=task_sn,
                title=title,
                episode=episode,
                resolution=resolution,
                owner_id=owner_id,
            )

            resolved_info = self._downloader.download(
                task_sn,
                bvid,
                resolution=resolution,
                classify=classify,
            )

            filesize_bytes = None
            if resolved_info:
                filesize_bytes = resolved_info.get('filesize') or resolved_info.get('filesize_approx')
                if not filesize_bytes and resolved_info.get('requested_downloads'):
                    rd = resolved_info['requested_downloads'][0]
                    filesize_bytes = rd.get('filesize') or rd.get('filesize_approx')
            if filesize_bytes:
                file_size_mb = int(filesize_bytes / (1024 * 1024))

            self._emit_telegram_event(
                event='completed',
                task_sn=task_sn,
                title=title,
                episode=episode,
                resolution=resolution,
                owner_id=owner_id,
                file_size_mb=file_size_mb,
            )

        except yt_dlp.utils.DownloadCancelled:
            self._emit_telegram_event(
                event='cancelled',
                task_sn=task_sn,
                title=title,
                episode=episode,
                resolution=resolution,
                owner_id=owner_id,
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)[:200]
            self._logger.error(
                None,
                'BilibiliRunner',
                f'Download failed for {bvid}: {error_msg}',
                display=False,
            )
            self._progress_bus.update_status(task_sn, '失敗')
            self._emit_telegram_event(
                event='failed',
                task_sn=task_sn,
                title=title,
                episode=episode,
                resolution=resolution,
                owner_id=owner_id,
                error_message=error_msg,
            )
        finally:
            self._progress_bus.finish(task_sn)

    # ------------------------------------------------------------------ multi-part

    def _run_multipart(
        self,
        *,
        parent_sn: int,
        bvid: str,
        info: dict[str, T.Any],
        entries: list[T.Any],
        resolution: str,
        classify: bool,
        owner_id: str | None,
    ) -> None:
        n = len(entries)
        parent_title = info.get('title') or bvid

        # Allocate one child_sn per part; fall back to a simple counter-based
        # sn when no task_id_map_repo is wired (e.g. unit tests).
        child_sns: list[int] = []
        for idx in range(1, n + 1):
            external_id = f'{bvid}_p{idx}'
            if self._task_id_map_repo is not None:
                child_sn = self._task_id_map_repo.allocate(source='bilibili', external_id=external_id)
            else:
                child_sn = parent_sn * 1000 + idx
            child_sns.append(child_sn)

        # Announce ALL children as 等待下載 before any download begins.
        for idx, child_sn in enumerate(child_sns, start=1):
            part_title = f'{parent_title} - p{idx}'
            self._progress_bus.start(
                child_sn,
                part_title,
                status='等待下載',
                bangumi_name=parent_title,
                episode=f'P{idx}/{n}',
                owner_id=owner_id,
                source='bilibili',
                external_id=f'{bvid}_p{idx}',
            )

        max_workers = max(1, int(self._settings.bilibili_concurrent_parts))

        def _download_one_part(idx: int, child_sn: int) -> None:
            episode = f'P{idx}/{n}'

            self._progress_bus.update_status(child_sn, '正在下載')
            self._emit_telegram_event(
                event='started',
                task_sn=child_sn,
                title=parent_title,
                episode=episode,
                resolution=resolution,
                owner_id=owner_id,
            )

            file_size_mb: int | None = None
            try:
                resolved_info = self._downloader.download(
                    child_sn,
                    bvid,
                    resolution=resolution,
                    classify=classify,
                    part_idx=idx,
                    parent_sn=parent_sn,
                )

                filesize_bytes = None
                if resolved_info:
                    filesize_bytes = resolved_info.get('filesize') or resolved_info.get('filesize_approx')
                    if not filesize_bytes and resolved_info.get('requested_downloads'):
                        rd = resolved_info['requested_downloads'][0]
                        filesize_bytes = rd.get('filesize') or rd.get('filesize_approx')
                if filesize_bytes:
                    file_size_mb = int(filesize_bytes / (1024 * 1024))

                self._emit_telegram_event(
                    event='completed',
                    task_sn=child_sn,
                    title=parent_title,
                    episode=episode,
                    resolution=resolution,
                    owner_id=owner_id,
                    file_size_mb=file_size_mb,
                )

            except yt_dlp.utils.DownloadCancelled:
                self._emit_telegram_event(
                    event='cancelled',
                    task_sn=child_sn,
                    title=parent_title,
                    episode=episode,
                    resolution=resolution,
                    owner_id=owner_id,
                )
                raise

            except Exception as exc:  # noqa: BLE001
                error_msg = str(exc)[:200]
                self._logger.error(
                    None,
                    'BilibiliRunner',
                    f'Download failed for {bvid} part {idx}: {error_msg}',
                    display=False,
                )
                self._progress_bus.update_status(child_sn, '失敗')
                self._emit_telegram_event(
                    event='failed',
                    task_sn=child_sn,
                    title=parent_title,
                    episode=episode,
                    resolution=resolution,
                    owner_id=owner_id,
                    error_message=error_msg,
                )

            finally:
                self._progress_bus.finish(child_sn)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_download_one_part, idx, child_sn): child_sn
                for idx, child_sn in enumerate(child_sns, start=1)
            }
            for fut in concurrent.futures.as_completed(futures):
                with contextlib.suppress(yt_dlp.utils.DownloadCancelled, Exception):
                    fut.result()

        snap = self._progress_bus.snapshot()
        for child_sn in child_sns:
            entry = snap.get(child_sn)
            if entry is not None and entry.status in ('等待下載', '正在下載'):
                self._progress_bus.update_status(child_sn, '已取消')
                self._progress_bus.finish(child_sn)

    # ------------------------------------------------------------------ shared

    def _emit_telegram_event(
        self,
        *,
        event: str,
        task_sn: int,
        title: str,
        episode: str | None,
        resolution: str,
        owner_id: str | None,
        file_size_mb: int | None = None,
        error_message: str | None = None,
    ) -> None:
        if self._notify_event_send is None:
            return
        payload: dict[str, T.Any] = {
            'event': event,
            'owner_id': owner_id,
            'sn': task_sn,
            'bangumi_name': title,
            'episode': episode,
            'resolution': resolution,
            'custom_name': None,
            'season': 1,
            'episode_number': None,
        }
        if file_size_mb is not None:
            payload['file_size_mb'] = file_size_mb
        if error_message is not None:
            payload['error_message'] = error_message
        with contextlib.suppress(Exception):
            self._notify_event_send(kwargs=payload)

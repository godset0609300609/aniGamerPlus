"""High-level ``Anime`` orchestrator — the replacement for legacy ``Anime``.

Unlike the legacy class (1000-line ``__init__`` triggered four network
round trips, then a 500-line ``download`` handled everything from path
computation to FTP to Plex), this one:

- Takes every collaborator via constructor injection.
- Does zero I/O in ``__init__``; caller must invoke ``.load()``
  (idempotent) before other methods.
- Splits the old god-method into focused helpers so ``download`` reads
  top-to-bottom.
- Leaves upload / notify calls to the Worker (Batch 5) — the orchestrator
  only produces a file.
"""

from __future__ import annotations

import dataclasses
import pathlib
import typing as T

from . import exceptions

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..models import AppSettings
    from ..persistence.paths import WorkspacePaths
    from ..scheduler.cd_counter import DownloadCooldown
    from .danmu import DanmuRenderer
    from .ffmpeg_downloader import FFmpegDownloader
    from .filename import FilenameBuilder
    from .m3u8_client import M3u8Client
    from .metadata import AnimeMetadata, MetadataExtractor
    from .notifier import CompositeNotifier
    from .progress import ProgressBus
    from .segment_downloader import SegmentDownloader
    from .uploader_ftp import FtpUploader


@dataclasses.dataclass(slots=True)
class DownloadResult:
    """Return value of :meth:`Anime.download`."""

    success: bool
    file_path: pathlib.Path | None
    size_mb: int


class Anime:
    """Orchestrator for one sn.

    ``__init__`` does NO network I/O. Call :meth:`load` first (idempotent)
    before using any of the metadata getters or :meth:`download`.
    """

    def __init__(
        self,
        sn: int,
        *,
        metadata_extractor: MetadataExtractor,
        m3u8_client: M3u8Client,
        segment_downloader: SegmentDownloader,
        ffmpeg_downloader: FFmpegDownloader,
        filename_builder: FilenameBuilder,
        danmu_renderer: DanmuRenderer,
        uploader: FtpUploader | None,
        notifier: CompositeNotifier,
        progress: ProgressBus,
        settings: AppSettings,
        paths: WorkspacePaths,
        logger: Logger,
        cooldown: DownloadCooldown | None = None,
    ) -> None:
        self._sn = int(sn)
        self._metadata_extractor = metadata_extractor
        self._m3u8_client = m3u8_client
        self._segment_downloader = segment_downloader
        self._ffmpeg_downloader = ffmpeg_downloader
        self._filename_builder = filename_builder
        self._danmu_renderer = danmu_renderer
        self._uploader = uploader
        self._notifier = notifier
        self._progress = progress
        self._settings = settings
        self._paths = paths
        self._logger = logger
        self._cooldown = cooldown

        self._metadata: AnimeMetadata | None = None
        self._m3u8_dict: dict[str, str] | None = None
        self._danmu_enabled = False
        self._video_resolution = 0

        # Populated by ``download`` so the Worker in Batch 5 can pick them up.
        self._last_file_path: pathlib.Path | None = None
        self._last_filename = ''
        self._last_size_mb = 0
        self._last_bangumi_tag = ''

    # ------------------------------------------------------------------ lifecycle

    def load(self) -> None:
        """Fetch metadata. Idempotent — a second call is a no-op."""
        if self._metadata is not None:
            return
        self._metadata = self._metadata_extractor.fetch(self._sn)

    def renew(self) -> None:
        """Invalidate cached metadata / m3u8 — force re-fetch on next call."""
        self._metadata = None
        self._m3u8_dict = None

    # ------------------------------------------------------------------ getters

    @property
    def sn(self) -> int:
        return self._sn

    def get_bangumi_name(self) -> str:
        self.load()
        assert self._metadata is not None
        return self._metadata.bangumi_name

    def get_episode(self) -> str:
        self.load()
        assert self._metadata is not None
        return self._metadata.episode

    def get_episode_list(self) -> dict[str, int]:
        self.load()
        assert self._metadata is not None
        return dict(self._metadata.episode_list)

    def get_title(self) -> str:
        self.load()
        assert self._metadata is not None
        return self._metadata.title

    def get_filename(self, resolution: str = '') -> str:
        self.load()
        assert self._metadata is not None
        res = resolution or str(self._video_resolution or self._settings.download_resolution)
        return self._filename_builder.build(self._metadata, res)

    def get_resolution(self) -> int:
        """Return the resolution selected by the last successful download,
        falling back to ``settings.download_resolution`` when unset."""
        if self._video_resolution:
            return int(self._video_resolution)
        return int(self._settings.download_resolution)

    def get_m3u8_dict(self) -> dict[str, str]:
        self.load()
        if self._m3u8_dict is None:
            self._m3u8_dict = self._m3u8_client.fetch(self._sn)
        return dict(self._m3u8_dict)

    def get_info(self) -> None:
        """Log a summary of metadata (no download)."""
        self.load()
        assert self._metadata is not None
        meta = self._metadata
        self._logger.info(self._sn, '顯示資訊', display_time=False)
        self._logger.info(None, '  影片標題:', meta.title, display_time=False)
        self._logger.info(None, '  番劇名稱:', meta.bangumi_name, display_time=False)
        self._logger.info(None, '  劇集標題:', meta.episode, display_time=False)
        self._logger.info(
            None,
            '  可用解析度:',
            ' '.join(self.get_m3u8_dict().keys()) + 'P',
            display_time=False,
        )

    # ------------------------------------------------------------------ mutators

    def set_resolution(self, resolution: str) -> None:
        self._video_resolution = int(resolution)

    def enable_danmu(self) -> None:
        self._danmu_enabled = True

    # ------------------------------------------------------------------ main pipeline

    def download(
        self,
        *,
        resolution: str = '',
        save_dir: pathlib.Path | None = None,
        bangumi_tag: str = '',
        realtime_show_file_size: bool = False,
        season: int = 1,
        classify: bool = True,
        include_resolution_in_filename: bool = True,
    ) -> DownloadResult:
        """Run the full per-sn download pipeline."""
        self.load()
        assert self._metadata is not None

        filename_preview = f'《{self._metadata.title}》'
        self._progress.start(
            self._sn,
            filename_preview,
            status='等待下載',
            bangumi_name=self._metadata.bangumi_name or None,
            episode=self._metadata.episode or None,
        )

        # Grab the cancel event once; it is safe to hold a reference because
        # the Event object is never replaced, only set.
        cancel_event = self._progress.get_cancel_event(self._sn)

        def _check_cancelled() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise exceptions.TaskCancelledError(f'sn={self._sn} cancelled')

        try:
            _check_cancelled()  # phase: before m3u8 fetch

            m3u8_dict = self.get_m3u8_dict()
            picked_resolution = self._select_resolution(resolution, m3u8_dict)
            selected_url = m3u8_dict[picked_resolution]
            self._video_resolution = int(picked_resolution)

            _check_cancelled()  # phase: before path preparation

            # Persist the resolved resolution back to the progress entry.
            self._progress.update_resolution(self._sn, f'{picked_resolution}p')

            paths = self._prepare_paths(
                picked_resolution,
                save_dir,
                bangumi_tag,
                season,
                classify=classify,
                include_resolution_in_filename=include_resolution_in_filename,
            )

            _check_cancelled()  # phase: before cooldown / download

            # Cooldown is applied here — after all metadata parsing and path
            # preparation, but before any actual segment/ffmpeg download begins.
            # This ensures the user sees "等待下載" immediately on task submit
            # and only waits for the cooldown gap before bytes start flowing.
            if self._cooldown is not None:
                self._cooldown.wait(
                    progress_bus=self._progress,
                    sn=self._sn,
                    status_during='下載冷卻',
                )

            _check_cancelled()  # phase: after cooldown, before download

            self._progress.update_status(self._sn, '正在下載')
            self._logger.info(
                self._sn,
                '開始下載',
                f'開始下載片段 解析度={picked_resolution}p',
                display=False,
            )

            size_mb = self._run_download(
                selected_url,
                paths,
                picked_resolution,
                realtime_show_file_size=realtime_show_file_size,
            )

            _check_cancelled()  # phase: before post-processing

            self._post_process(paths.output_file)

            # Mark status as '下載完成' before returning so that when the outer
            # finally block calls progress.finish(sn), it sees a terminal status
            # and writes it to the DB as-is.  Without this, finish() would
            # normalise the transient '正在下載' status to '中斷', incorrectly
            # marking a successful download as an interrupted one.
            self._progress.update_status(self._sn, '下載完成')
            self._logger.info(
                self._sn,
                '下載完成',
                f'完成 size={size_mb:.1f}MB',
                display=False,
            )

            self._last_file_path = paths.output_file
            self._last_filename = paths.filename
            self._last_size_mb = size_mb
            self._last_bangumi_tag = bangumi_tag

            return DownloadResult(
                success=True,
                file_path=paths.output_file,
                size_mb=size_mb,
            )
        except exceptions.TaskCancelledError:
            # Cancel is already handled by ProgressBus.cancel() — status and
            # finish() are already scheduled. Just log and return a failure result.
            self._logger.info(self._sn, '下載取消', '任務已取消', display=False)
            return DownloadResult(success=False, file_path=None, size_mb=0)
        except exceptions.NoAvailableStreamError:
            # Episode deleted / no playable stream — mark explicitly as '失敗'
            # so the outer finish() call writes a meaningful terminal status to
            # the DB instead of normalising the transient '正在解析' to '中斷'.
            self._progress.update_status(self._sn, '失敗')
            raise
        except exceptions.TryTooManyTimeError:
            self._progress.update_status(self._sn, '失敗')
            raise

    def upload(self, bangumi_tag: str = '', debug_file: str = '') -> bool:
        """Delegate to :class:`FtpUploader`."""
        if self._uploader is None:
            self._logger.error(
                self._sn,
                '上傳失敗',
                'upload() called without an uploader configured',
                display=False,
            )
            return False

        if debug_file:
            local_path = pathlib.Path(debug_file)
            filename = local_path.name
        elif self._last_file_path is not None:
            local_path = self._last_file_path
            filename = self._last_filename
        else:
            self._logger.error(
                self._sn,
                '上傳失敗',
                'upload() with no prior successful download and no debug_file',
                display=False,
            )
            return False

        tag = bangumi_tag or self._last_bangumi_tag
        self.load()
        assert self._metadata is not None
        return self._uploader.upload(
            local_path=local_path,
            filename=filename,
            bangumi_tag=tag,
            bangumi_name=self._metadata.bangumi_name,
            sn=self._sn,
        )

    # ------------------------------------------------------------------ helpers

    def _select_resolution(self, requested: str, m3u8_dict: dict[str, str]) -> str:
        """Pick the best available resolution.

        - Empty ``requested`` → ``settings.download_resolution``.
        - Available → return as-is.
        - Missing + ``lock_resolution`` → raise.
        - Missing + unlocked → pick closest by |Δ|.
        """
        if not m3u8_dict:
            raise exceptions.NoAvailableStreamError(f'sn={self._sn}: no streams available')

        target = requested or str(self._settings.download_resolution)
        if target in m3u8_dict:
            return target

        if self._settings.lock_resolution:
            raise exceptions.NoAvailableStreamError(
                f'sn={self._sn}: resolution {target}P unavailable; have {list(m3u8_dict)} and lock_resolution=True'
            )

        try:
            target_int = int(target)
        except ValueError:
            target_int = 0
        best = min(
            m3u8_dict.keys(),
            key=lambda k: (abs(int(k) - target_int), -int(k)),
        )
        self._logger.info(
            self._sn,
            '解析度回退',
            f'{target}P unavailable; using {best}P',
            display=False,
        )
        return best

    def _prepare_paths(
        self,
        resolution: str,
        save_dir: pathlib.Path | None,
        bangumi_tag: str,
        season: int,
        *,
        classify: bool,
        include_resolution_in_filename: bool = True,
    ) -> _PreparedPaths:
        assert self._metadata is not None
        meta = self._metadata
        filename = self._filename_builder.build(
            meta,
            resolution,
            season=season,
            include_resolution=include_resolution_in_filename,
        )

        base_bangumi_dir = (
            pathlib.Path(save_dir)
            if save_dir is not None
            else pathlib.Path(self._settings.bangumi_dir or self._paths.bangumi_dir_default)
        )
        temp_root = pathlib.Path(self._settings.temp_dir or self._paths.temp_dir_default)

        target_dir = self._filename_builder.classify_dir(meta, base_bangumi_dir, bangumi_tag, season, classify=classify)
        target_dir.mkdir(parents=True, exist_ok=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        output_file = target_dir / filename

        segment_temp_dir = temp_root / f'{self._sn}-downloading-by-aniGamerPlus'

        merging_name = self._filename_builder.build_temp(meta, resolution, temp_suffix='MERGING', season=season)
        downloading_name = self._filename_builder.build_temp(meta, resolution, temp_suffix='DOWNLOADING', season=season)

        return _PreparedPaths(
            filename=filename,
            output_file=output_file,
            temp_root=temp_root,
            segment_temp_dir=segment_temp_dir,
            merging_file=temp_root / merging_name,
            downloading_file=temp_root / downloading_name,
        )

    def _run_download(
        self,
        m3u8_url: str,
        paths: _PreparedPaths,
        resolution: str,
        *,
        realtime_show_file_size: bool,
    ) -> int:
        assert self._metadata is not None
        title = self._metadata.title

        if self._settings.segment_download_mode:
            return self._segment_downloader.download(
                self._sn,
                m3u8_url,
                paths.output_file,
                paths.segment_temp_dir,
                paths.merging_file,
                paths.filename,
                title,
                realtime_show=realtime_show_file_size,
            )

        return self._ffmpeg_downloader.download(
            self._sn,
            m3u8_url,
            paths.output_file,
            paths.downloading_file,
            paths.filename,
            title,
            None,
            realtime_show=realtime_show_file_size,
        )

    def _post_process(self, output_file: pathlib.Path) -> None:
        if self._danmu_enabled:
            self._danmu_renderer.render(
                self._sn,
                output_file,
                ban_words=tuple(self._settings.danmu_ban_words),
            )


@dataclasses.dataclass(slots=True)
class _PreparedPaths:
    filename: str
    output_file: pathlib.Path
    temp_root: pathlib.Path
    segment_temp_dir: pathlib.Path
    merging_file: pathlib.Path
    downloading_file: pathlib.Path

"""yt-dlp wrapper for Bilibili downloads."""

from __future__ import annotations

import pathlib
import re
import time
import typing as T

import yt_dlp
import yt_dlp.utils

if T.TYPE_CHECKING:
    from ...downloader.progress import ProgressBus
    from ...logging_ import Logger
    from ...persistence.bilibili_cookie_repo import BilibiliCookieRepository

_WINDOWS_ILLEGAL = re.compile(r'[<>:"/\\|?*]')
_PART_RE = re.compile(r'\.(part|ytdl|f\d+\..+)$')
_THROTTLE_INTERVAL = 0.25


def _sanitize_title(title: str) -> str:
    sanitized = _WINDOWS_ILLEGAL.sub('_', title)
    return sanitized.rstrip('. ')


class YtdlpDownloader:
    """Wraps yt_dlp.YoutubeDL; bridges progress events to ProgressBus."""

    def __init__(
        self,
        progress_bus: ProgressBus,
        cookie_repo: BilibiliCookieRepository,
        bangumi_dir: pathlib.Path,
        logger: Logger,
        ffmpeg_location: str | None = None,
    ) -> None:
        self._progress_bus = progress_bus
        self._cookie_repo = cookie_repo
        self._bangumi_dir = bangumi_dir
        self._logger = logger
        self._ffmpeg_location = ffmpeg_location

    def _base_opts(self) -> dict[str, T.Any]:
        opts: dict[str, T.Any] = {
            'quiet': True,
            'no_warnings': True,
            'noprogress': True,
            'restrictfilenames': False,
        }
        opts.update(
            {
                'retries': 20,
                'fragment_retries': 20,
                'socket_timeout': 30,
                'retry_sleep_functions': {
                    'http': lambda n: min(2**n, 30),
                    'fragment': lambda n: min(2**n, 30),
                },
            }
        )
        if self._cookie_repo.exists_and_nonempty():
            opts['cookiefile'] = str(self._cookie_repo.path)
        if self._ffmpeg_location is not None:
            opts['ffmpeg_location'] = self._ffmpeg_location
        return opts

    def extract_info(self, bvid: str) -> dict[str, T.Any]:
        url = f'https://www.bilibili.com/video/{bvid}'
        opts = self._base_opts()
        opts['skip_download'] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return info or {}

    def download(
        self,
        task_sn: int,
        bvid: str,
        *,
        resolution: str,
        classify: bool,
        part_idx: int | None = None,
        parent_sn: int | None = None,
    ) -> dict[str, T.Any]:
        """Download *bvid* and return the resolved info dict.

        When *part_idx* is given, only that playlist entry is downloaded
        (``playlist_items=str(part_idx)``).  *parent_sn* is an optional
        additional cancel-event source checked inside the progress hook so
        that aborting the dramatiq parent actor also cancels the active
        child download.

        Raises ``yt_dlp.utils.DownloadCancelled`` when either cancel event fires.
        """
        url = f'https://www.bilibili.com/video/{bvid}'
        res_int = int(resolution) if resolution.isdigit() else 1080
        fmt = f'bestvideo[height<={res_int}]+bestaudio/best[height<={res_int}]/best'

        last_update: dict[str, float] = {'t': 0.0}
        resolved_info: dict[str, T.Any] = {}
        seen: dict[str, tuple[int, int]] = {}

        def _progress_hook(d: dict[str, T.Any]) -> None:
            cancel_event = self._progress_bus.get_cancel_event(task_sn)
            if cancel_event is not None and cancel_event.is_set():
                raise yt_dlp.utils.DownloadCancelled()
            if parent_sn is not None:
                parent_cancel = self._progress_bus.get_cancel_event(parent_sn)
                if parent_cancel is not None and parent_cancel.is_set():
                    raise yt_dlp.utils.DownloadCancelled()

            now = time.monotonic()
            fn = d.get('filename', '')
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            if d['status'] == 'downloading':
                if now - last_update['t'] < _THROTTLE_INTERVAL:
                    return
                last_update['t'] = now
                self._progress_bus.update_status(task_sn, '正在下載')
                downloaded = d.get('downloaded_bytes') or 0
                if total > 0:
                    seen[fn] = (downloaded, total)
                sum_d = sum(v[0] for v in seen.values())
                sum_t = sum(v[1] for v in seen.values())
                if sum_t > 0:
                    self._progress_bus.update_rate(task_sn, round(sum_d / sum_t * 100.0, 2))
                speed_bytes = d.get('speed') or 0
                speed_mbps = speed_bytes / (1024 * 1024) if speed_bytes else None
                eta = d.get('eta')
                self._progress_bus.update_stats(task_sn, speed_mbps=speed_mbps, eta_seconds=eta)
            elif d['status'] == 'finished':
                if fn and total > 0:
                    seen[fn] = (total, total)
                self._progress_bus.update_rate(task_sn, 100.0)
                self._progress_bus.update_stats(task_sn, speed_mbps=None, eta_seconds=None)
                if d.get('info_dict'):
                    resolved_info.update(d['info_dict'])

        def _postprocessor_hook(d: dict[str, T.Any]) -> None:
            pp = d.get('postprocessor', '')
            status = d.get('status', '')
            if 'FFmpeg' in pp or 'Merger' in pp:
                if status == 'started':
                    self._progress_bus.update_status(task_sn, '正在合併')
                    self._progress_bus.update_stats(task_sn, speed_mbps=None, eta_seconds=None)
                elif status == 'finished':
                    self._progress_bus.update_status(task_sn, '下載完成')

        outtmpl = str(self._bangumi_dir / '《%(playlist_title,title)s》%(playlist_index& - p{0}|)s.%(ext)s')

        opts = self._base_opts()
        opts.update(
            {
                'format': fmt,
                'outtmpl': {'default': outtmpl},
                'progress_hooks': [_progress_hook],
                'postprocessor_hooks': [_postprocessor_hook],
                'postprocessors': [],
            }
        )
        if part_idx is not None:
            opts['playlist_items'] = str(part_idx)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    resolved_info.update(info)
        except yt_dlp.utils.DownloadCancelled:
            self._cleanup_partials(bvid)
            raise
        except Exception:
            self._cleanup_partials(bvid)
            raise

        # Idempotent terminal state — covers skipped (already exists), no-mux,
        # and normal postprocessor paths.
        self._progress_bus.update_rate(task_sn, 100.0)
        self._progress_bus.update_status(task_sn, '下載完成')
        self._progress_bus.update_stats(task_sn, speed_mbps=None, eta_seconds=None)

        height = None
        if resolved_info:
            rd_list = resolved_info.get('requested_downloads')
            height = resolved_info.get('height') or (rd_list[0].get('height') if rd_list else None)
        if height:
            self._progress_bus.update_metadata(task_sn, resolution=f'{height}P')

        return resolved_info

    def _cleanup_partials(self, bvid: str) -> None:
        try:
            for p in self._bangumi_dir.iterdir():
                if _PART_RE.search(p.name):
                    p.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

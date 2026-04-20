"""Single-pass ffmpeg downloader — the fallback to ``SegmentDownloader``.

Used when ``settings.segment_download_mode == False``. Runs a single
``ffmpeg -i {m3u8_url} ...`` invocation, parses stderr for ``time=`` lines
to report progress, and watches the partial output for stalls.

Replaces the legacy ``Anime.__ffmpeg_download_mode`` + its embedded
``check_ffmpeg_alive`` watchdog.
"""

from __future__ import annotations

import contextlib
import pathlib
import re
import subprocess
import threading
import time
import typing as T

from . import exceptions
from ._file_utils import move_file

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..models import AppSettings
    from .ffmpeg import FFmpegRunner
    from .progress import ProgressBus


_CANCEL_TERMINATE_TIMEOUT = 5.0  # seconds to wait after terminate() before kill()


_TIME_RE = re.compile(r'time=(\d+):(\d+):(\d+)(?:\.(\d+))?')
_BITRATE_RE = re.compile(r'bitrate=\s*([\d.]+)\s*kbits/s')
_WATCHDOG_STALL_SECONDS = 60

# Minimum wall-clock interval between update_stats calls from _read_stderr.
_REPORT_INTERVAL = 0.5


class FFmpegDownloader:
    """Single-pass ffmpeg download with stderr progress parsing."""

    def __init__(
        self,
        settings: AppSettings,
        ffmpeg: FFmpegRunner,
        progress: ProgressBus,
        logger: Logger,
    ) -> None:
        self._settings = settings
        self._ffmpeg = ffmpeg
        self._progress = progress
        self._logger = logger

    # ------------------------------------------------------------------ public

    def download(
        self,
        sn: int,
        m3u8_url: str,
        output_file: pathlib.Path,
        downloading_file: pathlib.Path,
        filename: str,
        title: str,
        total_duration_seconds: float | None,
        *,
        realtime_show: bool,
    ) -> int:
        """Run ffmpeg, watch it, and on success rename into place."""
        if downloading_file.exists():
            downloading_file.unlink()
        downloading_file.parent.mkdir(parents=True, exist_ok=True)

        # Grab cancel event once; it is safe to hold across the Popen lifetime.
        cancel_event = self._progress.get_cancel_event(sn)

        cmd = self._build_cmd(m3u8_url, downloading_file)
        self._progress.update_status(sn, '正在下載')

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )

        stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(proc, sn, total_duration_seconds),
            daemon=True,
        )
        stderr_thread.start()

        watchdog_stop = threading.Event()
        watchdog_thread = threading.Thread(
            target=self._watchdog,
            args=(proc, downloading_file, watchdog_stop, cancel_event),
            daemon=True,
        )
        watchdog_thread.start()

        return_code = proc.wait()
        watchdog_stop.set()
        stderr_thread.join(timeout=2)
        watchdog_thread.join(timeout=2)

        # If the cancel event fired, clean up the partial file and raise.
        if cancel_event is not None and cancel_event.is_set():
            try:
                if downloading_file.exists():
                    downloading_file.unlink()
            except OSError:
                pass
            raise exceptions.TaskCancelledError(f'sn={sn} ffmpeg cancelled')

        if return_code != 0:
            self._logger.error(
                sn,
                '下載失敗',
                f'{filename} ffmpeg rc={return_code}',
                display=False,
            )
            raise exceptions.TryTooManyTimeError(f'sn={sn} ffmpeg exit code {return_code}')

        if not downloading_file.exists():
            raise exceptions.TryTooManyTimeError(f'sn={sn} ffmpeg exited 0 but {downloading_file} missing')

        self._progress.update_status(sn, '正在移動檔案')
        self._logger.info(
            sn,
            '移動檔案',
            f'從 temp 移動到 {output_file.name}',
            display=False,
        )
        move_file(downloading_file, output_file)
        self._logger.info(
            sn,
            '移動檔案',
            f'已移動到 {output_file}',
            display=False,
        )

        self._progress.update_status(sn, '下載完成')
        return int(output_file.stat().st_size // (1024 * 1024))

    # ------------------------------------------------------------------ helpers

    def _build_cmd(self, m3u8_url: str, downloading_file: pathlib.Path) -> list[str]:
        ffmpeg = str(self._ffmpeg.which())
        cmd: list[str] = [
            ffmpeg,
            '-user_agent',
            self._settings.ua,
            '-headers',
            'Origin: https://ani.gamer.com.tw',
            '-i',
            m3u8_url,
            '-c',
            'copy',
        ]
        if self._settings.faststart_movflags:
            cmd.extend(['-movflags', 'faststart'])
        cmd.extend([str(downloading_file), '-y'])
        return cmd

    def _read_stderr(
        self,
        proc: subprocess.Popen[str],
        sn: int,
        total_duration: float | None,
    ) -> None:
        stream = proc.stderr
        if stream is None:
            return

        wall_start = time.monotonic()
        last_report = time.monotonic()

        try:
            for line in stream:
                now = time.monotonic()
                elapsed_media: float | None = None
                rate: float | None = None
                speed_mbps: float | None = None
                eta_seconds: int | None = None

                # Parse media time progress.
                time_match = _TIME_RE.search(line)
                if time_match and total_duration and total_duration > 0:
                    hours, minutes, seconds, hundreds = time_match.groups()
                    elapsed_media = (
                        int(hours) * 3600
                        + int(minutes) * 60
                        + int(seconds)
                        + (int(hundreds) / 100.0 if hundreds else 0.0)
                    )
                    rate = min(100.0, round(elapsed_media / total_duration * 100.0, 2))

                # Parse bitrate field → approximate speed_mbps.
                bitrate_match = _BITRATE_RE.search(line)
                if bitrate_match:
                    kbits_per_sec = float(bitrate_match.group(1))
                    # kbits/s → MB/s
                    speed_mbps = kbits_per_sec / 8.0 / 1024.0

                # Compute ETA from media-time progress and wall-clock elapsed.
                if (
                    elapsed_media is not None
                    and total_duration is not None
                    and total_duration > 0
                    and elapsed_media > 0
                ):
                    wall_elapsed = now - wall_start
                    if wall_elapsed > 0:
                        # Estimated wall time needed for (total_duration - elapsed_media)
                        # of additional media, at the current encode/download ratio.
                        ratio = wall_elapsed / elapsed_media  # wall-seconds per media-second
                        remaining_media = total_duration - elapsed_media
                        eta_seconds = max(0, int(remaining_media * ratio))

                # Throttle how often we call update_stats.
                if now - last_report >= _REPORT_INTERVAL:
                    last_report = now
                    if rate is not None:
                        self._progress.update_rate(sn, rate)
                    if speed_mbps is not None or eta_seconds is not None:
                        self._progress.update_stats(sn, speed_mbps=speed_mbps, eta_seconds=eta_seconds)
                elif rate is not None:
                    # Always keep rate current even between throttled windows.
                    self._progress.update_rate(sn, rate)

        except ValueError:
            # Stream closed mid-read, nothing to do.
            return

    def _watchdog(
        self,
        proc: subprocess.Popen[str],
        downloading_file: pathlib.Path,
        stop: threading.Event,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Kill the process if ``downloading_file`` stops growing, or if cancelled."""
        last_size = 0
        last_growth_at = time.monotonic()
        while not stop.is_set():
            if proc.poll() is not None:
                return

            # Cancel check — terminate then escalate to kill after timeout.
            if cancel_event is not None and cancel_event.is_set():
                with contextlib.suppress(OSError):
                    proc.terminate()
                # Wait up to _CANCEL_TERMINATE_TIMEOUT seconds for graceful exit.
                deadline = time.monotonic() + _CANCEL_TERMINATE_TIMEOUT
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        return
                    time.sleep(0.1)
                # Escalate to kill.
                with contextlib.suppress(OSError):
                    proc.kill()
                return

            size = 0
            try:
                if downloading_file.exists():
                    size = downloading_file.stat().st_size
            except OSError:
                size = 0

            now = time.monotonic()
            if size > last_size:
                last_size = size
                last_growth_at = now
            elif now - last_growth_at > _WATCHDOG_STALL_SECONDS:
                # Stalled — escalate.
                with contextlib.suppress(OSError):
                    proc.kill()
                return

            if stop.wait(1):
                return

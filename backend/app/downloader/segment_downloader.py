"""Segment downloader — m3u8 parse + threaded chunk fetch + ffmpeg merge.

Replaces the legacy ``Anime.__segment_download_mode``. Responsibilities:

1. Fetch the chunklist m3u8 and save it to a temp dir.
2. Extract the AES-128 key URI and download the key bytes.
3. Enumerate the ``.ts`` segment URIs.
4. Fan out chunk downloads across a bounded thread pool (with per-chunk
   retry + shared-state thread-safety via a lock).
5. Rewrite the m3u8 so it references the local chunk paths and local key
   file; hand the rewritten playlist to ffmpeg for AES decrypt + mux.

The class never invokes ``ffmpeg`` via ``shell=True`` — ``FFmpegRunner``
takes a list argv and enforces that.
"""

from __future__ import annotations

import collections
import concurrent.futures
import pathlib
import re
import shutil
import threading
import time
import typing as T
import urllib.parse

from . import exceptions

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..models import AppSettings
    from .ffmpeg import FFmpegRunner
    from .http_client import AniGamerHttpClient
    from .progress import ProgressBus


_KEY_URI_RE = re.compile(r'(?<=AES-128,URI=")(.*?)(?=")')
_SEGMENT_LINE_RE = re.compile(r'^[^#].*\.ts', re.MULTILINE)
_ABSOLUTE_URL_RE = re.compile(r'^https?://', re.IGNORECASE)

# Sliding-window parameters for speed / ETA calculation.
_WINDOW_SECONDS = 5.0  # look back this many seconds
_REPORT_INTERVAL = 0.5  # emit update_stats at most every N seconds


class SegmentDownloader:
    """m3u8 parse + threaded chunk fetch + key fetch + ffmpeg merge."""

    def __init__(
        self,
        client: AniGamerHttpClient,
        settings: AppSettings,
        ffmpeg: FFmpegRunner,
        progress: ProgressBus,
        logger: Logger,
    ) -> None:
        self._client = client
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
        temp_dir: pathlib.Path,
        merging_file: pathlib.Path,
        filename: str,
        title: str,
        *,
        realtime_show: bool,
    ) -> int:
        """Download all chunks + merge via ffmpeg. Returns size MB."""
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Grab the cancel event once; it is safe to hold a reference outside
        # the lock because the Event object is never replaced, only set.
        cancel_event = self._progress.get_cancel_event(sn)

        playlist_path = temp_dir / 'playlist.m3u8'
        m3u8_text = self._fetch_playlist_body(m3u8_url)
        playlist_path.write_text(m3u8_text, encoding='utf-8')

        key_uri = self._extract_key_uri(m3u8_text)
        base_url = _strip_last_path_segment(m3u8_url)

        key_local_path = temp_dir / 'key.key'
        self._fetch_key(key_uri, base_url, key_local_path)

        chunks = self._extract_chunks(m3u8_text)
        if not chunks:
            raise exceptions.TryTooManyTimeError(f'sn={sn} m3u8 has no segments')

        self._progress.update_status(sn, '正在下載')
        try:
            self._download_all_chunks(sn, chunks, base_url, temp_dir, cancel_event)
        except exceptions.TaskCancelledError:
            _cleanup_temp_dir(temp_dir)
            raise

        localized_path = temp_dir / 'localized.m3u8'
        localized_text = self._localize_m3u8(m3u8_text, key_uri, key_local_path, chunks, temp_dir)
        localized_path.write_text(localized_text, encoding='utf-8')

        # Check for cancellation before starting the expensive ffmpeg merge.
        if cancel_event is not None and cancel_event.is_set():
            _cleanup_temp_dir(temp_dir)
            raise exceptions.TaskCancelledError(f'sn={sn} cancelled before merge')

        self._progress.update_status(sn, '正在解密合併')
        try:
            self._merge(localized_path, merging_file, sn, filename, title, cancel_event)
        except exceptions.TaskCancelledError:
            _cleanup_temp_dir(temp_dir)
            raise

        if not merging_file.exists():
            raise exceptions.TryTooManyTimeError(f'sn={sn} ffmpeg reported success but {merging_file} missing')

        self._progress.update_status(sn, '正在移動檔案')
        self._logger.info(
            sn,
            '移動檔案',
            f'從 temp 移動到 {output_file.name}',
            display=False,
        )
        if output_file.exists():
            output_file.unlink()
        merging_file.replace(output_file)
        self._logger.info(
            sn,
            '移動檔案',
            f'已移動到 {output_file}',
            display=False,
        )

        self._progress.update_status(sn, '下載完成')
        return int(output_file.stat().st_size // (1024 * 1024))

    # ------------------------------------------------------------------ helpers

    def _fetch_playlist_body(self, url: str) -> str:
        response = self._client.get(url, no_cookies=True)
        data = getattr(response, 'content', b'') or b''
        if isinstance(data, bytes):
            return data.decode('utf-8', errors='replace')
        return str(data)

    @staticmethod
    def _extract_key_uri(m3u8_text: str) -> str:
        match = _KEY_URI_RE.search(m3u8_text)
        if not match:
            raise exceptions.TryTooManyTimeError('no AES-128 key URI in m3u8')
        return match.group(0)

    @staticmethod
    def _extract_chunks(m3u8_text: str) -> list[str]:
        raw = _SEGMENT_LINE_RE.findall(m3u8_text)
        return [line.strip() for line in raw if line.strip()]

    def _fetch_key(self, key_uri: str, base_url: str, out_path: pathlib.Path) -> None:
        url = _resolve_url(key_uri, base_url)
        response = self._client.get(url, no_cookies=True)
        content = getattr(response, 'content', b'') or b''
        out_path.write_bytes(bytes(content))

    # ------------------------------------------------------------------ fan-out

    def _download_all_chunks(
        self,
        sn: int,
        chunks: list[str],
        base_url: str,
        temp_dir: pathlib.Path,
        cancel_event: threading.Event | None = None,
    ) -> None:
        total = len(chunks)
        completed_counter: list[int] = [0]
        downloaded_bytes: list[int] = [0]
        counter_lock = threading.Lock()
        workers = max(1, int(self._settings.multi_downloading_segment))

        # Sliding window: deque of (monotonic_timestamp, byte_count) pairs.
        # Guarded by counter_lock so updates are atomic with the counters.
        window: collections.deque[tuple[float, int]] = collections.deque()
        last_report: list[float] = [time.monotonic()]

        # Progress milestone flags — each fires at most once per download.
        logged_25: list[bool] = [False]
        logged_50: list[bool] = [False]
        logged_75: list[bool] = [False]

        def _task(chunk_uri: str) -> None:
            # Check for cancellation before starting each chunk download.
            if cancel_event is not None and cancel_event.is_set():
                raise exceptions.TaskCancelledError(f'sn={sn} cancelled before chunk {chunk_uri}')
            chunk_bytes = self._download_chunk_bytes(chunk_uri, base_url, temp_dir, cancel_event=cancel_event)
            now = time.monotonic()

            # Collect the values we need to report outside the lock.
            emit_stats = False
            speed: float | None = None
            eta: int | None = None
            rate: float = 0.0

            with counter_lock:
                completed_counter[0] += 1
                downloaded_bytes[0] += chunk_bytes
                done = completed_counter[0]
                rate = round(done / total * 100.0, 2)

                # Maintain sliding window.
                window.append((now, chunk_bytes))
                cutoff = now - _WINDOW_SECONDS
                while window and window[0][0] < cutoff:
                    window.popleft()

                # Throttle update_stats to once per _REPORT_INTERVAL.
                since_last = now - last_report[0]
                if since_last >= _REPORT_INTERVAL:
                    last_report[0] = now
                    speed, eta = _compute_speed_eta(window, now, downloaded_bytes[0], total, done, chunk_bytes)
                    emit_stats = True

            # Milestone progress logs — each threshold fires at most once per
            # download so they're visible in the log panel without flooding it.
            if rate >= 25 and not logged_25[0]:
                logged_25[0] = True
                self._logger.info(sn, '下載進度', '25%', display=False)
            if rate >= 50 and not logged_50[0]:
                logged_50[0] = True
                self._logger.info(sn, '下載進度', '50%', display=False)
            if rate >= 75 and not logged_75[0]:
                logged_75[0] = True
                self._logger.info(sn, '下載進度', '75%', display=False)

            # Call progress methods outside the counter_lock to avoid
            # nested-lock ordering with ProgressBus._lock.
            if emit_stats:
                self._progress.update_stats(sn, speed_mbps=speed, eta_seconds=eta, rate=rate)
            else:
                self._progress.update_rate(sn, rate)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_task, chunk) for chunk in chunks]
            for future in concurrent.futures.as_completed(futures):
                exc = future.exception()
                if exc is not None:
                    if isinstance(exc, exceptions.TaskCancelledError):
                        raise exc
                    if isinstance(exc, exceptions.TryTooManyTimeError):
                        raise exc
                    raise exceptions.TryTooManyTimeError(f'chunk download failed: {exc}') from exc

    def _download_chunk_bytes(
        self,
        chunk_uri: str,
        base_url: str,
        temp_dir: pathlib.Path,
        cancel_event: threading.Event | None = None,
    ) -> int:
        """Download one chunk, persist to disk, and return the byte count."""
        url = _resolve_url(chunk_uri, base_url)
        local_name = _chunk_local_name(chunk_uri)
        local_path = temp_dir / local_name

        max_retry = int(self._settings.segment_max_retry)
        attempt = 0
        last_exc: Exception | None = None
        while True:
            # Check for cancellation at the top of each retry iteration so
            # that a cancel signal during a retry delay is noticed promptly.
            if cancel_event is not None and cancel_event.is_set():
                raise exceptions.TaskCancelledError(f'sn chunk {local_name}: cancelled during retry')
            try:
                response = self._client.get(url, no_cookies=True, max_retry=0)
                content = getattr(response, 'content', b'') or b''
                data = bytes(content)
                local_path.write_bytes(data)
                return len(data)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                attempt += 1
                if max_retry >= 0 and attempt > max_retry:
                    break
                continue
        raise exceptions.TryTooManyTimeError(f'chunk {local_name}: retries exhausted ({last_exc})')

    def _download_chunk(
        self,
        chunk_uri: str,
        base_url: str,
        temp_dir: pathlib.Path,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Download one chunk without returning byte count (legacy helper kept
        for reference; internal callers now use ``_download_chunk_bytes``)."""
        self._download_chunk_bytes(chunk_uri, base_url, temp_dir, cancel_event=cancel_event)

    # ------------------------------------------------------------------ merge

    def _localize_m3u8(
        self,
        m3u8_text: str,
        key_uri: str,
        key_local_path: pathlib.Path,
        chunks: list[str],
        temp_dir: pathlib.Path,
    ) -> str:
        text = m3u8_text.replace(key_uri, key_local_path.as_posix())
        for chunk in chunks:
            local_name = _chunk_local_name(chunk)
            text = text.replace(chunk, (temp_dir / local_name).as_posix())
        return text

    def _merge(
        self,
        localized_m3u8: pathlib.Path,
        merging_file: pathlib.Path,
        sn: int,
        filename: str,
        title: str,
        cancel_event: threading.Event | None = None,
    ) -> None:
        args = self._ffmpeg.build_segment_merge_cmd(
            localized_m3u8,
            merging_file,
            faststart=self._settings.faststart_movflags,
            audio_lang=self._settings.audio_language,
        )
        # ``FFmpegRunner.run`` is a synchronous blocking call. The cancel
        # event is checked immediately before we block — if it fires after
        # that point the watchdog inside the downloader (Batch H FFmpegDownloader
        # extension) is responsible. For the segment merge path the ffmpeg
        # subprocess is short-lived so we do a final pre-run check only.
        if cancel_event is not None and cancel_event.is_set():
            raise exceptions.TaskCancelledError(f'sn={sn} cancelled before ffmpeg merge')
        result = self._ffmpeg.run(args, timeout=None)
        if result.returncode != 0:
            self._logger.error(
                sn,
                '下載失敗',
                f'{filename} ffmpeg merge failed rc={result.returncode}',
                display=False,
            )
            raise exceptions.TryTooManyTimeError(f'sn={sn} ffmpeg merge returned {result.returncode}')


# ---------------------------------------------------------------------------
# Speed / ETA helpers
# ---------------------------------------------------------------------------


def _compute_speed_eta(
    window: collections.deque[tuple[float, int]],
    now: float,
    downloaded_bytes: int,
    total_chunks: int,
    done_chunks: int,
    last_chunk_bytes: int,
) -> tuple[float | None, int | None]:
    """Return ``(speed_mbps, eta_seconds)`` from the sliding window.

    Returns ``(None, None)`` when the window covers less than 1 second of
    data (insufficient history for a meaningful estimate).
    """
    if not window:
        return None, None

    oldest_ts = window[0][0]
    window_duration = now - oldest_ts
    if window_duration < 1.0:
        return None, None

    window_bytes = sum(b for _, b in window)
    speed_bps = window_bytes / window_duration  # bytes per second
    speed_mbps = speed_bps / (1024 * 1024)

    # Estimate remaining bytes from average chunk size.
    avg_chunk_bytes = downloaded_bytes / done_chunks if done_chunks > 0 else last_chunk_bytes
    remaining_chunks = total_chunks - done_chunks
    remaining_bytes = remaining_chunks * avg_chunk_bytes

    eta_seconds: int | None = None
    if speed_bps > 0:
        eta_seconds = int(remaining_bytes / speed_bps)

    return speed_mbps, eta_seconds


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _strip_last_path_segment(url: str) -> str:
    """Return ``url`` with everything after the final ``/`` removed, no slash.

    Mirrors legacy ``os.path.split(m3u8_url)[0]`` used to build chunk URLs.
    """
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path
    if '/' in path:
        path = path[: path.rfind('/')]
    stripped = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))
    return stripped


def _resolve_url(uri: str, base_url: str) -> str:
    """Join ``uri`` against ``base_url`` if it's not already absolute."""
    if _ABSOLUTE_URL_RE.match(uri):
        return uri
    return base_url.rstrip('/') + '/' + uri.lstrip('/')


def _chunk_local_name(chunk_uri: str) -> str:
    """Derive a stable on-disk filename from a chunk URI."""
    parsed = urllib.parse.urlsplit(chunk_uri)
    path = parsed.path if parsed.path else chunk_uri
    name = path.rsplit('/', 1)[-1]
    return name or 'chunk.ts'


def _cleanup_temp_dir(temp_dir: pathlib.Path) -> None:
    """Remove ``temp_dir`` and all its contents silently.

    Called on ``TaskCancelledError`` to reclaim partial downloads.
    Errors are swallowed so the cancel path itself never raises.
    """
    try:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

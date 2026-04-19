"""Tests for ``SegmentDownloader``."""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess
from collections.abc import Mapping
from typing import Any

import pytest

from app.downloader import exceptions
from app.downloader.progress import ProgressBus
from app.downloader.segment_downloader import SegmentDownloader
from app.logging_ import Logger
from app.models import AppSettings


_M3U8_BODY = (
    '#EXTM3U\n'
    '#EXT-X-VERSION:3\n'
    '#EXT-X-TARGETDURATION:6\n'
    '#EXT-X-KEY:METHOD=AES-128,URI="key.key",IV=0x00\n'
    '#EXTINF:6.000,\n'
    'media_b_0.ts\n'
    '#EXTINF:6.000,\n'
    'media_b_1.ts\n'
    '#EXTINF:6.000,\n'
    'media_b_2.ts\n'
    '#EXT-X-ENDLIST\n'
)


@dataclasses.dataclass
class _FakeResponse:
    status_code: int = 200
    text: str = ''
    content: bytes = b''
    cookies: dict[str, str] = dataclasses.field(default_factory=dict)
    headers: dict[str, str] = dataclasses.field(default_factory=dict)

    def json(self) -> Any:  # pragma: no cover — not used here
        import json

        return json.loads(self.text or 'null')


class _FakeClient:
    """Stub ``AniGamerHttpClient`` with URL-routed canned responses."""

    def __init__(
        self,
        *,
        body: str = _M3U8_BODY,
        chunk_data: bytes = b'X' * 2048,
        key_data: bytes = b'0123456789abcdef',
        fail_chunk_uris: set[str] | None = None,
        fail_times: int = 0,
    ) -> None:
        self.body = body
        self.chunk_data = chunk_data
        self.key_data = key_data
        self.fail_chunk_uris = fail_chunk_uris or set()
        self.fail_times = fail_times
        self._fail_counter: dict[str, int] = {}
        self.calls: list[str] = []

    def get(
        self,
        url: str,
        *,
        no_cookies: bool = False,
        max_retry: int = 3,
        extra_headers: Mapping[str, str] | None = None,
        use_pyhttpx: bool = False,
    ) -> _FakeResponse:
        self.calls.append(url)
        if url in self.fail_chunk_uris:
            count = self._fail_counter.get(url, 0)
            if count < self.fail_times:
                self._fail_counter[url] = count + 1
                raise ConnectionError(f'simulated fail {url}')
        if url.endswith('.m3u8'):
            return _FakeResponse(content=self.body.encode('utf-8'), text=self.body)
        if url.endswith('key.key'):
            return _FakeResponse(content=self.key_data)
        return _FakeResponse(content=self.chunk_data)


class _FakeFFmpeg:
    """Stub ``FFmpegRunner`` — captures build + run calls."""

    def __init__(self, *, returncode: int = 0, merging_file_size: int = 0) -> None:
        self.returncode = returncode
        self.merging_file_size = merging_file_size
        self.build_calls: list[dict[str, Any]] = []
        self.run_calls: list[list[str]] = []
        self._pending_merging_file: pathlib.Path | None = None

    def which(self) -> pathlib.Path:
        return pathlib.Path('/usr/bin/ffmpeg')

    def build_segment_merge_cmd(
        self,
        m3u8: pathlib.Path,
        output: pathlib.Path,
        *,
        faststart: bool,
        audio_lang: bool,
    ) -> list[str]:
        self.build_calls.append(
            {
                'm3u8': m3u8,
                'output': output,
                'faststart': faststart,
                'audio_lang': audio_lang,
            }
        )
        self._pending_merging_file = output
        return [
            '-allowed_extensions',
            'ALL',
            '-protocol_whitelist',
            'file,http,https,tcp,tls,crypto',
            '-i',
            str(m3u8),
            '-c',
            'copy',
            str(output),
            '-y',
        ]

    def run(self, args, *, timeout: float | None = None) -> 'subprocess.CompletedProcess[str]':
        self.run_calls.append(list(args))
        # Write a plausible merging file so .stat() succeeds.
        if self._pending_merging_file is not None and self.returncode == 0:
            self._pending_merging_file.parent.mkdir(parents=True, exist_ok=True)
            self._pending_merging_file.write_bytes(b'\x00' * (self.merging_file_size or 1024 * 1024 * 2))
        return subprocess.CompletedProcess(
            args=['/usr/bin/ffmpeg', *args],
            returncode=self.returncode,
            stdout='',
            stderr='',
        )


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture
def progress() -> ProgressBus:
    bus = ProgressBus()
    bus.start(1, 'test-file', status='正在解析')
    return bus


def _settings(**overrides: Any) -> AppSettings:
    base: dict[str, Any] = {
        'ua': 'Mozilla/5.0',
        'multi_downloading_segment': 2,
        'segment_max_retry': 3,
    }
    base.update(overrides)
    return AppSettings(**base)


def _m3u8_url() -> str:
    return 'https://cdn.example.com/path/chunklist.m3u8'


def test_parses_m3u8_and_writes_localized_playlist(
    tmp_path: pathlib.Path, logger: Logger, progress: ProgressBus
) -> None:
    client = _FakeClient()
    ffmpeg = _FakeFFmpeg(merging_file_size=3 * 1024 * 1024)
    downloader = SegmentDownloader(client, _settings(), ffmpeg, progress, logger)

    output_file = tmp_path / 'out.mp4'
    temp_dir = tmp_path / 'temp'
    merging_file = tmp_path / 'temp' / 'merging.mp4'

    size = downloader.download(
        1,
        _m3u8_url(),
        output_file,
        temp_dir,
        merging_file,
        'out.mp4',
        'the-title',
        realtime_show=False,
    )

    assert size >= 1
    localized = temp_dir / 'localized.m3u8'
    content = localized.read_text(encoding='utf-8')
    # Key URI replaced with absolute local path; relative ``key.key`` gone.
    assert 'URI="key.key"' not in content
    assert (temp_dir / 'key.key').as_posix() in content
    # Each chunk should reference the local path.
    for chunk_name in ('media_b_0.ts', 'media_b_1.ts', 'media_b_2.ts'):
        assert (temp_dir / chunk_name).as_posix() in content


def test_progress_reaches_100_percent(tmp_path: pathlib.Path, logger: Logger, progress: ProgressBus) -> None:
    client = _FakeClient()
    ffmpeg = _FakeFFmpeg(merging_file_size=1024 * 1024)
    downloader = SegmentDownloader(client, _settings(), ffmpeg, progress, logger)

    downloader.download(
        1,
        _m3u8_url(),
        tmp_path / 'out.mp4',
        tmp_path / 'temp',
        tmp_path / 'merge.mp4',
        'out.mp4',
        't',
        realtime_show=False,
    )

    entry = progress.snapshot()[1]
    assert entry.rate == pytest.approx(100.0)
    assert entry.status == '下載完成'


def test_chunk_failure_retries_then_raises(tmp_path: pathlib.Path, logger: Logger, progress: ProgressBus) -> None:
    bad_url = 'https://cdn.example.com/path/media_b_1.ts'
    client = _FakeClient(fail_chunk_uris={bad_url}, fail_times=999)
    ffmpeg = _FakeFFmpeg()
    settings = _settings(segment_max_retry=2, multi_downloading_segment=1)
    downloader = SegmentDownloader(client, settings, ffmpeg, progress, logger)

    with pytest.raises(exceptions.TryTooManyTimeError):
        downloader.download(
            1,
            _m3u8_url(),
            tmp_path / 'out.mp4',
            tmp_path / 'temp',
            tmp_path / 'merge.mp4',
            'out.mp4',
            't',
            realtime_show=False,
        )
    # Attempted 1 + 2 retries = 3 requests to the bad url.
    bad_calls = [c for c in client.calls if c == bad_url]
    assert len(bad_calls) == 3


def test_ffmpeg_merge_invoked_with_expected_args(tmp_path: pathlib.Path, logger: Logger, progress: ProgressBus) -> None:
    client = _FakeClient()
    ffmpeg = _FakeFFmpeg(merging_file_size=1024 * 1024)
    settings = _settings(faststart_movflags=True, audio_language=True)
    downloader = SegmentDownloader(client, settings, ffmpeg, progress, logger)

    downloader.download(
        1,
        _m3u8_url(),
        tmp_path / 'out.mp4',
        tmp_path / 'temp',
        tmp_path / 'merge.mp4',
        'out.mp4',
        't',
        realtime_show=False,
    )

    assert len(ffmpeg.build_calls) == 1
    call = ffmpeg.build_calls[0]
    assert call['faststart'] is True
    assert call['audio_lang'] is True
    assert call['m3u8'] == tmp_path / 'temp' / 'localized.m3u8'
    assert call['output'] == tmp_path / 'merge.mp4'
    # The runner is invoked with the bare flag list — no ffmpeg binary
    # prepended. FFmpegRunner.run is the single place that resolves the
    # binary, so handing it [ffmpeg, ...] would produce [ffmpeg, ffmpeg, ...].
    assert len(ffmpeg.run_calls) == 1
    run_args = ffmpeg.run_calls[0]
    assert not any(pathlib.Path(token).name in {'ffmpeg', 'ffmpeg.exe'} for token in run_args)
    assert run_args[0] == '-allowed_extensions'


def test_returns_file_size_in_mb(tmp_path: pathlib.Path, logger: Logger, progress: ProgressBus) -> None:
    client = _FakeClient()
    # Exactly 5 MiB merging file.
    ffmpeg = _FakeFFmpeg(merging_file_size=5 * 1024 * 1024)
    downloader = SegmentDownloader(client, _settings(), ffmpeg, progress, logger)

    size_mb = downloader.download(
        1,
        _m3u8_url(),
        tmp_path / 'out.mp4',
        tmp_path / 'temp',
        tmp_path / 'merge.mp4',
        'out.mp4',
        't',
        realtime_show=False,
    )
    assert size_mb == 5


def test_segment_max_retry_minus_one_means_infinite(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With segment_max_retry=-1 the loop should not cap itself.

    We simulate a transient fail: first 2 attempts fail, 3rd succeeds.
    The downloader must keep trying (no ``max_retry`` short-circuit).
    """
    flaky_url = 'https://cdn.example.com/path/media_b_0.ts'
    client = _FakeClient(fail_chunk_uris={flaky_url}, fail_times=2)
    ffmpeg = _FakeFFmpeg(merging_file_size=1024 * 1024)
    settings = _settings(segment_max_retry=-1, multi_downloading_segment=1)
    downloader = SegmentDownloader(client, settings, ffmpeg, progress, logger)

    # Run and make sure it succeeds.
    size = downloader.download(
        1,
        _m3u8_url(),
        tmp_path / 'out.mp4',
        tmp_path / 'temp',
        tmp_path / 'merge.mp4',
        'out.mp4',
        't',
        realtime_show=False,
    )
    assert size >= 1
    # Exactly 3 hits to the flaky URL: 2 fails + 1 success.
    assert [c for c in client.calls if c == flaky_url].count(flaky_url) == 3


def test_ffmpeg_failure_raises_try_too_many(tmp_path: pathlib.Path, logger: Logger, progress: ProgressBus) -> None:
    client = _FakeClient()
    ffmpeg = _FakeFFmpeg(returncode=1)
    downloader = SegmentDownloader(client, _settings(), ffmpeg, progress, logger)

    with pytest.raises(exceptions.TryTooManyTimeError):
        downloader.download(
            1,
            _m3u8_url(),
            tmp_path / 'out.mp4',
            tmp_path / 'temp',
            tmp_path / 'merge.mp4',
            'out.mp4',
            't',
            realtime_show=False,
        )


# ---------------------------------------------------------------------------
# Batch H — cancel tests
# ---------------------------------------------------------------------------


def test_cancel_between_chunks_raises_and_cleans_temp(
    tmp_path: pathlib.Path,
    logger: Logger,
) -> None:
    """When the cancel event is set before any chunk, TaskCancelledError is raised
    and the temp dir is removed."""
    bus = ProgressBus()
    bus.start(1, 'test-file', status='正在解析')

    # Pre-set the cancel event so the very first chunk check fires.
    cancel_event = bus.get_cancel_event(1)
    assert cancel_event is not None
    cancel_event.set()

    client = _FakeClient()
    ffmpeg = _FakeFFmpeg()
    settings = _settings(multi_downloading_segment=1)
    downloader = SegmentDownloader(client, settings, ffmpeg, bus, logger)

    temp_dir = tmp_path / 'temp_1'
    temp_dir.mkdir(parents=True, exist_ok=True)
    # Put a sentinel file in the temp dir to confirm cleanup happened.
    sentinel = temp_dir / 'sentinel.txt'
    sentinel.write_text('should be deleted')

    with pytest.raises(exceptions.TaskCancelledError):
        downloader.download(
            1,
            _m3u8_url(),
            tmp_path / 'out.mp4',
            temp_dir,
            temp_dir / 'merge.mp4',
            'out.mp4',
            't',
            realtime_show=False,
        )

    # temp dir should have been cleaned up.
    assert not temp_dir.exists()


def test_speed_calculation_with_fake_time(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Speed and ETA should be reported via update_stats when the sliding
    window has at least 1 second of data.

    Strategy: each of the 3 chunks is 512 KiB.  We monkeypatch
    ``time.monotonic`` in the segment_downloader module so successive calls
    advance the clock by 2 seconds each.  Because ``_download_all_chunks``
    runs with ``multi_downloading_segment=1`` (serial), the calls come from a
    single thread and the ordering is deterministic.  ``_REPORT_INTERVAL`` is
    patched to 0 so every chunk triggers an ``update_stats`` call.

    Note: the ThreadPoolExecutor worker thread shares the same module-level
    ``time`` reference that we patch, so the monkeypatch is visible inside
    the executor.
    """
    import threading
    import app.downloader.segment_downloader as sd_mod

    # 512 KiB per chunk; 3 chunks → 1.5 MiB total.
    chunk_size = 512 * 1024
    client = _FakeClient(chunk_data=b'X' * chunk_size)
    ffmpeg = _FakeFFmpeg(merging_file_size=1024 * 1024)

    # Thread-safe monotonic clock that advances by 2 seconds each call so that
    # by the time the second chunk completes the window spans > 1 second.
    _clock: list[float] = [100.0]
    _clock_lock = threading.Lock()

    def fake_monotonic() -> float:
        with _clock_lock:
            val = _clock[0]
            _clock[0] += 2.0
            return val

    # Record update_stats calls (called from the executor worker thread).
    stats_calls: list[dict[str, object]] = []
    _calls_lock = threading.Lock()
    orig_update_stats = progress.update_stats

    def recording_update_stats(
        sn: int,
        *,
        speed_mbps: float | None = None,
        eta_seconds: int | None = None,
        rate: float | None = None,
    ) -> None:
        with _calls_lock:
            stats_calls.append({'sn': sn, 'speed_mbps': speed_mbps, 'eta_seconds': eta_seconds, 'rate': rate})
        orig_update_stats(sn, speed_mbps=speed_mbps, eta_seconds=eta_seconds, rate=rate)

    monkeypatch.setattr(sd_mod, '_REPORT_INTERVAL', 0.0)
    monkeypatch.setattr(sd_mod.time, 'monotonic', fake_monotonic)
    monkeypatch.setattr(progress, 'update_stats', recording_update_stats)

    settings = _settings(multi_downloading_segment=1)  # serial → deterministic order
    downloader = SegmentDownloader(client, settings, ffmpeg, progress, logger)
    downloader.download(
        1,
        _m3u8_url(),
        tmp_path / 'out.mp4',
        tmp_path / 'temp',
        tmp_path / 'merge.mp4',
        'out.mp4',
        't',
        realtime_show=False,
    )

    # At least one call must have reported a non-None speed_mbps (window ≥ 1s).
    speed_calls = [c for c in stats_calls if c['speed_mbps'] is not None]
    assert len(speed_calls) >= 1, f'Expected update_stats with speed_mbps to be called; got {stats_calls}'
    # Speed should be positive (real MB/s from fake chunk bytes / fake elapsed).
    for call in speed_calls:
        assert isinstance(call['speed_mbps'], float)
        assert call['speed_mbps'] > 0.0


def test_cancel_mid_chunk_retry_raises_and_cleans_temp(
    tmp_path: pathlib.Path,
    logger: Logger,
) -> None:
    """Cancel event set between retry attempts inside _download_chunk_bytes raises
    TaskCancelledError and cleans up the temp dir.

    Strategy: configure a chunk that always fails so the retry loop runs
    multiple times. After the first failure, set the cancel event.
    The second iteration of the retry loop should detect the event and raise
    TaskCancelledError instead of TryTooManyTimeError.
    """
    import threading

    bus = ProgressBus()
    bus.start(1, 'test-file', status='正在解析')
    cancel_event = bus.get_cancel_event(1)
    assert cancel_event is not None

    fail_url = 'https://cdn.example.com/path/media_b_0.ts'

    call_count: list[int] = [0]
    call_lock = threading.Lock()

    class _CancelOnSecondAttemptClient(_FakeClient):
        def get(self, url: str, *, no_cookies: bool = False, **kwargs: object) -> '_FakeResponse':  # type: ignore[override]
            if url == fail_url:
                with call_lock:
                    call_count[0] += 1
                    count = call_count[0]
                if count == 1:
                    # First attempt fails; set cancel event so the next retry
                    # iteration will see it.
                    cancel_event.set()
                    raise ConnectionError('simulated fail')
            return super().get(url, no_cookies=no_cookies, **kwargs)

    client = _CancelOnSecondAttemptClient(fail_chunk_uris=set())
    ffmpeg = _FakeFFmpeg()
    settings = _settings(segment_max_retry=5, multi_downloading_segment=1)
    downloader = SegmentDownloader(client, settings, ffmpeg, bus, logger)

    temp_dir = tmp_path / 'temp_cancel_retry'
    temp_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(exceptions.TaskCancelledError):
        downloader.download(
            1,
            _m3u8_url(),
            tmp_path / 'out.mp4',
            temp_dir,
            temp_dir / 'merge.mp4',
            'out.mp4',
            't',
            realtime_show=False,
        )

    # The temp dir must have been cleaned up on cancel.
    assert not temp_dir.exists()


# ---------------------------------------------------------------------------
# feat(downloader): 正在移動檔案 status + log messages (segment path)
# ---------------------------------------------------------------------------


def test_segment_moving_file_status_before_replace(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``update_status(sn, '正在移動檔案')`` must be called before
    ``update_status(sn, '下載完成')`` in the segment-downloader path."""
    client = _FakeClient()
    ffmpeg = _FakeFFmpeg(merging_file_size=1 * 1024 * 1024)
    downloader = SegmentDownloader(client, _settings(), ffmpeg, progress, logger)

    status_sequence: list[str] = []
    orig_update_status = progress.update_status

    def _record(sn: int, status: str) -> None:
        status_sequence.append(status)
        orig_update_status(sn, status)

    monkeypatch.setattr(progress, 'update_status', _record)

    downloader.download(
        1,
        _m3u8_url(),
        tmp_path / 'out.mp4',
        tmp_path / 'temp',
        tmp_path / 'temp' / 'merge.mp4',
        'out.mp4',
        't',
        realtime_show=False,
    )

    assert '正在移動檔案' in status_sequence
    assert '下載完成' in status_sequence
    idx_moving = status_sequence.index('正在移動檔案')
    idx_done = status_sequence.index('下載完成')
    assert idx_moving < idx_done, (
        f"'正在移動檔案' ({idx_moving}) must precede '下載完成' ({idx_done})"
    )


def test_segment_moving_file_info_logs_emitted(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ``logger.info`` calls must appear around the merge-file rename:
    one 'from temp' message and one 'moved to' message."""
    client = _FakeClient()
    ffmpeg = _FakeFFmpeg(merging_file_size=512 * 1024)
    downloader = SegmentDownloader(client, _settings(), ffmpeg, progress, logger)

    info_messages: list[str] = []
    orig_info = logger.info

    def _capture(sn: int, tag: str, msg: str, **kwargs: object) -> None:
        info_messages.append(msg)
        orig_info(sn, tag, msg, **kwargs)

    monkeypatch.setattr(logger, 'info', _capture)

    downloader.download(
        1,
        _m3u8_url(),
        tmp_path / 'out.mp4',
        tmp_path / 'temp',
        tmp_path / 'temp' / 'merge.mp4',
        'out.mp4',
        't',
        realtime_show=False,
    )

    from_temp = [m for m in info_messages if '從 temp' in m]
    moved_to = [m for m in info_messages if '已移動到' in m]
    assert from_temp, f'Expected 從 temp log; got {info_messages}'
    assert moved_to, f'Expected 已移動到 log; got {info_messages}'

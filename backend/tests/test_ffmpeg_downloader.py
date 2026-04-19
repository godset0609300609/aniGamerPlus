"""Tests for ``FFmpegDownloader``."""

from __future__ import annotations

import io
import pathlib
import threading
from typing import Any

import pytest

from app.downloader import exceptions
from app.downloader.ffmpeg_downloader import FFmpegDownloader
from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.models import AppSettings


class _FakeFFmpegRunner:
    def __init__(self) -> None:
        self.path = pathlib.Path('/usr/bin/ffmpeg')

    def which(self) -> pathlib.Path:
        return self.path


class _FakePopen:
    """Stub enough of ``subprocess.Popen`` for the downloader to consume."""

    def __init__(
        self,
        *,
        stderr_lines: list[str],
        returncode: int = 0,
        produce_output_file: pathlib.Path | None = None,
        output_file_size: int = 1024,
    ) -> None:
        self.returncode_after_wait = returncode
        self.returncode: int | None = None
        self._stderr_lines = stderr_lines
        self.stderr = io.StringIO(''.join(stderr_lines))
        self.kill_called = False
        self._wait_event = threading.Event()
        self._produce_output_file = produce_output_file
        self._output_file_size = output_file_size
        self.init_captured: dict[str, Any] = {}

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        # Emulate ffmpeg finishing: write the output file, then set rc.
        if self._produce_output_file is not None:
            self._produce_output_file.parent.mkdir(parents=True, exist_ok=True)
            self._produce_output_file.write_bytes(b'\x00' * self._output_file_size)
        self.returncode = self.returncode_after_wait
        return self.returncode

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture
def progress() -> ProgressBus:
    bus = ProgressBus()
    bus.start(1, 'test-file', status='正在解析')
    return bus


def _settings(**overrides: Any) -> AppSettings:
    base: dict[str, Any] = {'ua': 'Mozilla/5.0'}
    base.update(overrides)
    return AppSettings(**base)


def test_happy_path_progress_50_percent(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_file = tmp_path / 'out.mp4'
    downloading_file = tmp_path / 'downloading.mp4'

    fake = _FakePopen(
        stderr_lines=['frame=1 fps=0 q=-1.0 size=0kB time=00:05:00.00 bitrate=N/A\n'],
        produce_output_file=downloading_file,
        output_file_size=2 * 1024 * 1024,
    )
    captured: dict[str, Any] = {}

    def fake_popen(cmd, *args, **kwargs):
        captured['cmd'] = cmd
        captured['kwargs'] = kwargs
        return fake

    monkeypatch.setattr('app.downloader.ffmpeg_downloader.subprocess.Popen', fake_popen)

    dl = FFmpegDownloader(_settings(), _FakeFFmpegRunner(), progress, logger)
    size = dl.download(
        1,
        'https://cdn.example.com/path/chunklist.m3u8',
        out_file,
        downloading_file,
        'out.mp4',
        't',
        total_duration_seconds=600.0,
        realtime_show=False,
    )
    assert size >= 1
    # Progress rate landed on 50%.
    entry = progress.snapshot()[1]
    assert entry.rate == pytest.approx(50.0)
    assert entry.status == '下載完成'
    # Confirm final file was renamed into place.
    assert out_file.exists()
    assert not downloading_file.exists()


def test_watchdog_kills_when_file_stagnates(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic: the watchdog kills on its first poll and ``proc.wait()``
    unblocks via an event set by ``kill()``. No real time.sleep anywhere."""
    downloading_file = tmp_path / 'downloading.mp4'
    out_file = tmp_path / 'out.mp4'

    # Empty file so the first poll sees ``size == last_size`` (no growth) and
    # falls through to the stall-threshold branch.
    downloading_file.parent.mkdir(parents=True, exist_ok=True)
    downloading_file.write_bytes(b'')

    # Stall threshold set to -1 so any "no growth" poll triggers kill
    # immediately. Removes all time-based ordering from the test.
    monkeypatch.setattr('app.downloader.ffmpeg_downloader._WATCHDOG_STALL_SECONDS', -1)

    # proc.wait() blocks on an internal event that kill() sets. This replaces
    # the earlier version's ``time.sleep(0.02)`` loop that raced the watchdog.
    class _StallingPopen(_FakePopen):
        def wait(self, timeout: float | None = None) -> int:
            # 5s is a safety cap so a buggy run can't hang the suite.
            self._wait_event.wait(timeout=5.0)
            return self.returncode if self.returncode is not None else -9

        def kill(self) -> None:
            super().kill()
            self._wait_event.set()

    fake = _StallingPopen(
        stderr_lines=[],
        returncode=-9,
        produce_output_file=None,
    )

    monkeypatch.setattr(
        'app.downloader.ffmpeg_downloader.subprocess.Popen',
        lambda *a, **kw: fake,
    )

    dl = FFmpegDownloader(_settings(), _FakeFFmpegRunner(), progress, logger)

    with pytest.raises(exceptions.TryTooManyTimeError):
        dl.download(
            1,
            'https://cdn/path.m3u8',
            out_file,
            downloading_file,
            'out.mp4',
            't',
            total_duration_seconds=None,
            realtime_show=False,
        )
    assert fake.kill_called is True


def test_nonzero_return_code_raises(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloading_file = tmp_path / 'downloading.mp4'
    fake = _FakePopen(
        stderr_lines=[],
        returncode=1,
        produce_output_file=None,
    )
    monkeypatch.setattr(
        'app.downloader.ffmpeg_downloader.subprocess.Popen',
        lambda *a, **kw: fake,
    )

    dl = FFmpegDownloader(_settings(), _FakeFFmpegRunner(), progress, logger)
    with pytest.raises(exceptions.TryTooManyTimeError):
        dl.download(
            1,
            'https://cdn/x.m3u8',
            tmp_path / 'out.mp4',
            downloading_file,
            'out.mp4',
            't',
            total_duration_seconds=None,
            realtime_show=False,
        )


def test_renames_downloading_to_output(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloading_file = tmp_path / 'downloading.mp4'
    out_file = tmp_path / 'out.mp4'

    fake = _FakePopen(
        stderr_lines=[],
        returncode=0,
        produce_output_file=downloading_file,
        output_file_size=3 * 1024 * 1024,
    )
    monkeypatch.setattr(
        'app.downloader.ffmpeg_downloader.subprocess.Popen',
        lambda *a, **kw: fake,
    )

    dl = FFmpegDownloader(_settings(), _FakeFFmpegRunner(), progress, logger)
    size = dl.download(
        1,
        'https://cdn/x.m3u8',
        out_file,
        downloading_file,
        'out.mp4',
        't',
        total_duration_seconds=None,
        realtime_show=False,
    )
    assert out_file.exists()
    assert not downloading_file.exists()
    assert size == 3


def test_popen_decodes_utf8_stderr_on_cp950_locale(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: on Windows-TW (cp950) locale, ``subprocess.Popen(text=True)``
    without an explicit ``encoding=`` decodes ffmpeg's UTF-8 stderr with cp950
    and blows up with ``UnicodeDecodeError``. ``FFmpegDownloader.download``
    must pin ``encoding="utf-8"`` + ``errors="replace"`` for Popen too.
    """
    downloading_file = tmp_path / 'downloading.mp4'
    captured: dict[str, Any] = {}

    fake = _FakePopen(
        stderr_lines=[],
        returncode=0,
        produce_output_file=downloading_file,
        output_file_size=1024,
    )

    def fake_popen(cmd, *args, **kwargs):
        captured['cmd'] = cmd
        captured['kwargs'] = kwargs
        return fake

    monkeypatch.setattr('app.downloader.ffmpeg_downloader.subprocess.Popen', fake_popen)

    dl = FFmpegDownloader(_settings(), _FakeFFmpegRunner(), progress, logger)
    dl.download(
        1,
        'https://cdn/x.m3u8',
        tmp_path / 'out.mp4',
        downloading_file,
        'out.mp4',
        't',
        total_duration_seconds=None,
        realtime_show=False,
    )

    kwargs = captured['kwargs']
    assert kwargs.get('encoding') == 'utf-8'
    assert kwargs.get('errors') == 'replace'
    # text=True stays on — encoding/errors only take effect in text mode.
    assert kwargs.get('text') is True


def test_eta_reported_from_ffmpeg_time_and_bitrate(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stderr line containing ``bitrate=`` should cause ``update_stats``
    to be called with a non-None ``speed_mbps`` derived from that bitrate
    field.  The rate field must also be updated from ``time=`` when
    ``total_duration_seconds`` is provided.

    ``_REPORT_INTERVAL`` is patched to 0 so the first matching line
    immediately triggers a throttle-pass and calls ``update_stats``.
    """
    import app.downloader.ffmpeg_downloader as ffd_mod

    # Patch _REPORT_INTERVAL to 0 so the first matching line triggers a call.
    monkeypatch.setattr(ffd_mod, '_REPORT_INTERVAL', 0.0)

    downloading_file = tmp_path / 'downloading.mp4'

    # stderr line: 2 min 30 s elapsed (25 %), 2500 kbits/s, total = 600 s.
    stderr_line = 'frame=100 fps=24 q=-1.0 size=  4096kB time=00:02:30.00 bitrate=2500.0kbits/s speed=1.0x\n'

    fake = _FakePopen(
        stderr_lines=[stderr_line],
        produce_output_file=downloading_file,
        output_file_size=2 * 1024 * 1024,
    )
    monkeypatch.setattr(
        'app.downloader.ffmpeg_downloader.subprocess.Popen',
        lambda *a, **kw: fake,
    )

    stats_calls: list[dict[str, object]] = []
    orig_update_stats = progress.update_stats

    def recording_update_stats(
        sn: int,
        *,
        speed_mbps: float | None = None,
        eta_seconds: int | None = None,
    ) -> None:
        stats_calls.append({'sn': sn, 'speed_mbps': speed_mbps, 'eta_seconds': eta_seconds})
        orig_update_stats(sn, speed_mbps=speed_mbps, eta_seconds=eta_seconds)

    monkeypatch.setattr(progress, 'update_stats', recording_update_stats)

    dl = FFmpegDownloader(_settings(), _FakeFFmpegRunner(), progress, logger)
    dl.download(
        1,
        'https://cdn.example.com/path/chunklist.m3u8',
        tmp_path / 'out.mp4',
        downloading_file,
        'out.mp4',
        't',
        total_duration_seconds=600.0,
        realtime_show=False,
    )

    # The progress rate must be set from the time= parse (150/600 = 25%).
    snap = progress.snapshot()
    assert 1 in snap
    assert snap[1].rate == pytest.approx(25.0)

    # Must have at least one update_stats call with non-None speed_mbps.
    speed_calls = [c for c in stats_calls if c['speed_mbps'] is not None]
    assert speed_calls, f'Expected update_stats with speed_mbps; got {stats_calls}'

    call = speed_calls[0]
    # bitrate=2500 kbits/s → 2500 / 8 / 1024 ≈ 0.305 MB/s
    assert isinstance(call['speed_mbps'], float)
    assert call['speed_mbps'] == pytest.approx(2500.0 / 8.0 / 1024.0, rel=1e-3)

    # eta_seconds must be filled (may be 0 if wall-clock elapsed is tiny).
    # The key invariant: update_stats was called with a non-None eta_seconds.
    # With real wall-clock, wall_elapsed is always > 0, so eta_seconds is set.
    eta_calls = [c for c in stats_calls if c.get('eta_seconds') is not None]
    assert eta_calls, f'Expected update_stats with eta_seconds filled; got {stats_calls}'
    assert isinstance(eta_calls[0]['eta_seconds'], int)
    assert eta_calls[0]['eta_seconds'] >= 0


# ---------------------------------------------------------------------------
# Batch H — cancel tests
# ---------------------------------------------------------------------------


def test_cancel_terminates_ffmpeg_and_cleans_partial(
    tmp_path: pathlib.Path,
    logger: Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the cancel event fires, the watchdog terminates the process and
    the downloading_file partial is removed; TaskCancelledError is raised."""
    import threading as _threading
    import time as _time
    from app.downloader.progress import ProgressBus

    # Use a real ProgressBus so get_cancel_event works.
    bus = ProgressBus()
    bus.start(1, 'test-file', status='正在解析')
    cancel_event = bus.get_cancel_event(1)
    assert cancel_event is not None

    downloading_file = tmp_path / 'downloading.mp4'

    # FakePopen that blocks in wait() until kill/terminate is called.
    class _CancelPopen(_FakePopen):
        def __init__(self) -> None:
            super().__init__(
                stderr_lines=[],
                returncode=0,
                produce_output_file=None,
            )
            self.terminated = False

        def wait(self, timeout: float | None = None) -> int:
            # Block until terminate/kill sets returncode.
            self._wait_event.wait(timeout=5.0)
            return self.returncode if self.returncode is not None else -15

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15
            self._wait_event.set()

        def kill(self) -> None:
            super().kill()
            self._wait_event.set()

    fake = _CancelPopen()
    monkeypatch.setattr(
        'app.downloader.ffmpeg_downloader.subprocess.Popen',
        lambda *a, **kw: fake,
    )

    # Fire the cancel event shortly after download starts.
    def _fire_cancel() -> None:
        _time.sleep(0.05)
        cancel_event.set()

    _threading.Thread(target=_fire_cancel, daemon=True).start()

    dl = FFmpegDownloader(_settings(), _FakeFFmpegRunner(), bus, logger)
    with pytest.raises(exceptions.TaskCancelledError):
        dl.download(
            1,
            'https://cdn/x.m3u8',
            tmp_path / 'out.mp4',
            downloading_file,
            'out.mp4',
            't',
            total_duration_seconds=None,
            realtime_show=False,
        )

    # The partial downloading file must have been cleaned up.
    assert not downloading_file.exists()
    # Process was terminated (or killed).
    assert fake.terminated or fake.kill_called


def test_popen_never_uses_shell_true(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloading_file = tmp_path / 'downloading.mp4'
    captured: dict[str, Any] = {}

    fake = _FakePopen(
        stderr_lines=[],
        returncode=0,
        produce_output_file=downloading_file,
        output_file_size=1024,
    )

    def fake_popen(cmd, *args, **kwargs):
        captured['cmd'] = cmd
        captured['kwargs'] = kwargs
        return fake

    monkeypatch.setattr('app.downloader.ffmpeg_downloader.subprocess.Popen', fake_popen)

    dl = FFmpegDownloader(_settings(), _FakeFFmpegRunner(), progress, logger)
    dl.download(
        1,
        'https://cdn/x.m3u8',
        tmp_path / 'out.mp4',
        downloading_file,
        'out.mp4',
        't',
        total_duration_seconds=None,
        realtime_show=False,
    )

    assert isinstance(captured['cmd'], list)
    assert captured['kwargs'].get('shell', False) is False


# ---------------------------------------------------------------------------
# feat(downloader): 正在移動檔案 status + log messages
# ---------------------------------------------------------------------------


def test_moving_file_status_set_before_replace(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``update_status(sn, '正在移動檔案')`` must be called before the file rename
    completes, and ``update_status(sn, '下載完成')`` must be called after."""
    downloading_file = tmp_path / 'downloading.mp4'
    out_file = tmp_path / 'out.mp4'

    fake = _FakePopen(
        stderr_lines=[],
        returncode=0,
        produce_output_file=downloading_file,
        output_file_size=1 * 1024 * 1024,
    )
    monkeypatch.setattr(
        'app.downloader.ffmpeg_downloader.subprocess.Popen',
        lambda *a, **kw: fake,
    )

    status_sequence: list[str] = []

    orig_update_status = progress.update_status

    def _record_status(sn: int, status: str) -> None:
        status_sequence.append(status)
        orig_update_status(sn, status)

    monkeypatch.setattr(progress, 'update_status', _record_status)

    dl = FFmpegDownloader(_settings(), _FakeFFmpegRunner(), progress, logger)
    dl.download(
        1,
        'https://cdn/x.m3u8',
        out_file,
        downloading_file,
        'out.mp4',
        't',
        total_duration_seconds=None,
        realtime_show=False,
    )

    # '正在移動檔案' must appear before '下載完成' in the sequence.
    assert '正在移動檔案' in status_sequence
    assert '下載完成' in status_sequence
    idx_moving = status_sequence.index('正在移動檔案')
    idx_done = status_sequence.index('下載完成')
    assert idx_moving < idx_done, (
        f"'正在移動檔案' ({idx_moving}) must come before '下載完成' ({idx_done})"
    )


def test_moving_file_info_logs_emitted(
    tmp_path: pathlib.Path,
    logger: Logger,
    progress: ProgressBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ``logger.info`` calls should be emitted around the file rename:
    one 'from temp' message and one 'moved to' message."""
    downloading_file = tmp_path / 'downloading.mp4'
    out_file = tmp_path / 'out.mp4'

    fake = _FakePopen(
        stderr_lines=[],
        returncode=0,
        produce_output_file=downloading_file,
        output_file_size=512 * 1024,
    )
    monkeypatch.setattr(
        'app.downloader.ffmpeg_downloader.subprocess.Popen',
        lambda *a, **kw: fake,
    )

    info_messages: list[str] = []
    orig_info = logger.info

    def _capture_info(sn: int, tag: str, msg: str, **kwargs: object) -> None:
        info_messages.append(msg)
        orig_info(sn, tag, msg, **kwargs)

    monkeypatch.setattr(logger, 'info', _capture_info)

    dl = FFmpegDownloader(_settings(), _FakeFFmpegRunner(), progress, logger)
    dl.download(
        1,
        'https://cdn/x.m3u8',
        out_file,
        downloading_file,
        'out.mp4',
        't',
        total_duration_seconds=None,
        realtime_show=False,
    )

    # One "from temp" log and one "moved to" log must appear.
    from_temp = [m for m in info_messages if '從 temp' in m]
    moved_to = [m for m in info_messages if '已移動到' in m]
    assert from_temp, f'Expected 從 temp log; got {info_messages}'
    assert moved_to, f'Expected 已移動到 log; got {info_messages}'

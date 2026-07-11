"""Tests for yt-dlp progress hook 250ms throttle logic."""

from __future__ import annotations

import pathlib
import time
import typing as T
import unittest.mock

import pytest
import yt_dlp

from app.downloader.bilibili.ytdlp_downloader import YtdlpDownloader, _THROTTLE_INTERVAL, _sanitize_title
from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.persistence.paths import WorkspacePaths


@pytest.fixture
def paths(tmp_path: pathlib.Path) -> WorkspacePaths:
    return WorkspacePaths.detect(working_dir=tmp_path)


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


class FakeBilibiliCookieRepo:
    def exists_and_nonempty(self) -> bool:
        return False

    @property
    def path(self) -> pathlib.Path:
        return pathlib.Path('/dev/null')


def _make_downloader(progress_bus: ProgressBus, logger: Logger, tmp_path: pathlib.Path) -> YtdlpDownloader:
    return YtdlpDownloader(
        progress_bus=progress_bus,
        cookie_repo=FakeBilibiliCookieRepo(),  # type: ignore[arg-type]
        bangumi_dir=tmp_path,
        logger=logger,
    )


def test_throttle_interval_constant() -> None:
    assert _THROTTLE_INTERVAL == 0.25


def test_progress_hook_throttles_updates(tmp_path: pathlib.Path, logger: Logger) -> None:
    progress_bus = ProgressBus()
    progress_bus.start(1, 'bvid')

    update_rate_calls: list[float] = []
    original_update_rate = progress_bus.update_rate

    def tracking_update_rate(sn: int, rate: float) -> None:
        update_rate_calls.append(rate)
        original_update_rate(sn, rate)

    downloader = _make_downloader(progress_bus, logger, tmp_path)

    fake_times = [0.0, 0.1, 0.2, 0.26, 0.3, 0.52]
    time_iter = iter(fake_times)
    last_update: dict[str, float] = {'t': 0.0}

    def fake_monotonic() -> float:
        return next(time_iter)

    with unittest.mock.patch.object(progress_bus, 'update_rate', side_effect=tracking_update_rate):
        with unittest.mock.patch('app.downloader.bilibili.ytdlp_downloader.time.monotonic', side_effect=fake_monotonic):
            hook_calls = [
                {'status': 'downloading', 'downloaded_bytes': 100, 'total_bytes': 1000},
                {'status': 'downloading', 'downloaded_bytes': 200, 'total_bytes': 1000},
                {'status': 'downloading', 'downloaded_bytes': 300, 'total_bytes': 1000},
                {'status': 'downloading', 'downloaded_bytes': 400, 'total_bytes': 1000},
                {'status': 'downloading', 'downloaded_bytes': 500, 'total_bytes': 1000},
                {'status': 'downloading', 'downloaded_bytes': 600, 'total_bytes': 1000},
            ]

            with unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls:
                mock_ydl_instance = unittest.mock.MagicMock()
                mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
                mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)

                captured_hooks: list[T.Callable] = []

                def capture_opts(opts: dict[str, T.Any]) -> None:
                    hooks = opts.get('progress_hooks', [])
                    captured_hooks.extend(hooks)

                original_init = mock_ydl_cls.side_effect

                def patched_init(opts: dict[str, T.Any]) -> T.Any:
                    capture_opts(opts)
                    return mock_ydl_cls.return_value

                mock_ydl_cls.side_effect = patched_init
                mock_ydl_instance.extract_info.return_value = None

                try:
                    downloader.download(1, 'BV1xx411c7mD', resolution='1080', classify=True)
                except Exception:
                    pass

                if captured_hooks:
                    hook = captured_hooks[0]
                    for call in hook_calls:
                        hook(call)

    assert len(update_rate_calls) < len(hook_calls)


def test_cancel_raises_download_cancelled(tmp_path: pathlib.Path, logger: Logger) -> None:
    import yt_dlp.utils

    progress_bus = ProgressBus()
    progress_bus.start(2, 'bvid')
    cancel_event = progress_bus.get_cancel_event(2)
    assert cancel_event is not None
    cancel_event.set()

    downloader = _make_downloader(progress_bus, logger, tmp_path)

    with unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls:
        mock_ydl_instance = unittest.mock.MagicMock()
        mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)
        captured_hooks: list[T.Callable] = []

        def patched_init(opts: dict[str, T.Any]) -> T.Any:
            hooks = opts.get('progress_hooks', [])
            captured_hooks.extend(hooks)
            return mock_ydl_cls.return_value

        mock_ydl_cls.side_effect = patched_init
        mock_ydl_instance.extract_info.return_value = None

        try:
            downloader.download(2, 'BV1xx411c7mD', resolution='1080', classify=True)
        except Exception:
            pass

        if captured_hooks:
            hook = captured_hooks[0]
            with pytest.raises(yt_dlp.utils.DownloadCancelled):
                hook({'status': 'downloading', 'downloaded_bytes': 100, 'total_bytes': 1000})


# ---------------------------------------------------------------------------
# Sanitizer tests
# ---------------------------------------------------------------------------


def test_sanitize_title_preserves_spaces() -> None:
    assert _sanitize_title('FF14 - p01 FF14 7.0') == 'FF14 - p01 FF14 7.0'


def test_sanitize_title_replaces_illegal_chars() -> None:
    assert _sanitize_title('Bad?File|Name') == 'Bad_File_Name'


def test_sanitize_title_strips_trailing_dot() -> None:
    assert _sanitize_title('trailing dot.') == 'trailing dot'


def test_sanitize_title_strips_trailing_space() -> None:
    assert _sanitize_title('trailing space ') == 'trailing space'


def test_sanitize_title_preserves_cjk() -> None:
    assert _sanitize_title('【FF14】佐拉加歼殛战') == '【FF14】佐拉加歼殛战'


# ---------------------------------------------------------------------------
# outtmpl / _base_opts tests
# ---------------------------------------------------------------------------


def test_base_opts_restrictfilenames_false(tmp_path: pathlib.Path, logger: Logger) -> None:
    progress_bus = ProgressBus()
    downloader = _make_downloader(progress_bus, logger, tmp_path)
    opts = downloader._base_opts()
    assert opts.get('restrictfilenames') is False


def test_base_opts_has_retry_config(tmp_path: pathlib.Path, logger: Logger) -> None:
    progress_bus = ProgressBus()
    downloader = _make_downloader(progress_bus, logger, tmp_path)
    opts = downloader._base_opts()
    assert opts['retries'] >= 10
    assert opts['fragment_retries'] >= 10
    assert opts['socket_timeout'] > 0


def test_base_opts_retry_sleep_exponential_capped(tmp_path: pathlib.Path, logger: Logger) -> None:
    progress_bus = ProgressBus()
    downloader = _make_downloader(progress_bus, logger, tmp_path)
    opts = downloader._base_opts()
    http_sleep = opts['retry_sleep_functions']['http']

    values = [http_sleep(n) for n in range(1, 11)]
    for prev, curr in zip(values, values[1:]):
        assert curr >= prev
    assert max(values) <= 30


def test_base_opts_retry_sleep_symmetric_http_and_fragment(tmp_path: pathlib.Path, logger: Logger) -> None:
    progress_bus = ProgressBus()
    downloader = _make_downloader(progress_bus, logger, tmp_path)
    opts = downloader._base_opts()
    http_sleep = opts['retry_sleep_functions']['http']
    fragment_sleep = opts['retry_sleep_functions']['fragment']

    for n in range(1, 11):
        assert http_sleep(n) == fragment_sleep(n)


def _capture_opts(tmp_path: pathlib.Path, logger: Logger, task_sn: int = 3) -> dict[str, T.Any]:
    progress_bus = ProgressBus()
    downloader = _make_downloader(progress_bus, logger, tmp_path)

    captured_opts: list[dict[str, T.Any]] = []

    with unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls:
        mock_ydl_instance = unittest.mock.MagicMock()
        mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)

        def patched_init(opts: dict[str, T.Any]) -> T.Any:
            captured_opts.append(opts)
            return mock_ydl_cls.return_value

        mock_ydl_cls.side_effect = patched_init
        mock_ydl_instance.extract_info.return_value = None

        try:
            downloader.download(task_sn, 'BV1xx411c7mD', resolution='1080', classify=True)
        except Exception:
            pass

    assert captured_opts, 'YoutubeDL was never instantiated'
    return captured_opts[0]


def test_outtmpl_is_string_in_dict(tmp_path: pathlib.Path, logger: Logger) -> None:
    opts = _capture_opts(tmp_path, logger)
    outtmpl = opts.get('outtmpl', {})
    assert isinstance(outtmpl, dict), 'outtmpl should be a dict with a "default" key'
    assert isinstance(outtmpl.get('default'), str), 'outtmpl["default"] must be a string'


def test_outtmpl_renders_single_part_via_ytdlp(tmp_path: pathlib.Path, logger: Logger) -> None:
    opts = _capture_opts(tmp_path, logger)
    info = {'title': 'Foo Bar', 'ext': 'mp4'}
    with yt_dlp.YoutubeDL(opts) as ydl:
        filename = ydl.prepare_filename(info)
    assert filename.endswith('Foo Bar》.mp4'), filename


def test_outtmpl_renders_multipart_p1_via_ytdlp(tmp_path: pathlib.Path, logger: Logger) -> None:
    opts = _capture_opts(tmp_path, logger)
    info = {'title': 'P1 sub-title', 'playlist_title': 'Parent BV Title', 'playlist_index': 1, 'ext': 'mp4'}
    with yt_dlp.YoutubeDL(opts) as ydl:
        filename = ydl.prepare_filename(info)
    assert filename.endswith('Parent BV Title》 - p1.mp4'), filename


def test_outtmpl_renders_multipart_p2_via_ytdlp(tmp_path: pathlib.Path, logger: Logger) -> None:
    opts = _capture_opts(tmp_path, logger)
    info = {'title': 'P2 sub-title', 'playlist_title': 'Parent BV Title', 'playlist_index': 2, 'ext': 'mp4'}
    with yt_dlp.YoutubeDL(opts) as ydl:
        filename = ydl.prepare_filename(info)
    assert filename.endswith('Parent BV Title》 - p2.mp4'), filename


def test_outtmpl_no_backref_or_homoglyph_leakage(tmp_path: pathlib.Path, logger: Logger) -> None:
    opts = _capture_opts(tmp_path, logger)
    info = {'title': 'Parent BV Title', 'playlist_title': 'Parent BV Title', 'playlist_index': 1, 'ext': 'mp4'}
    with yt_dlp.YoutubeDL(opts) as ydl:
        filename = ydl.prepare_filename(info)
    assert '\\1' not in filename
    assert '⧹' not in filename
    assert 'NA' not in filename


def _capture_hook(tmp_path: pathlib.Path, logger: Logger, task_sn: int = 10) -> T.Callable:
    progress_bus = ProgressBus()
    progress_bus.start(task_sn, 'bvid')
    downloader = _make_downloader(progress_bus, logger, tmp_path)

    with unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls:
        mock_ydl_instance = unittest.mock.MagicMock()
        mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)

        captured_hooks: list[T.Callable] = []

        def patched_init(opts: dict[str, T.Any]) -> T.Any:
            captured_hooks.extend(opts.get('progress_hooks', []))
            return mock_ydl_cls.return_value

        mock_ydl_cls.side_effect = patched_init
        mock_ydl_instance.extract_info.return_value = None

        try:
            downloader.download(task_sn, 'BV1xx411c7mD', resolution='1080', classify=True)
        except Exception:
            pass

    assert captured_hooks
    return captured_hooks[0]


def test_progress_hook_finished_sets_rate_100(tmp_path: pathlib.Path, logger: Logger) -> None:
    progress_bus = ProgressBus()
    progress_bus.start(10, 'bvid')
    downloader = _make_downloader(progress_bus, logger, tmp_path)

    with unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls:
        mock_ydl_instance = unittest.mock.MagicMock()
        mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)

        captured_hooks: list[T.Callable] = []

        def patched_init(opts: dict[str, T.Any]) -> T.Any:
            captured_hooks.extend(opts.get('progress_hooks', []))
            return mock_ydl_cls.return_value

        mock_ydl_cls.side_effect = patched_init
        mock_ydl_instance.extract_info.return_value = None

        try:
            downloader.download(10, 'BV1xx411c7mD', resolution='1080', classify=True)
        except Exception:
            pass

    assert captured_hooks
    hook = captured_hooks[0]

    with (
        unittest.mock.patch.object(progress_bus, 'update_rate') as mock_rate,
        unittest.mock.patch.object(progress_bus, 'update_stats') as mock_stats,
    ):
        hook({'status': 'finished', 'filename': 'test.mp4', 'info_dict': {}})

    mock_rate.assert_called_once_with(10, 100.0)
    mock_stats.assert_called_once_with(10, speed_mbps=None, eta_seconds=None)


def test_progress_hook_downloading_rate_uses_0_100_scale(tmp_path: pathlib.Path, logger: Logger) -> None:
    progress_bus = ProgressBus()
    progress_bus.start(11, 'bvid')
    downloader = _make_downloader(progress_bus, logger, tmp_path)

    with unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls:
        mock_ydl_instance = unittest.mock.MagicMock()
        mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)

        captured_hooks: list[T.Callable] = []

        def patched_init(opts: dict[str, T.Any]) -> T.Any:
            captured_hooks.extend(opts.get('progress_hooks', []))
            return mock_ydl_cls.return_value

        mock_ydl_cls.side_effect = patched_init
        mock_ydl_instance.extract_info.return_value = None

        try:
            downloader.download(11, 'BV1xx411c7mD', resolution='1080', classify=True)
        except Exception:
            pass

    assert captured_hooks
    hook = captured_hooks[0]

    with unittest.mock.patch.object(progress_bus, 'update_rate') as mock_rate:
        hook({'status': 'downloading', 'downloaded_bytes': 50, 'total_bytes': 100})

    mock_rate.assert_called_once_with(11, 50.0)


def test_progress_hook_downloading_rate_not_fractional(tmp_path: pathlib.Path, logger: Logger) -> None:
    progress_bus = ProgressBus()
    progress_bus.start(12, 'bvid')
    downloader = _make_downloader(progress_bus, logger, tmp_path)

    with unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls:
        mock_ydl_instance = unittest.mock.MagicMock()
        mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)

        captured_hooks: list[T.Callable] = []

        def patched_init(opts: dict[str, T.Any]) -> T.Any:
            captured_hooks.extend(opts.get('progress_hooks', []))
            return mock_ydl_cls.return_value

        mock_ydl_cls.side_effect = patched_init
        mock_ydl_instance.extract_info.return_value = None

        try:
            downloader.download(12, 'BV1xx411c7mD', resolution='1080', classify=True)
        except Exception:
            pass

    assert captured_hooks
    hook = captured_hooks[0]

    with unittest.mock.patch.object(progress_bus, 'update_rate') as mock_rate:
        hook({'status': 'downloading', 'downloaded_bytes': 33, 'total_bytes': 100})

    _, called_rate = mock_rate.call_args.args
    assert called_rate > 1.0, f'rate {called_rate} is in 0..1 scale; expected 0..100'


# ---------------------------------------------------------------------------
# Helpers shared by status-string / postprocessor tests
# ---------------------------------------------------------------------------


def _capture_both_hooks(
    tmp_path: pathlib.Path,
    logger: Logger,
    task_sn: int,
) -> tuple[T.Callable, T.Callable, ProgressBus]:
    """Return (progress_hook, postprocessor_hook, progress_bus) captured from download()."""
    progress_bus = ProgressBus()
    progress_bus.start(task_sn, 'bvid')
    downloader = _make_downloader(progress_bus, logger, tmp_path)

    captured_progress: list[T.Callable] = []
    captured_pp: list[T.Callable] = []

    with unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls:
        mock_ydl_instance = unittest.mock.MagicMock()
        mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)

        def patched_init(opts: dict[str, T.Any]) -> T.Any:
            captured_progress.extend(opts.get('progress_hooks', []))
            captured_pp.extend(opts.get('postprocessor_hooks', []))
            return mock_ydl_cls.return_value

        mock_ydl_cls.side_effect = patched_init
        mock_ydl_instance.extract_info.return_value = None

        try:
            downloader.download(task_sn, 'BV1xx411c7mD', resolution='1080', classify=True)
        except Exception:
            pass

    assert captured_progress, 'progress_hooks not captured'
    assert captured_pp, 'postprocessor_hooks not captured'
    return captured_progress[0], captured_pp[0], progress_bus


# ---------------------------------------------------------------------------
# Status-string consistency tests
# ---------------------------------------------------------------------------


def test_progress_hook_downloading_uses_正在下載(tmp_path: pathlib.Path, logger: Logger) -> None:
    hook, _, progress_bus = _capture_both_hooks(tmp_path, logger, task_sn=20)

    with unittest.mock.patch.object(progress_bus, 'update_status') as mock_status:
        hook({'status': 'downloading', 'downloaded_bytes': 50, 'total_bytes': 100})

    mock_status.assert_called_once_with(20, '正在下載')


def test_postprocessor_hook_started_sets_正在合併(tmp_path: pathlib.Path, logger: Logger) -> None:
    _, pp_hook, progress_bus = _capture_both_hooks(tmp_path, logger, task_sn=21)

    with unittest.mock.patch.object(progress_bus, 'update_status') as mock_status:
        pp_hook({'status': 'started', 'postprocessor': 'FFmpegMerger'})

    mock_status.assert_called_once_with(21, '正在合併')


def test_postprocessor_hook_finished_sets_下載完成(tmp_path: pathlib.Path, logger: Logger) -> None:
    _, pp_hook, progress_bus = _capture_both_hooks(tmp_path, logger, task_sn=22)

    with unittest.mock.patch.object(progress_bus, 'update_status') as mock_status:
        pp_hook({'status': 'finished', 'postprocessor': 'FFmpegMerger'})

    mock_status.assert_called_once_with(22, '下載完成')


def test_progress_hook_finished_no_longer_sets_下載完成(tmp_path: pathlib.Path, logger: Logger) -> None:
    hook, _, progress_bus = _capture_both_hooks(tmp_path, logger, task_sn=23)

    status_calls: list[tuple[T.Any, ...]] = []
    with unittest.mock.patch.object(progress_bus, 'update_status', side_effect=lambda *a: status_calls.append(a)):
        hook({'status': 'finished', 'filename': 'test.mp4', 'info_dict': {}})

    for call_args in status_calls:
        assert call_args != (23, '下載完成'), 'progress_hook finished branch must not set 下載完成 directly'


def test_download_no_postprocessor_still_sets_下載完成(tmp_path: pathlib.Path, logger: Logger) -> None:
    task_sn = 24
    progress_bus = ProgressBus()
    progress_bus.start(task_sn, 'bvid')
    downloader = _make_downloader(progress_bus, logger, tmp_path)

    status_calls: list[tuple[T.Any, ...]] = []
    original_update_status = progress_bus.update_status

    def tracking_status(sn: int, status: str) -> None:
        status_calls.append((sn, status))
        original_update_status(sn, status)

    with unittest.mock.patch.object(progress_bus, 'update_status', side_effect=tracking_status):
        with unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls:
            mock_ydl_instance = unittest.mock.MagicMock()
            mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
            mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)

            def patched_init(opts: dict[str, T.Any]) -> T.Any:
                return mock_ydl_cls.return_value

            mock_ydl_cls.side_effect = patched_init
            mock_ydl_instance.extract_info.return_value = None

            downloader.download(task_sn, 'BV1xx411c7mD', resolution='1080', classify=True)

    assert (task_sn, '下載完成') in status_calls, f'Expected 下載完成 in status calls; got {status_calls}'


def test_postprocessor_hook_registered_in_opts(tmp_path: pathlib.Path, logger: Logger) -> None:
    task_sn = 25
    opts = _capture_opts(tmp_path, logger, task_sn=task_sn)
    pp_hooks = opts.get('postprocessor_hooks', [])
    assert len(pp_hooks) == 1, f'Expected 1 postprocessor_hook, got {len(pp_hooks)}'
    assert callable(pp_hooks[0])


# ---------------------------------------------------------------------------
# Aggregated multi-stream progress tests (DASH: video + audio)
# ---------------------------------------------------------------------------


def _capture_hook_with_bus(
    tmp_path: pathlib.Path,
    logger: Logger,
    task_sn: int,
) -> tuple[T.Callable, ProgressBus]:
    """Return (progress_hook, progress_bus) with throttle bypassed via fake time."""
    progress_bus = ProgressBus()
    progress_bus.start(task_sn, 'bvid')
    downloader = _make_downloader(progress_bus, logger, tmp_path)

    time_counter = [0.0]

    def fake_monotonic() -> float:
        v = time_counter[0]
        time_counter[0] += _THROTTLE_INTERVAL + 0.01
        return v

    captured_hooks: list[T.Callable] = []

    with unittest.mock.patch('app.downloader.bilibili.ytdlp_downloader.time.monotonic', side_effect=fake_monotonic):
        with unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls:
            mock_ydl_instance = unittest.mock.MagicMock()
            mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
            mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)

            def patched_init(opts: dict[str, T.Any]) -> T.Any:
                captured_hooks.extend(opts.get('progress_hooks', []))
                return mock_ydl_cls.return_value

            mock_ydl_cls.side_effect = patched_init
            mock_ydl_instance.extract_info.return_value = None

            try:
                downloader.download(task_sn, 'BV1xx411c7mD', resolution='1080', classify=True)
            except Exception:
                pass

    assert captured_hooks
    return captured_hooks[0], progress_bus


def test_progress_aggregation_two_streams_video_then_audio(
    tmp_path: pathlib.Path, logger: Logger
) -> None:
    """Rate is monotonically non-decreasing across a DASH video+audio sequence."""
    task_sn = 30
    hook, progress_bus = _capture_hook_with_bus(tmp_path, logger, task_sn)

    rates: list[float] = []
    original_update_rate = progress_bus.update_rate

    def tracking_rate(sn: int, rate: float) -> None:
        rates.append(rate)
        original_update_rate(sn, rate)

    # Start time counter past the throttle interval so the first hook call is not suppressed.
    time_counter = [_THROTTLE_INTERVAL + 0.01]

    def fake_monotonic() -> float:
        v = time_counter[0]
        time_counter[0] += _THROTTLE_INTERVAL + 0.01
        return v

    with (
        unittest.mock.patch.object(progress_bus, 'update_rate', side_effect=tracking_rate),
        unittest.mock.patch('app.downloader.bilibili.ytdlp_downloader.time.monotonic', side_effect=fake_monotonic),
    ):
        hook({'status': 'downloading', 'filename': 'video.mp4', 'downloaded_bytes': 500, 'total_bytes': 1000})
        hook({'status': 'finished', 'filename': 'video.mp4', 'total_bytes': 1000})
        hook({'status': 'downloading', 'filename': 'audio.m4a', 'downloaded_bytes': 0, 'total_bytes': 500})
        hook({'status': 'downloading', 'filename': 'audio.m4a', 'downloaded_bytes': 250, 'total_bytes': 500})

    downloading_rates = [r for r in rates if r < 100.0]
    if downloading_rates:
        for i in range(1, len(downloading_rates)):
            assert downloading_rates[i] >= downloading_rates[i - 1], (
                f'Rate decreased: {downloading_rates[i - 1]} → {downloading_rates[i]}'
            )

    assert 100.0 in rates

    final_downloading = next((r for r in reversed(rates) if r < 100.0), None)
    if final_downloading is not None:
        expected = round((1000 + 250) / (1000 + 500) * 100.0, 2)
        assert abs(final_downloading - expected) < 0.1, (
            f'Expected final downloading rate ~{expected}, got {final_downloading}'
        )


def test_progress_aggregation_no_reset_on_new_file(
    tmp_path: pathlib.Path, logger: Logger
) -> None:
    """When a second file starts at 0 bytes, the aggregate rate must not reset to near-zero.

    With video at 80% complete (800/1000) and audio starting (0/500), the
    aggregate is (800+0)/(1000+500) = 53.3% — well above 0.  Without
    aggregation the rate would reset to 0/500 = 0%.
    """
    task_sn = 31
    hook, progress_bus = _capture_hook_with_bus(tmp_path, logger, task_sn)

    rates: list[float] = []
    original_update_rate = progress_bus.update_rate

    def tracking_rate(sn: int, rate: float) -> None:
        rates.append(rate)
        original_update_rate(sn, rate)

    # Start time counter past the throttle interval so the first hook call is not suppressed.
    time_counter = [_THROTTLE_INTERVAL + 0.01]

    def fake_monotonic() -> float:
        v = time_counter[0]
        time_counter[0] += _THROTTLE_INTERVAL + 0.01
        return v

    with (
        unittest.mock.patch.object(progress_bus, 'update_rate', side_effect=tracking_rate),
        unittest.mock.patch('app.downloader.bilibili.ytdlp_downloader.time.monotonic', side_effect=fake_monotonic),
    ):
        hook({'status': 'downloading', 'filename': 'video.mp4', 'downloaded_bytes': 800, 'total_bytes': 1000})
        hook({'status': 'finished', 'filename': 'video.mp4', 'total_bytes': 1000})
        hook({'status': 'downloading', 'filename': 'audio.m4a', 'downloaded_bytes': 0, 'total_bytes': 500})

    downloading_rates = [r for r in rates if r < 100.0]
    assert downloading_rates, 'expected at least one downloading rate'
    # No rate should drop to near-zero (old bug: audio starting at 0/500 = 0%).
    for r in downloading_rates:
        assert r > 10.0, (
            f'Rate reset to near-zero ({r}) when new file started — aggregation not working'
        )


# ---------------------------------------------------------------------------
# Terminal state tests — success path always sets rate=100 / 下載完成
# ---------------------------------------------------------------------------


def test_download_sets_rate_100_on_success_even_when_hooks_silent(
    tmp_path: pathlib.Path, logger: Logger
) -> None:
    """When yt-dlp skips because the file already exists, no hooks fire.

    download() must still emit rate=100 and status=下載完成 on the success path.
    """
    task_sn = 40
    progress_bus = ProgressBus()
    progress_bus.start(task_sn, 'bvid')
    downloader = _make_downloader(progress_bus, logger, tmp_path)

    with (
        unittest.mock.patch.object(progress_bus, 'update_rate') as mock_rate,
        unittest.mock.patch.object(progress_bus, 'update_status') as mock_status,
        unittest.mock.patch.object(progress_bus, 'update_stats') as mock_stats,
        unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls,
    ):
        mock_ydl_instance = unittest.mock.MagicMock()
        mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)
        mock_ydl_cls.side_effect = lambda opts: mock_ydl_cls.return_value
        mock_ydl_instance.extract_info.return_value = None

        downloader.download(task_sn, 'BV1xx411c7mD', resolution='1080', classify=True)

    mock_rate.assert_any_call(task_sn, 100.0)
    mock_status.assert_any_call(task_sn, '下載完成')
    mock_stats.assert_any_call(task_sn, speed_mbps=None, eta_seconds=None)


def test_download_does_not_set_rate_100_on_exception(
    tmp_path: pathlib.Path, logger: Logger
) -> None:
    """When yt-dlp raises DownloadCancelled, the terminal rate=100 / 下載完成 must NOT be emitted."""
    task_sn = 41
    progress_bus = ProgressBus()
    progress_bus.start(task_sn, 'bvid')
    downloader = _make_downloader(progress_bus, logger, tmp_path)

    terminal_rate_calls: list[float] = []
    terminal_status_calls: list[str] = []

    def tracking_rate(sn: int, rate: float) -> None:
        terminal_rate_calls.append(rate)

    def tracking_status(sn: int, status: str) -> None:
        terminal_status_calls.append(status)

    with (
        unittest.mock.patch.object(progress_bus, 'update_rate', side_effect=tracking_rate),
        unittest.mock.patch.object(progress_bus, 'update_status', side_effect=tracking_status),
        unittest.mock.patch('yt_dlp.YoutubeDL') as mock_ydl_cls,
    ):
        mock_ydl_instance = unittest.mock.MagicMock()
        mock_ydl_cls.return_value.__enter__ = unittest.mock.Mock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = unittest.mock.Mock(return_value=False)
        mock_ydl_cls.side_effect = lambda opts: mock_ydl_cls.return_value
        mock_ydl_instance.extract_info.side_effect = yt_dlp.utils.DownloadCancelled()

        with pytest.raises(yt_dlp.utils.DownloadCancelled):
            downloader.download(task_sn, 'BV1xx411c7mD', resolution='1080', classify=True)

    assert 100.0 not in terminal_rate_calls, (
        f'update_rate(100.0) must not be called on exception; got {terminal_rate_calls}'
    )
    assert '下載完成' not in terminal_status_calls, (
        f'update_status(下載完成) must not be called on exception; got {terminal_status_calls}'
    )

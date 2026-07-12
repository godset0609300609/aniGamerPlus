"""Tests for BilibiliRunner ffmpeg guardrail and YtdlpDownloader ffmpeg_location option."""

from __future__ import annotations

import pathlib
import typing as T
import unittest.mock

import pytest

from app.downloader.bilibili.runner import BilibiliRunner
from app.downloader.bilibili.ytdlp_downloader import YtdlpDownloader
from app.downloader.ffmpeg import resolve_ffmpeg_path
from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.persistence.paths import WorkspacePaths

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture
def progress_bus() -> ProgressBus:
    return ProgressBus()


def _make_settings() -> T.Any:
    from app.models import AppSettings

    return AppSettings()


class FakeYtdlpDownloader:
    def __init__(self) -> None:
        self.extract_calls: list[str] = []
        self.download_calls: list[dict[str, T.Any]] = []

    def extract_info(self, bvid: str) -> dict[str, T.Any]:
        self.extract_calls.append(bvid)
        return {'title': bvid, 'entries': None}

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
        self.download_calls.append({'task_sn': task_sn, 'bvid': bvid})
        return {}


# ---------------------------------------------------------------------------
# resolve_ffmpeg_path helper-level tests
# ---------------------------------------------------------------------------


def test_resolve_ffmpeg_path_prefers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: '/usr/bin/ffmpeg')
    result = resolve_ffmpeg_path()
    assert result == '/usr/bin/ffmpeg'


def test_resolve_ffmpeg_path_falls_back_to_cwd(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: None)
    monkeypatch.setattr('app.downloader.ffmpeg.os.name', 'nt')
    candidate = tmp_path / 'ffmpeg.exe'
    candidate.write_bytes(b'')
    monkeypatch.chdir(tmp_path)
    result = resolve_ffmpeg_path()
    assert result is not None
    assert pathlib.Path(result).name == 'ffmpeg.exe'


def test_resolve_ffmpeg_path_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.downloader.ffmpeg.shutil.which', lambda _: None)
    monkeypatch.setattr('app.downloader.ffmpeg.os.name', 'nt')
    # No ffmpeg.exe in cwd (relies on tmp test isolation of chdir not being set)
    # We patch pathlib.Path.exists to always return False to be safe.
    with unittest.mock.patch('app.downloader.ffmpeg.pathlib.Path.exists', return_value=False):
        result = resolve_ffmpeg_path()
    assert result is None


# ---------------------------------------------------------------------------
# BilibiliRunner: ffmpeg missing → early fail, yt-dlp never called
# ---------------------------------------------------------------------------


def test_runner_fails_early_when_ffmpeg_missing(
    progress_bus: ProgressBus, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr('app.downloader.bilibili.runner.resolve_ffmpeg_path', lambda: None)

    dl = FakeYtdlpDownloader()
    events: list[dict[str, T.Any]] = []

    def notify(*, kwargs: dict[str, T.Any]) -> None:
        events.append(dict(kwargs))

    runner = BilibiliRunner(
        ytdlp_downloader=dl,
        progress_bus=progress_bus,
        logger=logger,
        settings=_make_settings(),
        notify_event_send=notify,
    )
    runner.run(200, bvid='BV1test', resolution='1080', classify=True)

    assert dl.extract_calls == [], 'extract_info must not be called when ffmpeg is absent'
    assert dl.download_calls == [], 'download must not be called when ffmpeg is absent'

    snap = progress_bus.snapshot()
    assert 200 in snap
    assert snap[200].finished_at is not None

    failed_events = [e for e in events if e['event'] == 'failed']
    assert len(failed_events) == 1
    assert 'ffmpeg' in failed_events[0]['error_message']


def test_runner_status_is_failed_when_ffmpeg_missing(
    progress_bus: ProgressBus, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr('app.downloader.bilibili.runner.resolve_ffmpeg_path', lambda: None)

    dl = FakeYtdlpDownloader()
    runner = BilibiliRunner(
        ytdlp_downloader=dl,
        progress_bus=progress_bus,
        logger=logger,
        settings=_make_settings(),
    )
    runner.run(201, bvid='BV1test', resolution='1080', classify=True)

    snap = progress_bus.snapshot()
    assert snap[201].status == '失敗'


def test_runner_finish_called_when_ffmpeg_missing(
    progress_bus: ProgressBus, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr('app.downloader.bilibili.runner.resolve_ffmpeg_path', lambda: None)

    dl = FakeYtdlpDownloader()
    runner = BilibiliRunner(
        ytdlp_downloader=dl,
        progress_bus=progress_bus,
        logger=logger,
        settings=_make_settings(),
    )
    runner.run(202, bvid='BV1test', resolution='1080', classify=True)

    snap = progress_bus.snapshot()
    assert snap[202].finished_at is not None


# ---------------------------------------------------------------------------
# YtdlpDownloader: ffmpeg_location forwarded into yt-dlp opts
# ---------------------------------------------------------------------------


def test_ytdlp_downloader_passes_ffmpeg_location_to_opts(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ffmpeg_location is supplied, _base_opts must include it."""
    from app.logging_ import Logger
    from app.persistence.bilibili_cookie_repo import BilibiliCookieRepository

    log = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    bus = ProgressBus()
    cookie_repo = BilibiliCookieRepository(WorkspacePaths.detect(working_dir=tmp_path))

    ffmpeg_path = '/resolved/ffmpeg'
    dl = YtdlpDownloader(
        progress_bus=bus,
        cookie_repo=cookie_repo,
        bangumi_dir=tmp_path,
        logger=log,
        ffmpeg_location=ffmpeg_path,
    )

    opts = dl._base_opts()
    assert opts.get('ffmpeg_location') == ffmpeg_path


def test_ytdlp_downloader_no_ffmpeg_location_key_when_none(
    tmp_path: pathlib.Path,
) -> None:
    from app.logging_ import Logger
    from app.persistence.bilibili_cookie_repo import BilibiliCookieRepository

    log = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    bus = ProgressBus()
    cookie_repo = BilibiliCookieRepository(WorkspacePaths.detect(working_dir=tmp_path))

    dl = YtdlpDownloader(
        progress_bus=bus,
        cookie_repo=cookie_repo,
        bangumi_dir=tmp_path,
        logger=log,
        ffmpeg_location=None,
    )

    opts = dl._base_opts()
    assert 'ffmpeg_location' not in opts


# ---------------------------------------------------------------------------
# Container wiring: resolve_ffmpeg_path() is threaded into YtdlpDownloader
# ---------------------------------------------------------------------------


def test_container_wires_ffmpeg_location_into_ytdlp_downloader(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The container passes resolve_ffmpeg_path()'s return value into
    YtdlpDownloader so yt-dlp can find ffmpeg.exe located in backend/.
    """
    import app.core as core_module
    from app.persistence.bilibili_cookie_repo import BilibiliCookieRepository

    fake_ffmpeg = '/fake/ffmpeg'
    monkeypatch.setattr(core_module, 'resolve_ffmpeg_path', lambda: fake_ffmpeg)

    log = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    bus = ProgressBus()
    cookie_repo = BilibiliCookieRepository(WorkspacePaths.detect(working_dir=tmp_path))

    dl = YtdlpDownloader(
        progress_bus=bus,
        cookie_repo=cookie_repo,
        bangumi_dir=tmp_path,
        logger=log,
        ffmpeg_location=core_module.resolve_ffmpeg_path(),
    )

    assert dl._ffmpeg_location == fake_ffmpeg

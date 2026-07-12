"""Tests for BilibiliRunner lifecycle and telegram events."""

from __future__ import annotations

import pathlib
import typing as T

import pytest
import yt_dlp.utils

from app.downloader.bilibili.runner import BilibiliRunner
from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.persistence.paths import WorkspacePaths


class FakeYtdlpDownloader:
    def __init__(
        self,
        info: dict[str, T.Any] | None = None,
        raise_on_download: Exception | None = None,
    ) -> None:
        self._info = info or {'title': 'Test Video', 'entries': None}
        self._raise = raise_on_download
        self.extract_calls: list[str] = []
        self.download_calls: list[dict[str, T.Any]] = []

    def extract_info(self, bvid: str) -> dict[str, T.Any]:
        self.extract_calls.append(bvid)
        return self._info

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
        self.download_calls.append(
            {
                'task_sn': task_sn,
                'bvid': bvid,
                'resolution': resolution,
                'part_idx': part_idx,
            }
        )
        if self._raise is not None:
            raise self._raise
        return self._info


@pytest.fixture
def paths(tmp_path: pathlib.Path) -> WorkspacePaths:
    return WorkspacePaths.detect(working_dir=tmp_path)


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture
def progress_bus() -> ProgressBus:
    return ProgressBus()


def _make_settings() -> T.Any:
    from app.models import AppSettings

    return AppSettings()


def _make_runner(
    downloader: FakeYtdlpDownloader,
    progress_bus: ProgressBus,
    logger: Logger,
    notify_send: T.Any = None,
) -> BilibiliRunner:
    return BilibiliRunner(
        ytdlp_downloader=downloader,
        progress_bus=progress_bus,
        logger=logger,
        settings=_make_settings(),
        notify_event_send=notify_send,
    )


def test_success_lifecycle(progress_bus: ProgressBus, logger: Logger) -> None:
    dl = FakeYtdlpDownloader(info={'title': 'My Video', 'entries': None})
    events: list[str] = []

    def notify(*, kwargs: dict[str, T.Any]) -> None:
        events.append(kwargs['event'])

    runner = _make_runner(dl, progress_bus, logger, notify_send=notify)
    runner.run(99, bvid='BV1xx411c7mD', resolution='1080', classify=True)

    assert 'started' in events
    assert 'completed' in events
    assert 'failed' not in events
    assert 'cancelled' not in events

    snap = progress_bus.snapshot()
    assert 99 in snap
    assert snap[99].finished_at is not None


def test_cancel_lifecycle(progress_bus: ProgressBus, logger: Logger) -> None:
    dl = FakeYtdlpDownloader(raise_on_download=yt_dlp.utils.DownloadCancelled())
    events: list[str] = []

    def notify(*, kwargs: dict[str, T.Any]) -> None:
        events.append(kwargs['event'])

    runner = _make_runner(dl, progress_bus, logger, notify_send=notify)
    runner.run(100, bvid='BV1xx411c7mD', resolution='1080', classify=True)

    assert 'cancelled' in events
    assert 'completed' not in events
    assert 'failed' not in events


def test_error_lifecycle(progress_bus: ProgressBus, logger: Logger) -> None:
    dl = FakeYtdlpDownloader(raise_on_download=RuntimeError('network error'))
    events: list[dict[str, T.Any]] = []

    def notify(*, kwargs: dict[str, T.Any]) -> None:
        events.append(dict(kwargs))

    runner = _make_runner(dl, progress_bus, logger, notify_send=notify)
    runner.run(101, bvid='BV1xx411c7mD', resolution='1080', classify=True)

    failed_events = [e for e in events if e['event'] == 'failed']
    assert len(failed_events) == 1
    assert 'error_message' in failed_events[0]


def test_multipart_creates_child_entries_not_parent(progress_bus: ProgressBus, logger: Logger) -> None:
    info = {
        'title': 'Multi P Video',
        'entries': [{'id': 'p1'}, {'id': 'p2'}, {'id': 'p3'}],
    }
    dl = FakeYtdlpDownloader(info=info)
    runner = _make_runner(dl, progress_bus, logger)
    runner.run(102, bvid='BV1xx411c7mD', resolution='1080', classify=True)

    snap = progress_bus.snapshot()
    # parent_sn must NOT have a progress entry in multi-part path
    assert 102 not in snap
    # child SNs are parent_sn * 1000 + idx when no task_id_map_repo wired
    child1 = 102 * 1000 + 1
    child2 = 102 * 1000 + 2
    child3 = 102 * 1000 + 3
    assert child1 in snap
    assert child2 in snap
    assert child3 in snap
    assert snap[child1].episode == 'P1/3'
    assert snap[child2].episode == 'P2/3'
    assert snap[child3].episode == 'P3/3'


def test_progress_bus_start_called_with_source(progress_bus: ProgressBus, logger: Logger) -> None:
    dl = FakeYtdlpDownloader()
    runner = _make_runner(dl, progress_bus, logger)
    runner.run(103, bvid='BV1xx411c7mD', resolution='1080', classify=True)

    snap = progress_bus.snapshot()
    assert 103 in snap


def test_no_notify_when_not_wired(progress_bus: ProgressBus, logger: Logger) -> None:
    dl = FakeYtdlpDownloader()
    runner = _make_runner(dl, progress_bus, logger, notify_send=None)
    runner.run(104, bvid='BV1xx411c7mD', resolution='1080', classify=True)
    snap = progress_bus.snapshot()
    assert 104 in snap
    assert snap[104].finished_at is not None

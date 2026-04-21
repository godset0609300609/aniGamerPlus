"""Tests for the ``Anime`` orchestrator.

Every collaborator is faked so the test never touches the network, disk
(beyond ``tmp_path``), ffmpeg or FTP. The fakes record method calls on a
``calls`` list each so assertions read top-to-bottom.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

import pytest

from app.downloader import exceptions
from app.downloader.anime import Anime, DownloadResult
from app.downloader.metadata import AnimeMetadata
from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.models import AppSettings
from app.persistence.paths import WorkspacePaths
from app.scheduler.cd_counter import DownloadCooldown

# ---------------------------------------------------------------------------
# Collaborator fakes
# ---------------------------------------------------------------------------


class _CallRecorder:
    """Mixin — every public call appends to ``self.calls``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _rec(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))


class _FakeMetadataExtractor(_CallRecorder):
    def __init__(self, meta: AnimeMetadata) -> None:
        super().__init__()
        self._meta = meta

    def fetch(self, sn: int) -> AnimeMetadata:
        self._rec('fetch', sn=sn)
        return self._meta


class _FakeM3u8Client(_CallRecorder):
    def __init__(self, streams: dict[str, str]) -> None:
        super().__init__()
        self._streams = streams

    def fetch(self, sn: int) -> dict[str, str]:
        self._rec('fetch', sn=sn)
        return dict(self._streams)


class _FakeSegmentDownloader(_CallRecorder):
    def __init__(self, size_mb: int = 100) -> None:
        super().__init__()
        self._size_mb = size_mb

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
        self._rec(
            'download',
            sn=sn,
            m3u8_url=m3u8_url,
            output_file=output_file,
            filename=filename,
        )
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(b'\x00' * 1024)
        return self._size_mb


class _FakeFFmpegDownloader(_CallRecorder):
    def __init__(self, size_mb: int = 100) -> None:
        super().__init__()
        self._size_mb = size_mb

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
        self._rec(
            'download',
            sn=sn,
            m3u8_url=m3u8_url,
            output_file=output_file,
            filename=filename,
        )
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(b'\x00' * 1024)
        return self._size_mb


class _FakeDanmuRenderer(_CallRecorder):
    def render(
        self,
        sn: int,
        full_filename: pathlib.Path,
        ban_words: Any = (),
    ) -> None:
        self._rec('render', sn=sn, full_filename=full_filename)


class _FakeFtpUploader(_CallRecorder):
    def __init__(self, result: bool = True) -> None:
        super().__init__()
        self._result = result

    def upload(
        self,
        local_path: pathlib.Path,
        filename: str,
        bangumi_tag: str,
        bangumi_name: str,
        sn: int,
    ) -> bool:
        self._rec(
            'upload',
            local_path=local_path,
            filename=filename,
            bangumi_tag=bangumi_tag,
            bangumi_name=bangumi_name,
            sn=sn,
        )
        return self._result


# ---------------------------------------------------------------------------
# Fixtures — build a fully-wired Anime with fakes in place.
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Harness:
    anime: Anime
    metadata: _FakeMetadataExtractor
    m3u8: _FakeM3u8Client
    segment: _FakeSegmentDownloader
    ffmpeg: _FakeFFmpegDownloader
    danmu: _FakeDanmuRenderer
    uploader: _FakeFtpUploader
    progress: ProgressBus
    settings: AppSettings
    cooldown: DownloadCooldown | None = None


def _sample_meta(sn: int = 1) -> AnimeMetadata:
    return AnimeMetadata(
        sn=sn,
        title='某某 [01]',
        bangumi_name='某某',
        bangumi_name_orig='某某',
        episode='01',
        episode_list={'01': sn},
    )


def _build_harness(
    tmp_path: pathlib.Path,
    *,
    streams: dict[str, str] | None = None,
    settings_overrides: dict[str, Any] | None = None,
    meta: AnimeMetadata | None = None,
    cooldown: DownloadCooldown | None = None,
) -> _Harness:
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    progress = ProgressBus()

    settings_kwargs: dict[str, Any] = {
        'ua': 'Mozilla/5.0',
        'bangumi_dir': str(tmp_path / 'bangumi'),
        'temp_dir': str(tmp_path / 'temp'),
        'segment_download_mode': True,
    }
    settings_kwargs.update(settings_overrides or {})
    settings = AppSettings(**settings_kwargs)

    meta = meta or _sample_meta()
    streams = streams or {'1080': 'https://cdn.example/1080.m3u8', '720': 'https://cdn.example/720.m3u8'}

    from app.downloader.filename import FilenameBuilder

    metadata = _FakeMetadataExtractor(meta)
    m3u8 = _FakeM3u8Client(streams)
    segment = _FakeSegmentDownloader()
    ffmpeg = _FakeFFmpegDownloader()
    danmu = _FakeDanmuRenderer()
    uploader = _FakeFtpUploader()

    anime = Anime(
        sn=meta.sn,
        metadata_extractor=metadata,  # type: ignore[arg-type]
        m3u8_client=m3u8,  # type: ignore[arg-type]
        segment_downloader=segment,  # type: ignore[arg-type]
        ffmpeg_downloader=ffmpeg,  # type: ignore[arg-type]
        filename_builder=FilenameBuilder(settings),
        danmu_renderer=danmu,  # type: ignore[arg-type]
        uploader=uploader,  # type: ignore[arg-type]
        progress=progress,
        settings=settings,
        paths=paths,
        logger=logger,
        cooldown=cooldown,
    )

    return _Harness(
        anime=anime,
        metadata=metadata,
        m3u8=m3u8,
        segment=segment,
        ffmpeg=ffmpeg,
        danmu=danmu,
        uploader=uploader,
        progress=progress,
        settings=settings,
        cooldown=cooldown,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_init_does_no_io(tmp_path: pathlib.Path) -> None:
    harness = _build_harness(tmp_path)
    # No .fetch / .download / .render / .upload on any dep.
    assert harness.metadata.calls == []
    assert harness.m3u8.calls == []
    assert harness.segment.calls == []
    assert harness.ffmpeg.calls == []
    assert harness.danmu.calls == []
    assert harness.uploader.calls == []


def test_load_is_idempotent(tmp_path: pathlib.Path) -> None:
    harness = _build_harness(tmp_path)
    harness.anime.load()
    harness.anime.load()
    assert len(harness.metadata.calls) == 1


def test_download_autoloads_metadata(tmp_path: pathlib.Path) -> None:
    harness = _build_harness(tmp_path)
    result = harness.anime.download(resolution='1080', classify=False)
    assert result.success is True
    assert any(c[0] == 'fetch' for c in harness.metadata.calls)


def test_lock_resolution_raises_when_missing(tmp_path: pathlib.Path) -> None:
    harness = _build_harness(
        tmp_path,
        streams={'720': 'https://cdn.example/720.m3u8'},
        settings_overrides={'lock_resolution': True},
    )
    with pytest.raises(exceptions.NoAvailableStreamError):
        harness.anime.download(resolution='1080', classify=False)


def test_unlocked_picks_closest_resolution(tmp_path: pathlib.Path) -> None:
    harness = _build_harness(
        tmp_path,
        streams={'480': 'https://cdn.example/480.m3u8', '720': 'https://cdn.example/720.m3u8'},
        settings_overrides={'lock_resolution': False},
    )
    result = harness.anime.download(resolution='1080', classify=False)
    assert result.success is True
    # 720 is closer to 1080 than 480.
    chosen_urls = [c[1]['m3u8_url'] for c in harness.segment.calls]
    assert any('720.m3u8' in u for u in chosen_urls)


def test_segment_mode_uses_segment_downloader(tmp_path: pathlib.Path) -> None:
    harness = _build_harness(
        tmp_path,
        settings_overrides={'segment_download_mode': True},
    )
    harness.anime.download(resolution='1080', classify=False)
    assert len(harness.segment.calls) == 1
    assert harness.ffmpeg.calls == []


def test_ffmpeg_mode_uses_ffmpeg_downloader(tmp_path: pathlib.Path) -> None:
    harness = _build_harness(
        tmp_path,
        settings_overrides={'segment_download_mode': False},
    )
    harness.anime.download(resolution='1080', classify=False)
    assert len(harness.ffmpeg.calls) == 1
    assert harness.segment.calls == []


def test_progress_status_transitions(tmp_path: pathlib.Path) -> None:
    """The orchestrator transitions 等待下載 → 正在下載 → 下載完成.

    On the success path, download() must end with status='下載完成' so that
    the outer finally-block's finish() call writes the correct terminal status
    to the DB instead of normalising the transient '正在下載' to '中斷'.
    """
    harness = _build_harness(tmp_path)

    seen: list[str] = []
    orig_update = harness.progress.update_status

    def record_update(sn: int, status: str) -> None:
        seen.append(status)
        orig_update(sn, status)

    harness.progress.update_status = record_update  # type: ignore[assignment]

    harness.anime.download(resolution='1080', classify=False)

    assert '正在下載' in seen
    # Success path must end with '下載完成' so the outer finish() sees a
    # recognised terminal status and writes it correctly to the DB.
    assert '下載完成' in seen
    assert seen[-1] == '下載完成'

    # And the initial 'start' seeded 等待下載.
    bus = ProgressBus()
    captured_starts: list[tuple[int, str, str]] = []
    orig_start = bus.start

    def record_start(
        sn: int,
        filename: str,
        status: str = '等待下載',
        *,
        bangumi_name: str | None = None,
        episode: str | None = None,
        resolution: str | None = None,
    ) -> None:
        captured_starts.append((sn, filename, status))
        orig_start(
            sn,
            filename,
            status,
            bangumi_name=bangumi_name,
            episode=episode,
            resolution=resolution,
        )

    bus.start = record_start  # type: ignore[assignment]

    # Rebuild with the instrumented bus.
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    from app.downloader.filename import FilenameBuilder

    meta = _sample_meta()
    settings = harness.settings
    anime2 = Anime(
        sn=meta.sn,
        metadata_extractor=_FakeMetadataExtractor(meta),  # type: ignore[arg-type]
        m3u8_client=_FakeM3u8Client({'1080': 'https://x'}),  # type: ignore[arg-type]
        segment_downloader=_FakeSegmentDownloader(),  # type: ignore[arg-type]
        ffmpeg_downloader=_FakeFFmpegDownloader(),  # type: ignore[arg-type]
        filename_builder=FilenameBuilder(settings),
        danmu_renderer=_FakeDanmuRenderer(),  # type: ignore[arg-type]
        uploader=_FakeFtpUploader(),  # type: ignore[arg-type]
        progress=bus,
        settings=settings,
        paths=paths,
        logger=logger,
    )
    anime2.download(resolution='1080', classify=False)
    assert captured_starts[0][2] == '等待下載'


def test_download_returns_result_shape(tmp_path: pathlib.Path) -> None:
    harness = _build_harness(tmp_path)
    result = harness.anime.download(resolution='1080', classify=False)
    assert isinstance(result, DownloadResult)
    assert result.success is True
    assert result.file_path is not None
    assert result.file_path.exists()
    assert result.size_mb == 100


# ---------------------------------------------------------------------------
# Cancel tests
# ---------------------------------------------------------------------------


def test_cancel_before_m3u8_fetch_returns_failure(tmp_path: pathlib.Path) -> None:
    """Cancel event set immediately after start() → download() returns failure.

    The orchestrator calls progress.start() first (creating a fresh Event),
    then checks cancel before the m3u8 fetch.  We intercept start() to set
    the cancel event the moment it is created so the first _check_cancelled()
    fires.
    """
    harness = _build_harness(tmp_path)

    # Wrap progress.start so we can grab the newly created cancel event and
    # set it before download() proceeds past _check_cancelled().
    orig_start = harness.progress.start

    def _intercept_start(sn: int, filename: str, status: str = '等待下載', **kw: Any) -> None:
        orig_start(sn, filename, status, **kw)
        # Immediately signal cancel on the event that was just created.
        ev = harness.progress.get_cancel_event(sn)
        if ev is not None:
            ev.set()

    harness.progress.start = _intercept_start  # type: ignore[method-assign]

    result = harness.anime.download(resolution='1080', classify=False)

    assert result.success is False
    assert result.file_path is None
    assert result.size_mb == 0
    # The segment downloader must NOT have been invoked.
    assert harness.segment.calls == []


def test_cancel_between_m3u8_and_download_stops_pipeline(tmp_path: pathlib.Path) -> None:
    """Cancel set after m3u8 fetch but before _run_download → no actual download.

    Strategy: wrap progress.get_cancel_event to intercept the live event
    reference, then wrap the m3u8 client to set it mid-fetch.  This ensures
    we set the same event that the orchestrator is holding, not a stale one
    replaced by a subsequent start() call.
    """
    import threading

    harness = _build_harness(tmp_path)

    # Capture the live cancel_event that the orchestrator creates in start().
    live_event: list[threading.Event] = []
    orig_get = harness.progress.get_cancel_event

    def _capturing_get_cancel_event(sn: int) -> threading.Event | None:
        ev = orig_get(sn)
        if ev is not None and not live_event:
            live_event.append(ev)
        return ev

    harness.progress.get_cancel_event = _capturing_get_cancel_event  # type: ignore[method-assign]

    class _CancelAfterM3u8Fetch(_FakeM3u8Client):
        """Set the live cancel event as the fetch response is being returned."""

        def fetch(self, sn: int) -> dict[str, Any]:
            result = super().fetch(sn)
            # Set the event captured from the orchestrator's get_cancel_event call.
            if live_event:
                live_event[0].set()
            return result

    from app.downloader.filename import FilenameBuilder
    from app.logging_ import Logger
    from app.persistence.paths import WorkspacePaths

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    meta = _sample_meta()
    cancelling_m3u8 = _CancelAfterM3u8Fetch({'1080': 'https://cdn.example/1080.m3u8'})
    segment = _FakeSegmentDownloader()

    anime = Anime(
        sn=meta.sn,
        metadata_extractor=_FakeMetadataExtractor(meta),  # type: ignore[arg-type]
        m3u8_client=cancelling_m3u8,  # type: ignore[arg-type]
        segment_downloader=segment,  # type: ignore[arg-type]
        ffmpeg_downloader=harness.ffmpeg,  # type: ignore[arg-type]
        filename_builder=FilenameBuilder(harness.settings),
        danmu_renderer=harness.danmu,  # type: ignore[arg-type]
        uploader=harness.uploader,  # type: ignore[arg-type]
        progress=harness.progress,
        settings=harness.settings,
        paths=paths,
        logger=logger,
    )
    result = anime.download(resolution='1080', classify=False)

    assert result.success is False
    # Segment downloader was NOT called — cancel fired before _run_download.
    assert segment.calls == []


# ---------------------------------------------------------------------------
# Cooldown tests (C) — cooldown fires after parse, before download
# ---------------------------------------------------------------------------


def test_cooldown_called_after_parse_before_download(tmp_path: pathlib.Path) -> None:
    """Cooldown wait() must be called after metadata.fetch (parse phase) but
    before segment_downloader.download (actual byte transfer begins).

    The call order recorded across all fakes must satisfy:
    ``metadata.fetch`` → ``cooldown.wait`` → ``segment.download``
    """
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    cooldown = DownloadCooldown(5, logger)
    call_log: list[str] = []
    cooldown._set_sleep(lambda _s: call_log.append('cooldown.wait'))

    harness = _build_harness(
        tmp_path,
        settings_overrides={'segment_download_mode': True},
        cooldown=cooldown,
    )

    # Wrap metadata fetch to record its call.
    orig_fetch = harness.metadata.fetch

    def _recording_fetch(sn: int) -> AnimeMetadata:
        call_log.append('metadata.fetch')
        return orig_fetch(sn)

    harness.metadata.fetch = _recording_fetch  # type: ignore[method-assign]

    # Wrap segment download to record its call.
    orig_download = harness.segment.download

    def _recording_download(*args: Any, **kwargs: Any) -> int:
        call_log.append('segment.download')
        return orig_download(*args, **kwargs)

    harness.segment.download = _recording_download  # type: ignore[method-assign]

    result = harness.anime.download(resolution='1080', classify=False)

    assert result.success is True
    assert 'metadata.fetch' in call_log
    assert 'cooldown.wait' in call_log
    assert 'segment.download' in call_log
    fetch_idx = call_log.index('metadata.fetch')
    cooldown_idx = call_log.index('cooldown.wait')
    download_idx = call_log.index('segment.download')
    assert fetch_idx < cooldown_idx, 'cooldown must fire AFTER metadata.fetch'
    assert cooldown_idx < download_idx, 'cooldown must fire BEFORE segment.download'


def test_download_sets_status_to_cooldown_during_wait(tmp_path: pathlib.Path) -> None:
    """Status must be '下載冷卻' when cooldown.wait() is entered, then '正在下載' after.

    A fake cooldown captures progress.snapshot() the instant wait() is called
    so we can assert the status seen by the UI during the sleeping period.
    """
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    cooldown = DownloadCooldown(5, logger)

    harness = _build_harness(
        tmp_path,
        settings_overrides={'segment_download_mode': True},
        cooldown=cooldown,
    )

    status_during_wait: list[str] = []

    def _spy_sleep(_s: float) -> None:
        snap = harness.progress.snapshot()
        entry = snap.get(harness.anime.sn)
        if entry is not None:
            status_during_wait.append(entry.status)

    cooldown._set_sleep(_spy_sleep)

    status_sequence: list[str] = []
    orig_update = harness.progress.update_status

    def _record_update(sn: int, status: str) -> None:
        status_sequence.append(status)
        orig_update(sn, status)

    harness.progress.update_status = _record_update  # type: ignore[method-assign]

    result = harness.anime.download(resolution='1080', classify=False)

    assert result.success is True
    # During the sleep, status must be '下載冷卻'.
    assert status_during_wait == ['下載冷卻'], (
        f"Expected ['下載冷卻'] while cooldown sleeps, got {status_during_wait!r}"
    )
    # After cooldown, '正在下載' must follow '下載冷卻' in the sequence.
    assert '下載冷卻' in status_sequence
    assert '正在下載' in status_sequence
    cooldown_idx = status_sequence.index('下載冷卻')
    download_idx = status_sequence.index('正在下載')
    assert cooldown_idx < download_idx, "'正在下載' must be set after '下載冷卻' in the status sequence"


def test_no_cooldown_still_downloads(tmp_path: pathlib.Path) -> None:
    """``cooldown=None`` (the default) must not raise and download must succeed."""
    harness = _build_harness(tmp_path)  # no cooldown
    result = harness.anime.download(resolution='1080', classify=False)
    assert result.success is True


def test_cooldown_not_called_when_get_info(tmp_path: pathlib.Path) -> None:
    """get_info() does not reach the download phase, so cooldown must not fire."""
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    cooldown = DownloadCooldown(5, logger)
    slept: list[float] = []
    cooldown._set_sleep(lambda s: slept.append(s))

    harness = _build_harness(tmp_path, cooldown=cooldown)
    harness.anime.load()
    harness.anime.get_info()

    assert slept == [], 'cooldown.wait() must not be called during get_info'


def test_success_path_sets_download_complete_status(tmp_path: pathlib.Path) -> None:
    """After a successful download, Anime.download() must set status to '下載完成'
    so that when the outer finally block calls progress.finish(sn), the DB row
    receives the correct terminal status instead of being normalised to '中斷'.
    """
    harness = _build_harness(tmp_path)

    status_sequence: list[str] = []
    orig_update = harness.progress.update_status

    def _record(sn: int, status: str) -> None:
        status_sequence.append(status)
        orig_update(sn, status)

    harness.progress.update_status = _record  # type: ignore[method-assign]

    result = harness.anime.download(resolution='1080', classify=False)

    assert result.success is True
    # The last status set by download() must be '下載完成' so the outer
    # safety-net finish() call sees a terminal status.
    assert status_sequence[-1] == '下載完成'

    # In-memory entry must reflect '下載完成'.
    snap = harness.progress.snapshot()
    sn = harness.anime.sn
    assert snap[sn].status == '下載完成'


def test_success_path_does_not_call_finish(tmp_path: pathlib.Path) -> None:
    """Anime.download() itself must NOT call progress.finish(sn) on the success
    path — finish() is the responsibility of the outer caller (ManualRunner
    _download_one finally block).  This keeps the design consistent: exactly
    one call site owns the finish() lifecycle per task.
    """
    harness = _build_harness(tmp_path)

    finish_calls: list[int] = []
    orig_finish = harness.progress.finish

    def _record_finish(sn: int) -> None:
        finish_calls.append(sn)
        orig_finish(sn)

    harness.progress.finish = _record_finish  # type: ignore[method-assign]

    result = harness.anime.download(resolution='1080', classify=False)

    assert result.success is True
    # download() must not call finish() — leave that to the outer wrapper.
    assert finish_calls == [], (
        'Anime.download() must not call progress.finish(sn); the outer finally block is responsible for that'
    )


def test_cancel_after_download_stops_post_processing(tmp_path: pathlib.Path) -> None:
    """Cancel set inside the fake segment downloader → danmu renderer not called.

    Strategy: wrap progress.get_cancel_event to capture the live Event, then
    set it from inside the fake segment downloader after "download" completes.
    """
    import threading

    harness = _build_harness(
        tmp_path,
        settings_overrides={'segment_download_mode': True},
    )

    live_event: list[threading.Event] = []
    orig_get = harness.progress.get_cancel_event

    def _capturing_get(sn: int) -> threading.Event | None:
        ev = orig_get(sn)
        if ev is not None and not live_event:
            live_event.append(ev)
        return ev

    harness.progress.get_cancel_event = _capturing_get  # type: ignore[method-assign]

    class _CancellingSegmentDownloader(_FakeSegmentDownloader):
        def download(self, sn: int, *args: Any, **kwargs: Any) -> int:  # type: ignore[override]
            size = super().download(sn, *args, **kwargs)
            if live_event:
                live_event[0].set()  # signal cancel after "download" completes
            return size

    from app.downloader.filename import FilenameBuilder
    from app.logging_ import Logger
    from app.persistence.paths import WorkspacePaths

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    meta = _sample_meta()
    cancelling_seg = _CancellingSegmentDownloader()

    anime = Anime(
        sn=meta.sn,
        metadata_extractor=_FakeMetadataExtractor(meta),  # type: ignore[arg-type]
        m3u8_client=_FakeM3u8Client({'1080': 'https://cdn.example/1080.m3u8'}),  # type: ignore[arg-type]
        segment_downloader=cancelling_seg,  # type: ignore[arg-type]
        ffmpeg_downloader=harness.ffmpeg,  # type: ignore[arg-type]
        filename_builder=FilenameBuilder(harness.settings),
        danmu_renderer=harness.danmu,  # type: ignore[arg-type]
        uploader=harness.uploader,  # type: ignore[arg-type]
        progress=harness.progress,
        settings=harness.settings,
        paths=paths,
        logger=logger,
    )
    # Enable danmu so we can verify it was skipped.
    anime.enable_danmu()
    result = anime.download(resolution='1080', classify=False)

    assert result.success is False
    # Danmu renderer skipped because cancel fired before post-processing.
    assert harness.danmu.calls == []


# ---------------------------------------------------------------------------
# Bug (1) — failure paths set status to '失敗'
# ---------------------------------------------------------------------------


def test_download_failure_updates_status_to_failed_no_stream(
    tmp_path: pathlib.Path,
) -> None:
    """When get_m3u8_dict raises NoAvailableStreamError, Anime.download() must
    set status to '失敗' before re-raising so the outer finish() call writes
    '失敗' to the DB instead of normalising the transient '正在解析' to '中斷'.
    """

    class _EmptyM3u8Client(_FakeM3u8Client):
        def fetch(self, sn: int) -> dict[str, str]:
            raise exceptions.NoAvailableStreamError('page has no title — episode may be deleted')

    from app.downloader.filename import FilenameBuilder
    from app.logging_ import Logger
    from app.persistence.paths import WorkspacePaths

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    progress = ProgressBus()
    meta = _sample_meta()
    settings = AppSettings(ua='Mozilla/5.0', bangumi_dir=str(tmp_path / 'bangumi'), temp_dir=str(tmp_path / 'temp'))

    anime = Anime(
        sn=meta.sn,
        metadata_extractor=_FakeMetadataExtractor(meta),  # type: ignore[arg-type]
        m3u8_client=_EmptyM3u8Client({}),  # type: ignore[arg-type]
        segment_downloader=_FakeSegmentDownloader(),  # type: ignore[arg-type]
        ffmpeg_downloader=_FakeFFmpegDownloader(),  # type: ignore[arg-type]
        filename_builder=FilenameBuilder(settings),
        danmu_renderer=_FakeDanmuRenderer(),  # type: ignore[arg-type]
        uploader=_FakeFtpUploader(),  # type: ignore[arg-type]
        progress=progress,
        settings=settings,
        paths=paths,
        logger=logger,
    )

    progress.start(meta.sn, '《某某》', status='等待下載')

    with pytest.raises(exceptions.NoAvailableStreamError):
        anime.download(resolution='1080', classify=False)

    snap = progress.snapshot()
    entry = snap.get(meta.sn)
    assert entry is not None
    assert entry.status == '失敗', f"Expected status '失敗' after NoAvailableStreamError, got {entry.status!r}"


def test_download_failure_updates_status_to_failed_too_many_tries(
    tmp_path: pathlib.Path,
) -> None:
    """When the segment downloader raises TryTooManyTimeError, Anime.download()
    must set status to '失敗' before re-raising.
    """

    class _FailingSegmentDownloader(_FakeSegmentDownloader):
        def download(self, sn: int, *args: Any, **kwargs: Any) -> int:  # type: ignore[override]
            raise exceptions.TryTooManyTimeError('retries exhausted')

    from app.downloader.filename import FilenameBuilder
    from app.logging_ import Logger
    from app.persistence.paths import WorkspacePaths

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    progress = ProgressBus()
    meta = _sample_meta()
    settings = AppSettings(
        ua='Mozilla/5.0',
        bangumi_dir=str(tmp_path / 'bangumi'),
        temp_dir=str(tmp_path / 'temp'),
        segment_download_mode=True,
    )

    anime = Anime(
        sn=meta.sn,
        metadata_extractor=_FakeMetadataExtractor(meta),  # type: ignore[arg-type]
        m3u8_client=_FakeM3u8Client({'1080': 'https://cdn.example/1080.m3u8'}),  # type: ignore[arg-type]
        segment_downloader=_FailingSegmentDownloader(),  # type: ignore[arg-type]
        ffmpeg_downloader=_FakeFFmpegDownloader(),  # type: ignore[arg-type]
        filename_builder=FilenameBuilder(settings),
        danmu_renderer=_FakeDanmuRenderer(),  # type: ignore[arg-type]
        uploader=_FakeFtpUploader(),  # type: ignore[arg-type]
        progress=progress,
        settings=settings,
        paths=paths,
        logger=logger,
    )

    progress.start(meta.sn, '《某某》', status='等待下載')

    with pytest.raises(exceptions.TryTooManyTimeError):
        anime.download(resolution='1080', classify=False)

    snap = progress.snapshot()
    entry = snap.get(meta.sn)
    assert entry is not None
    assert entry.status == '失敗', f"Expected status '失敗' after TryTooManyTimeError, got {entry.status!r}"


# ---------------------------------------------------------------------------
# Bug (2) — resolution updated on stream selection
# ---------------------------------------------------------------------------


def test_resolution_updated_on_stream_selection(tmp_path: pathlib.Path) -> None:
    """After successful stream selection, progress entry must have resolution set.

    This verifies that update_resolution is called with the picked resolution
    label (e.g. '1080p') so that finish() can persist it to the DB and the
    frontend can display it in the recently-completed list.
    """
    harness = _build_harness(
        tmp_path,
        streams={'1080': 'https://cdn.example/1080.m3u8', '720': 'https://cdn.example/720.m3u8'},
    )

    update_resolution_calls: list[tuple[int, str]] = []
    orig_update_resolution = harness.progress.update_resolution

    def _recording_update_resolution(sn: int, resolution: str) -> None:
        update_resolution_calls.append((sn, resolution))
        orig_update_resolution(sn, resolution)

    harness.progress.update_resolution = _recording_update_resolution  # type: ignore[method-assign]

    result = harness.anime.download(resolution='1080', classify=False)

    assert result.success is True
    # update_resolution must have been called with the correct resolution label.
    assert len(update_resolution_calls) >= 1, 'update_resolution was never called'
    sn_arg, res_arg = update_resolution_calls[0]
    assert sn_arg == harness.anime.sn
    assert res_arg == '1080p', f"Expected '1080p', got {res_arg!r}"

    # In-memory entry must reflect the resolution.
    snap = harness.progress.snapshot()
    entry = snap.get(harness.anime.sn)
    assert entry is not None
    assert entry.resolution == '1080p', f"Expected resolution '1080p' in progress entry, got {entry.resolution!r}"


def test_resolution_updated_when_fallback_resolution_picked(tmp_path: pathlib.Path) -> None:
    """When the requested resolution is unavailable and a fallback is selected,
    update_resolution must be called with the FALLBACK resolution label.
    """
    harness = _build_harness(
        tmp_path,
        streams={'720': 'https://cdn.example/720.m3u8'},
        settings_overrides={'lock_resolution': False},
    )

    update_resolution_calls: list[tuple[int, str]] = []
    orig = harness.progress.update_resolution

    def _rec(sn: int, resolution: str) -> None:
        update_resolution_calls.append((sn, resolution))
        orig(sn, resolution)

    harness.progress.update_resolution = _rec  # type: ignore[method-assign]

    result = harness.anime.download(resolution='1080', classify=False)

    assert result.success is True
    assert len(update_resolution_calls) >= 1
    _sn, res = update_resolution_calls[0]
    # Fallback to 720 — label must be '720p'.
    assert res == '720p', f"Expected '720p' fallback label, got {res!r}"

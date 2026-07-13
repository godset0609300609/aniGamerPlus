"""Tests for ``TgDownloadWatcher`` — filter matching, dedup, download
execution, and task_history/ProgressBus/notification wiring.

Messages are lightweight ``types.SimpleNamespace`` stand-ins (a full
``hydrogram.types.Message`` needs a live client + chat/user graph that isn't
worth constructing here — the "real integration" boundary that matters for
this module is the *filter/dedup/persistence* logic, not hydrogram's message
parsing, which hydrogram itself already tests), but the media objects
attached to them (``.video`` / ``.document`` / ...) are genuine
``hydrogram.types`` instances so ``_extract_media``'s ``getattr`` reads
(``file_id`` / ``file_unique_id`` / ``file_name`` / ``file_size``) exercise
real hydrogram attribute shapes rather than a hand-rolled dict.
"""

from __future__ import annotations

import asyncio
import pathlib
import time
import types
import unittest.mock

import hydrogram
import hydrogram.types
import pydantic
import pytest

from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.models import TgWatchedChatCreate
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths
from app.persistence.task_history_repo import TaskHistoryRepository
from app.persistence.task_id_map_repo import TaskIdMapRepository
from app.persistence.tg_downloaded_media_repo import TgDownloadedMediaRepository
from app.persistence.tg_watched_chat_repo import TgWatchedChatRepository
from app.tg_downloader.downloader import TgDownloadWatcher

USER_ID = 'user-1'
CHAT_ID = -1001111111111
MESSAGE_ID = 42


@pytest.fixture
def database(tmp_path: pathlib.Path) -> Database:
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    db.run_baseline_migrations()
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def watched_chat_repo(database: Database) -> TgWatchedChatRepository:
    return TgWatchedChatRepository(database)


@pytest.fixture
def downloaded_media_repo(database: Database) -> TgDownloadedMediaRepository:
    return TgDownloadedMediaRepository(database)


@pytest.fixture
def task_history_repo(database: Database) -> TaskHistoryRepository:
    return TaskHistoryRepository(database)


@pytest.fixture
def task_id_map_repo(database: Database) -> TaskIdMapRepository:
    return TaskIdMapRepository(database)


@pytest.fixture
def progress_bus() -> ProgressBus:
    return ProgressBus()


class _EventCapture:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, *, kwargs: dict[str, object]) -> None:
        self.calls.append(kwargs)


@pytest.fixture
def notify() -> _EventCapture:
    return _EventCapture()


@pytest.fixture
def watcher(
    watched_chat_repo: TgWatchedChatRepository,
    downloaded_media_repo: TgDownloadedMediaRepository,
    task_history_repo: TaskHistoryRepository,
    task_id_map_repo: TaskIdMapRepository,
    progress_bus: ProgressBus,
    notify: _EventCapture,
    tmp_path: pathlib.Path,
) -> TgDownloadWatcher:
    return TgDownloadWatcher(
        watched_chat_repo,
        downloaded_media_repo,
        tmp_path / 'bangumi',
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
        progress_bus=progress_bus,
        notify_event_send=notify,
    )


def _watch_chat(repo: TgWatchedChatRepository, **overrides: object) -> None:
    defaults: dict[str, object] = {
        'chat_id': CHAT_ID,
        'chat_title': '測試頻道',
        'media_types': ['video'],
        'size_min_mb': None,
        'size_max_mb': None,
        'format_whitelist': None,
        'save_path': None,
        'enabled': True,
    }
    defaults.update(overrides)
    repo.insert(USER_ID, TgWatchedChatCreate(**defaults))  # type: ignore[arg-type]


def _real_video(*, file_size: int = 50 * 1024 * 1024, file_name: str = 'episode01.mp4') -> hydrogram.types.Video:
    return hydrogram.types.Video(
        file_id='video-file-id',
        file_unique_id='video-unique-id',
        width=1920,
        height=1080,
        duration=1200,
        file_name=file_name,
        file_size=file_size,
    )


def _message(*, video: hydrogram.types.Video | None = None, document: object = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(id=CHAT_ID),
        id=MESSAGE_ID,
        video=video,
        document=document,
        audio=None,
        photo=None,
    )


def _client_with_download(*, download_result: str | Exception = 'DEST_RESULT_PATH') -> unittest.mock.AsyncMock:
    client = unittest.mock.AsyncMock()
    if isinstance(download_result, Exception):
        client.download_media = unittest.mock.AsyncMock(side_effect=download_result)
    else:
        client.download_media = unittest.mock.AsyncMock(return_value=download_result)
    return client


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_matching_video_is_downloaded_and_recorded(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    downloaded_media_repo: TgDownloadedMediaRepository,
    task_history_repo: TaskHistoryRepository,
    progress_bus: ProgressBus,
    notify: _EventCapture,
) -> None:
    _watch_chat(watched_chat_repo)
    client = _client_with_download()
    message = _message(video=_real_video())

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_awaited_once()
    assert downloaded_media_repo.exists(USER_ID, CHAT_ID, MESSAGE_ID) is True

    history = task_history_repo.list_recent(days=1, user_id=USER_ID)
    assert len(history) == 1
    assert history[0].final_status == '下載完成'
    assert history[0].source == 'tg'

    events_by_type = [c['event'] for c in notify.calls]
    assert 'tg_started' in events_by_type
    assert 'tg_landed' in events_by_type


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_download_uses_default_save_dir_when_no_override(
    anyio_backend: str, watcher: TgDownloadWatcher, watched_chat_repo: TgWatchedChatRepository, tmp_path: pathlib.Path
) -> None:
    _watch_chat(watched_chat_repo)
    client = _client_with_download()
    message = _message(video=_real_video())

    await watcher.handle_message(USER_ID, client, message)

    call_kwargs = client.download_media.call_args.kwargs
    expected_dir = tmp_path / 'bangumi' / 'tg' / USER_ID / '測試頻道'
    assert call_kwargs['file_name'].startswith(str(expected_dir))


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_media_type_not_watched_is_skipped(
    anyio_backend: str, watcher: TgDownloadWatcher, watched_chat_repo: TgWatchedChatRepository
) -> None:
    _watch_chat(watched_chat_repo, media_types=['audio'])
    client = _client_with_download()
    message = _message(video=_real_video())

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_size_below_minimum_is_skipped(
    anyio_backend: str, watcher: TgDownloadWatcher, watched_chat_repo: TgWatchedChatRepository
) -> None:
    _watch_chat(watched_chat_repo, size_min_mb=100)  # video below is 50MB
    client = _client_with_download()
    message = _message(video=_real_video(file_size=50 * 1024 * 1024))

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_size_above_maximum_is_skipped(
    anyio_backend: str, watcher: TgDownloadWatcher, watched_chat_repo: TgWatchedChatRepository
) -> None:
    _watch_chat(watched_chat_repo, size_max_mb=10)
    client = _client_with_download()
    message = _message(video=_real_video(file_size=50 * 1024 * 1024))

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_format_not_in_whitelist_is_skipped(
    anyio_backend: str, watcher: TgDownloadWatcher, watched_chat_repo: TgWatchedChatRepository
) -> None:
    _watch_chat(watched_chat_repo, format_whitelist=['mkv'])
    client = _client_with_download()
    message = _message(video=_real_video(file_name='episode01.mp4'))

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_format_in_whitelist_is_downloaded(
    anyio_backend: str, watcher: TgDownloadWatcher, watched_chat_repo: TgWatchedChatRepository
) -> None:
    _watch_chat(watched_chat_repo, format_whitelist=['mp4', 'mkv'])
    client = _client_with_download()
    message = _message(video=_real_video(file_name='episode01.mp4'))

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_already_downloaded_is_deduped(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    downloaded_media_repo: TgDownloadedMediaRepository,
) -> None:
    _watch_chat(watched_chat_repo)
    downloaded_media_repo.insert_if_new(
        USER_ID,
        chat_id=CHAT_ID,
        message_id=MESSAGE_ID,
        file_id='video-unique-id',
        file_name='episode01.mp4',
        file_size=1,
        local_path='/already/here.mp4',
    )
    client = _client_with_download()
    message = _message(video=_real_video())

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_no_watched_chat_is_noop(anyio_backend: str, watcher: TgDownloadWatcher) -> None:
    client = _client_with_download()
    message = _message(video=_real_video())

    await watcher.handle_message(USER_ID, client, message)  # no watched chat inserted at all

    client.download_media.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_message_with_no_matching_media_is_noop(
    anyio_backend: str, watcher: TgDownloadWatcher, watched_chat_repo: TgWatchedChatRepository
) -> None:
    _watch_chat(watched_chat_repo)
    client = _client_with_download()
    message = _message()  # no video/document/audio/photo at all

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_download_failure_marks_task_history_failed_and_emits_tg_failed(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    downloaded_media_repo: TgDownloadedMediaRepository,
    task_history_repo: TaskHistoryRepository,
    notify: _EventCapture,
) -> None:
    _watch_chat(watched_chat_repo)
    client = _client_with_download(download_result=RuntimeError('network error'))
    message = _message(video=_real_video())

    await watcher.handle_message(USER_ID, client, message)

    assert downloaded_media_repo.exists(USER_ID, CHAT_ID, MESSAGE_ID) is False
    history = task_history_repo.list_recent(days=1, user_id=USER_ID)
    assert len(history) == 1
    assert history[0].final_status == '下載失敗'
    assert 'tg_failed' in [c['event'] for c in notify.calls]


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_progress_callback_updates_progress_bus(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    progress_bus: ProgressBus,
) -> None:
    """Directly drive ``_make_progress_callback`` to confirm ProgressBus.update_stats fires with a live fraction."""
    _watch_chat(watched_chat_repo)
    watched = watched_chat_repo.get(USER_ID, CHAT_ID)
    assert watched is not None
    from app.tg_downloader.downloader import _MatchedMedia

    media = _MatchedMedia(media_type='video', file_id='f', file_unique_id='u', file_name='episode01.mp4', file_size=100)
    message = _message(video=_real_video())
    progress_bus.start(1, 'episode01.mp4', owner_id=USER_ID)

    callback = watcher._make_progress_callback(1, watched, media, message)  # noqa: SLF001 — testing throttle logic directly
    callback(50, 100)

    snapshot = progress_bus.snapshot()
    assert snapshot[1].rate == pytest.approx(0.5)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_download_progress_publishes_rate_speed_eta_to_progress_bus(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    progress_bus: ProgressBus,
) -> None:
    """Two throttled callback invocations (0% then 50%, with a real sleep in
    between so the speed calc sees a nonzero time delta) must publish a live
    rate/speed_mbps/eta_seconds sample to ProgressBus — this is the actual
    fix for the '下載中 0%' permanently-stuck MonitorView symptom: previously
    only ``rate`` was ever written, never speed/ETA."""
    _watch_chat(watched_chat_repo)
    watched = watched_chat_repo.get(USER_ID, CHAT_ID)
    assert watched is not None
    from app.tg_downloader.downloader import _MatchedMedia

    media = _MatchedMedia(media_type='video', file_id='f', file_unique_id='u', file_name='episode01.mp4', file_size=100)
    message = _message(video=_real_video())
    progress_bus.start(1, 'episode01.mp4', owner_id=USER_ID)

    callback = watcher._make_progress_callback(1, watched, media, message)  # noqa: SLF001
    callback(0, 1000)  # first call always emits: rate 0.0, no speed sample yet
    time.sleep(0.02)  # ensure a nonzero time delta for the speed calculation
    callback(500, 1000)  # 50% jump -> emits again, this time with a real speed sample

    snapshot = progress_bus.snapshot()
    assert snapshot[1].rate == pytest.approx(0.5)
    assert snapshot[1].speed_mbps is not None
    assert snapshot[1].speed_mbps > 0
    assert snapshot[1].eta_seconds is not None
    assert snapshot[1].eta_seconds >= 0


def test_finish_progress_uses_force_finish_when_entry_missing(
    watcher: TgDownloadWatcher,
    progress_bus: ProgressBus,
) -> None:
    """Simulates a worker-process restart between ``progress_bus.start()`` and
    download-finish: this real ``ProgressBus`` has never seen sn 1 (its
    in-memory ``_entries`` is empty, exactly as it would be in a freshly
    restarted process), yet ``_finish_progress`` must still land a terminal
    100% entry via ``force_finish`` rather than silently no-op'ing."""
    assert 1 not in progress_bus.snapshot()  # confirm no local entry pre-exists

    watcher._finish_progress(1, status='下載完成', filename='episode01.mp4')  # noqa: SLF001

    snap = progress_bus.snapshot()
    assert 1 in snap
    entry = snap[1]
    assert entry.status == '下載完成'
    assert entry.rate == 1.0
    assert entry.finished_at is not None
    assert entry.filename == 'episode01.mp4'
    assert entry.source == 'tg'


def test_finish_progress_still_finishes_when_entry_exists(
    watcher: TgDownloadWatcher,
    progress_bus: ProgressBus,
) -> None:
    """When the local entry *does* exist (the normal, non-restarted case),
    ``force_finish`` must still produce a full terminal finish — same
    contract as the old ``update_status`` + ``finish`` combo."""
    progress_bus.start(1, 'placeholder.mp4', status='下載中', source='tg')
    assert 1 in progress_bus.snapshot()  # confirm a local entry pre-exists

    watcher._finish_progress(1, status='下載完成', filename='episode01.mp4')  # noqa: SLF001

    snap = progress_bus.snapshot()
    entry = snap[1]
    assert entry.status == '下載完成'
    assert entry.rate == 1.0
    assert entry.finished_at is not None
    assert entry.filename == 'episode01.mp4'
    assert entry.source == 'tg'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_download_progress_throttled_to_5s_or_5pct_jump(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    notify: _EventCapture,
) -> None:
    """Many rapid callback invocations that barely move the percentage (all
    within the same tick, so real elapsed time is ~0s) must collapse to a
    single emission — only the very first callback (last_edit_at is None)
    fires; the 5s/5%-jump throttle (tighter than BT's 5s/10%) suppresses the
    rest. A subsequent call that crosses the 5-point jump threshold fires
    again even with no time elapsed."""
    _watch_chat(watched_chat_repo)
    watched = watched_chat_repo.get(USER_ID, CHAT_ID)
    assert watched is not None
    from app.tg_downloader.downloader import _MatchedMedia

    media = _MatchedMedia(media_type='video', file_id='f', file_unique_id='u', file_name='episode01.mp4', file_size=100)
    message = _message(video=_real_video())

    callback = watcher._make_progress_callback(1, watched, media, message)  # noqa: SLF001

    chunk = 1024  # 1 KiB
    total = 10_000 * chunk  # 100 x 1 KiB chunks stay well under a 5% jump
    for i in range(1, 101):
        callback(i * chunk, total)

    progress_events = [c for c in notify.calls if c['event'] == 'tg_progress']
    assert len(progress_events) == 1

    callback(int(total * 0.06), total)  # jumps straight to 6% -> fires despite no elapsed time
    progress_events = [c for c in notify.calls if c['event'] == 'tg_progress']
    assert len(progress_events) == 2


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_download_progress_exception_does_not_break_download(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    downloaded_media_repo: TgDownloadedMediaRepository,
    progress_bus: ProgressBus,
) -> None:
    """A progress callback that raises (e.g. a broken ProgressBus) must never
    abort the download itself — ``contextlib.suppress(Exception)`` wraps the
    entire callback body, mirroring BT LandingWorker's own guardrail."""
    _watch_chat(watched_chat_repo)

    async def _fake_download_media(_message: object, *, file_name: str, progress: object) -> str:
        # Simulate hydrogram invoking the progress callback mid-download; a
        # broken ProgressBus (patched below) must not propagate from here.
        progress(50, 100)  # type: ignore[operator]
        return file_name

    client = unittest.mock.AsyncMock()
    client.download_media = unittest.mock.AsyncMock(side_effect=_fake_download_media)
    message = _message(video=_real_video())

    with unittest.mock.patch.object(progress_bus, 'update_stats', side_effect=RuntimeError('boom')):
        await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_awaited_once()
    assert downloaded_media_repo.exists(USER_ID, CHAT_ID, MESSAGE_ID) is True


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_register_and_unregister_wire_hydrogram_handler(
    anyio_backend: str, watcher: TgDownloadWatcher, watched_chat_repo: TgWatchedChatRepository
) -> None:
    """Uses a genuine ``hydrogram.Client`` — ``add_handler``/``remove_handler`` are real, unmocked calls."""
    _watch_chat(watched_chat_repo)
    # hydrogram.Client() construction needs a *running* event loop (its
    # Dispatcher calls asyncio.get_event_loop() at __init__ time) — hence
    # this test being async even though register()/unregister() are sync.
    client = hydrogram.Client('test-watcher-client', api_id=1, api_hash='a' * 32, in_memory=True)

    watcher.register(USER_ID, client)
    assert USER_ID in watcher._handlers  # noqa: SLF001

    watcher.unregister(USER_ID, client)
    assert USER_ID not in watcher._handlers  # noqa: SLF001


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_register_with_no_watched_chats_registers_nothing(anyio_backend: str, watcher: TgDownloadWatcher) -> None:
    client = hydrogram.Client('test-watcher-client-2', api_id=1, api_hash='a' * 32, in_memory=True)

    watcher.register(USER_ID, client)

    assert USER_ID not in watcher._handlers  # noqa: SLF001


# ---------------------------------------------------------------------------
# HIGH-1 security fix — save_path landing-root confinement
# ---------------------------------------------------------------------------


def test_save_path_traversal_rejected() -> None:
    """Pydantic-level guard (the static half of HIGH-1's confinement fix) —
    a literal ``..`` path segment is rejected at watched-chat write time
    (422 at the API layer), before it can ever reach the DB or the
    downloader's runtime confine check."""
    with pytest.raises(pydantic.ValidationError, match='save_path'):
        TgWatchedChatCreate(chat_id=CHAT_ID, chat_title='測試頻道', save_path='../../etc')


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_save_path_absolute_outside_root_rejected(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    tmp_path: pathlib.Path,
) -> None:
    """An absolute save_path pointed entirely outside the landing root has no
    ``..`` segment for the Pydantic-level guard to catch — only the runtime
    confine check in TgDownloadWatcher._resolve_save_dir rejects this."""
    outside = tmp_path / 'outside'
    outside.mkdir()
    _watch_chat(watched_chat_repo, save_path=str(outside))
    client = _client_with_download()
    message = _message(video=_real_video())

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_not_awaited()
    assert list(outside.iterdir()) == []


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_save_path_symlink_escape_rejected(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    tmp_path: pathlib.Path,
) -> None:
    """Mirrors test_putio_client.py::test_download_file_rejects_symlink_escape
    — a symlink planted inside the landing root but pointing outside it must
    still be caught, since the confine check resolves the path (following
    symlinks) rather than doing a purely textual '..' check."""
    landing_root = tmp_path / 'bangumi'
    landing_root.mkdir(parents=True, exist_ok=True)
    outside_dir = tmp_path / 'outside'
    outside_dir.mkdir()

    link = landing_root / 'escape_link'
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation not permitted in this environment')

    _watch_chat(watched_chat_repo, save_path=str(link))
    client = _client_with_download()
    message = _message(video=_real_video())

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_not_awaited()
    assert list(outside_dir.iterdir()) == []


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_save_path_null_uses_default_landing_dir(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    tmp_path: pathlib.Path,
) -> None:
    """Regression pin for HIGH-1: with no save_path override, the pre-existing
    default directory (bangumi_dir/tg/<user_id>/<chat_title>) is unchanged —
    same coverage as test_download_uses_default_save_dir_when_no_override,
    pinned explicitly here as part of the landing-root confinement fix."""
    _watch_chat(watched_chat_repo, save_path=None)
    client = _client_with_download()
    message = _message(video=_real_video())

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_awaited_once()
    expected_dir = tmp_path / 'bangumi' / 'tg' / USER_ID / '測試頻道'
    call_kwargs = client.download_media.call_args.kwargs
    assert call_kwargs['file_name'].startswith(str(expected_dir))


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_save_path_inside_root_absolute_is_accepted(
    anyio_backend: str,
    watcher: TgDownloadWatcher,
    watched_chat_repo: TgWatchedChatRepository,
    tmp_path: pathlib.Path,
) -> None:
    """An absolute save_path that legitimately resolves inside the landing
    root (e.g. operator points it at a subfolder of bangumi_dir) must still
    work — the fix confines, it doesn't blanket-reject every absolute path."""
    custom_dir = tmp_path / 'bangumi' / 'custom' / 'subdir'
    _watch_chat(watched_chat_repo, save_path=str(custom_dir))
    client = _client_with_download()
    message = _message(video=_real_video())

    await watcher.handle_message(USER_ID, client, message)

    client.download_media.assert_awaited_once()
    call_kwargs = client.download_media.call_args.kwargs
    assert call_kwargs['file_name'].startswith(str(custom_dir.resolve()))


# ---------------------------------------------------------------------------
# MEDIUM-6 security fix — per-user download concurrency cap
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_concurrent_downloads_capped_per_user(
    anyio_backend: str,
    watched_chat_repo: TgWatchedChatRepository,
    downloaded_media_repo: TgDownloadedMediaRepository,
    task_history_repo: TaskHistoryRepository,
    task_id_map_repo: TaskIdMapRepository,
    progress_bus: ProgressBus,
    notify: _EventCapture,
    tmp_path: pathlib.Path,
) -> None:
    """10 concurrently-matching messages for the same user must never have
    more than the configured concurrency cap (3 here) of
    ``client.download_media`` calls in flight at once."""
    watcher = TgDownloadWatcher(
        watched_chat_repo,
        downloaded_media_repo,
        tmp_path / 'bangumi',
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
        progress_bus=progress_bus,
        notify_event_send=notify,
        max_concurrent_downloads_per_user=3,
    )
    _watch_chat(watched_chat_repo)

    active = 0
    max_active = 0
    state_lock = asyncio.Lock()

    async def _fake_download_media(_message: object, *, file_name: str, progress: object = None) -> str:
        nonlocal active, max_active
        async with state_lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        async with state_lock:
            active -= 1
        return file_name

    client = unittest.mock.AsyncMock()
    client.download_media = unittest.mock.AsyncMock(side_effect=_fake_download_media)

    messages = []
    for i in range(10):
        message = _message(video=_real_video())
        message.id = MESSAGE_ID + i
        messages.append(message)

    await asyncio.gather(*(watcher.handle_message(USER_ID, client, m) for m in messages))

    assert client.download_media.await_count == 10
    assert max_active == 3  # confirms the cap was actually hit, not incidentally low
    assert active == 0  # every acquired slot was released

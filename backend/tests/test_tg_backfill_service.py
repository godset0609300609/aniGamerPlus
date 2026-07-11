"""Tests for ``TgBackfillService`` — historical chat scan, cutoff, filters,
dedup, and progress bookkeeping.

Wired against a real (sqlite-backed) ``TgWatchedChatRepository`` /
``TgDownloadedMediaRepository`` and a real ``TgDownloadWatcher`` (mirroring
``test_tg_downloader.py``'s "genuine filter/dedup/persistence logic" choice)
— only the hydrogram-touching client pool / client are fakes, since a
``get_chat_history`` async generator over a live MTProto connection isn't
worth constructing here.
"""

from __future__ import annotations

import datetime
import pathlib
import types
import unittest.mock

import hydrogram.types
import pytest

from app.logging_ import Logger
from app.models import TgWatchedChatCreate
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths
from app.persistence.tg_downloaded_media_repo import TgDownloadedMediaRepository
from app.persistence.tg_watched_chat_repo import TgWatchedChatRepository
from app.tg_downloader import backfill as backfill_module
from app.tg_downloader.backfill import TgBackfillService
from app.tg_downloader.downloader import TgDownloadWatcher

USER_ID = 'user-1'
CHAT_ID = -1001111111111


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
def downloader(
    watched_chat_repo: TgWatchedChatRepository,
    downloaded_media_repo: TgDownloadedMediaRepository,
    tmp_path: pathlib.Path,
) -> TgDownloadWatcher:
    return TgDownloadWatcher(watched_chat_repo, downloaded_media_repo, tmp_path / 'bangumi')


def _watch_chat(repo: TgWatchedChatRepository, **overrides: object) -> int:
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
    created = repo.insert(USER_ID, TgWatchedChatCreate(**defaults))  # type: ignore[arg-type]
    return created.id


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


def _message(
    message_id: int,
    *,
    date: datetime.datetime,
    video: hydrogram.types.Video | None = None,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(id=CHAT_ID),
        id=message_id,
        date=date,
        video=video,
        document=None,
        audio=None,
        photo=None,
    )


class _FakeHistoryClient:
    """Fake hydrogram client — ``get_chat_history`` yields canned messages, ``download_media`` succeeds."""

    def __init__(self, messages: list[types.SimpleNamespace]) -> None:
        self._messages = messages
        self.download_media = unittest.mock.AsyncMock(return_value='DEST_RESULT_PATH')
        # Warmup call TgBackfillService.run() makes before get_chat_history —
        # see that method's peer-cache-warmup comment. The return value
        # itself is unused by the service, only the call/ordering matters.
        self.get_chat = unittest.mock.AsyncMock(return_value=types.SimpleNamespace(id=0))

    async def get_chat_history(self, chat_id: int, *args: object, **kwargs: object):  # noqa: ANN201, ARG002
        for message in self._messages:
            yield message


class _FakeClientPool:
    def __init__(self, client: _FakeHistoryClient | None) -> None:
        self._client = client
        self.requested_for: list[str] = []

    async def get(self, user_id: str) -> _FakeHistoryClient | None:
        self.requested_for.append(user_id)
        return self._client


def _service(
    client_pool: _FakeClientPool, watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> TgBackfillService:
    return TgBackfillService(client_pool, watched_chat_repo, downloader)  # type: ignore[arg-type]


NOW = datetime.datetime.now()


# ---------------------------------------------------------------------------
# Happy path — scan, match, download, and status transitions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_matching_messages_within_cutoff_are_downloaded_and_marked_done(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    watched_id = _watch_chat(watched_chat_repo)
    messages = [
        _message(1, date=NOW - datetime.timedelta(hours=1), video=_real_video()),
        _message(2, date=NOW - datetime.timedelta(days=2), video=_real_video()),
    ]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run(USER_ID, CHAT_ID, days=7)

    assert client.download_media.await_count == 2
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.backfill_status == 'done'
    assert fetched.backfill_scanned_count == 2
    assert fetched.backfill_matched_count == 2
    assert fetched.backfill_started_at is not None
    assert fetched.backfill_finished_at is not None


@pytest.mark.anyio
async def test_get_chat_called_before_history_to_warm_peer_cache(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    """``run()`` must warm hydrogram's peer cache with a ``get_chat(chat_id)``
    call before walking ``get_chat_history`` — see that call site's comment
    in ``TgBackfillService.run()``. ``in_memory=True`` clients (every client
    ``TgClientPool`` builds) start with an empty peer cache on every process
    restart, so without this warmup a chat this client instance has never
    resolved before would have no cached access_hash for get_chat_history to
    build an InputPeer from."""
    _watch_chat(watched_chat_repo)
    call_order: list[str] = []

    class _OrderTrackingClient:
        async def get_chat(self, chat_id: int) -> types.SimpleNamespace:
            call_order.append('get_chat')
            return types.SimpleNamespace(id=chat_id)

        async def get_chat_history(self, chat_id: int, *args: object, **kwargs: object):  # noqa: ANN201, ARG002
            call_order.append('get_chat_history')
            return
            yield  # pragma: no cover — makes this an async generator function

    client = _OrderTrackingClient()
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)  # type: ignore[arg-type]

    await service.run(USER_ID, CHAT_ID, days=7)

    assert call_order == ['get_chat', 'get_chat_history']


@pytest.mark.anyio
async def test_break_on_cutoff_stops_scan_and_excludes_older_messages(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    """Newest-first ordering — once a message older than the cutoff is hit, scanning stops entirely."""
    watched_id = _watch_chat(watched_chat_repo)
    messages = [
        _message(1, date=NOW - datetime.timedelta(hours=1), video=_real_video()),
        _message(2, date=NOW - datetime.timedelta(days=10), video=_real_video()),  # past the 7-day cutoff
        _message(3, date=NOW - datetime.timedelta(hours=2), video=_real_video()),  # never reached
    ]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run(USER_ID, CHAT_ID, days=7)

    # Only message 1 was scanned before the break on message 2.
    assert client.download_media.await_count == 1
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.backfill_scanned_count == 1
    assert fetched.backfill_matched_count == 1


@pytest.mark.anyio
async def test_filtered_out_message_is_scanned_but_not_matched(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    watched_id = _watch_chat(watched_chat_repo, media_types=['audio'])  # watcher wants audio, message is video
    messages = [_message(1, date=NOW - datetime.timedelta(hours=1), video=_real_video())]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run(USER_ID, CHAT_ID, days=7)

    client.download_media.assert_not_awaited()
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.backfill_scanned_count == 1
    assert fetched.backfill_matched_count == 0
    assert fetched.backfill_status == 'done'


@pytest.mark.anyio
async def test_already_downloaded_message_is_deduped_via_unique_constraint(
    watched_chat_repo: TgWatchedChatRepository,
    downloaded_media_repo: TgDownloadedMediaRepository,
    downloader: TgDownloadWatcher,
) -> None:
    """UNIQUE(user_id, chat_id, message_id) — a message already on record is skipped, not re-downloaded."""
    watched_id = _watch_chat(watched_chat_repo)
    downloaded_media_repo.insert_if_new(
        USER_ID,
        chat_id=CHAT_ID,
        message_id=1,
        file_id='video-unique-id',
        file_name='episode01.mp4',
        file_size=1,
        local_path='/already/here.mp4',
    )
    messages = [_message(1, date=NOW - datetime.timedelta(hours=1), video=_real_video())]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run(USER_ID, CHAT_ID, days=7)

    client.download_media.assert_not_awaited()
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.backfill_scanned_count == 1
    assert fetched.backfill_matched_count == 0


@pytest.mark.anyio
async def test_progress_is_persisted_periodically_during_a_long_scan(
    monkeypatch: pytest.MonkeyPatch, watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    monkeypatch.setattr(backfill_module, '_PROGRESS_UPDATE_EVERY', 3)
    watched_id = _watch_chat(watched_chat_repo)
    messages = [_message(i, date=NOW - datetime.timedelta(hours=i), video=_real_video()) for i in range(1, 8)]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    progress_snapshots: list[tuple[int, int]] = []
    original_mark_progress = watched_chat_repo.mark_backfill_progress

    def _spy_mark_progress(user_id: str, watched_chat_id: int, *, scanned_count: int, matched_count: int) -> None:
        progress_snapshots.append((scanned_count, matched_count))
        original_mark_progress(user_id, watched_chat_id, scanned_count=scanned_count, matched_count=matched_count)

    monkeypatch.setattr(watched_chat_repo, 'mark_backfill_progress', _spy_mark_progress)

    await service.run(USER_ID, CHAT_ID, days=7)

    # Mid-scan updates at 3 and 6, plus the final flush at 7.
    assert (3, 3) in progress_snapshots
    assert (6, 6) in progress_snapshots
    assert (7, 7) in progress_snapshots
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.backfill_scanned_count == 7


# ---------------------------------------------------------------------------
# Guard clauses
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_watched_chat_is_noop(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    client = _FakeHistoryClient([])
    pool = _FakeClientPool(client)
    service = _service(pool, watched_chat_repo, downloader)

    await service.run(USER_ID, CHAT_ID, days=7)  # no watched chat inserted at all

    assert pool.requested_for == []  # never even asked for a client


@pytest.mark.anyio
async def test_disabled_watched_chat_is_noop(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    _watch_chat(watched_chat_repo, enabled=False)
    client = _FakeHistoryClient([])
    pool = _FakeClientPool(client)
    service = _service(pool, watched_chat_repo, downloader)

    await service.run(USER_ID, CHAT_ID, days=7)

    assert pool.requested_for == []


@pytest.mark.anyio
async def test_client_pool_returns_none_marks_failed(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    """Session revoked/expired (client_pool.get returns None) -> fail cleanly, don't raise."""
    watched_id = _watch_chat(watched_chat_repo)
    pool = _FakeClientPool(None)
    service = _service(pool, watched_chat_repo, downloader)

    await service.run(USER_ID, CHAT_ID, days=7)  # must not raise

    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.backfill_status == 'failed'
    assert fetched.backfill_finished_at is not None


@pytest.mark.anyio
async def test_scan_error_marks_failed_and_reraises(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    watched_id = _watch_chat(watched_chat_repo)

    class _BoomClient:
        async def get_chat(self, chat_id: int) -> types.SimpleNamespace:  # noqa: ARG002 — warmup succeeds
            return types.SimpleNamespace(id=chat_id)

        async def get_chat_history(self, chat_id: int, *args: object, **kwargs: object):  # noqa: ANN201, ARG002
            raise RuntimeError('FLOOD_WAIT')
            yield  # pragma: no cover — makes this an async generator function

    service = _service(_FakeClientPool(_BoomClient()), watched_chat_repo, downloader)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match='FLOOD_WAIT'):
        await service.run(USER_ID, CHAT_ID, days=7)

    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.backfill_status == 'failed'

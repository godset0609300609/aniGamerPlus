"""Tests for ``TgCatchupService`` — cursor-based periodic catch-up scan.

Mirrors ``test_tg_backfill_service.py``'s harness exactly (same fake
client/pool shape, same "genuine filter/dedup/persistence logic against a
real sqlite-backed repo, fake only the hydrogram-touching bits" choice) —
see that file's module docstring for why.

``_FakeHistoryClient.get_chat_history`` honours ``offset_id`` the same way
the real hydrogram client does (exclusive — only messages with
``id < offset_id``, see ``catchup.py``'s module docstring for the source-
level proof), since ``TgCatchupService``'s resumable-walk design depends on
that exact semantic.
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
from app.tg_downloader import catchup as catchup_module
from app.tg_downloader.catchup import TgCatchupService
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
    chat_id: int = CHAT_ID,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(id=chat_id),
        id=message_id,
        date=date,
        video=video,
        document=None,
        audio=None,
        photo=None,
    )


class _FakeHistoryClient:
    """Fake hydrogram client — ``get_chat_history`` yields canned messages, ``download_media`` succeeds.

    ``messages`` is public/mutable so tests can simulate new messages
    arriving in the chat between successive ``run_one`` calls (insert at
    index 0 with a higher id — the list is expected to stay newest-first,
    same as real ``get_chat_history`` output).
    """

    def __init__(self, messages: list[types.SimpleNamespace]) -> None:
        self.messages = messages
        self.download_media = unittest.mock.AsyncMock(return_value='DEST_RESULT_PATH')
        self.get_chat = unittest.mock.AsyncMock(return_value=types.SimpleNamespace(id=0))
        #: offset_id passed on each get_chat_history call, in call order — lets tests assert
        #: a resumed run actually used the resume marker instead of re-walking from the top.
        self.history_calls: list[int] = []

    async def get_chat_history(self, chat_id: int, *args: object, **kwargs: object):  # noqa: ANN201, ARG002
        offset_id: int = kwargs.get('offset_id', 0) or 0  # type: ignore[assignment]
        self.history_calls.append(offset_id)
        for message in self.messages:
            # Exclusive boundary — matches real hydrogram/Telegram messages.getHistory
            # semantics (see catchup.py's module docstring for the source-level proof).
            if offset_id and message.id >= offset_id:
                continue
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
) -> TgCatchupService:
    return TgCatchupService(client_pool, watched_chat_repo, downloader)  # type: ignore[arg-type]


NOW = datetime.datetime.now()


# ---------------------------------------------------------------------------
# First run — no cursor yet, falls back to the catchup_hours time cutoff
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_first_run_honours_catchup_hours_cutoff_and_sets_cursor_to_newest(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    watched_id = _watch_chat(watched_chat_repo)
    messages = [
        _message(30, date=NOW - datetime.timedelta(hours=1), video=_real_video()),
        _message(20, date=NOW - datetime.timedelta(hours=2), video=_real_video()),
        _message(10, date=NOW - datetime.timedelta(hours=30), video=_real_video()),  # past the 24h cutoff
    ]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    # Only messages 30 and 20 are within the cutoff.
    assert client.download_media.await_count == 2
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 30
    assert fetched.last_scanned_at is not None


@pytest.mark.anyio
async def test_first_run_with_no_activity_within_cutoff_still_establishes_baseline(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    """Even a run that processes zero messages (everything is older than the cutoff) must
    still record the chat's current newest message id as the cursor baseline — otherwise
    every future tick would redo the exact same (stale) time-window check forever."""
    watched_id = _watch_chat(watched_chat_repo)
    messages = [_message(5, date=NOW - datetime.timedelta(days=10), video=_real_video())]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    client.download_media.assert_not_awaited()
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 5


@pytest.mark.anyio
async def test_first_run_with_empty_history_sets_cursor_to_zero_sentinel(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    """A chat with no history at all still needs a non-None cursor after its first scan —
    otherwise every future tick keeps re-applying the time cutoff instead of switching to
    id-cursor mode (real Telegram message ids are always >= 1, so 0 means 'everything is new')."""
    watched_id = _watch_chat(watched_chat_repo)
    client = _FakeHistoryClient([])
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 0
    assert fetched.last_scanned_at is not None


@pytest.mark.anyio
async def test_get_chat_called_before_history_to_warm_peer_cache(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
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

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    assert call_order == ['get_chat', 'get_chat_history']


# ---------------------------------------------------------------------------
# Second run — cursor already set, stops at last_scanned_message_id
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_second_run_stops_at_cursor_and_does_not_reenqueue_already_scanned_messages(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    watched_id = _watch_chat(watched_chat_repo)
    watched_chat_repo.update_scan_cursor_state(
        USER_ID,
        watched_id,
        last_scanned_message_id=20,
        scan_resume_offset_id=None,
        scan_pending_cursor=None,
        scanned_at='2026-08-01T00:00:00+00:00',
    )
    messages = [
        _message(40, date=NOW - datetime.timedelta(minutes=5), video=_real_video()),
        _message(30, date=NOW - datetime.timedelta(minutes=10), video=_real_video()),
        _message(20, date=NOW - datetime.timedelta(hours=1), video=_real_video()),  # already scanned — must stop here
        _message(10, date=NOW - datetime.timedelta(hours=2), video=_real_video()),  # never reached
    ]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    # Only 40 and 30 are newer than the cursor.
    assert client.download_media.await_count == 2
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 40


@pytest.mark.anyio
async def test_second_run_with_nothing_new_keeps_cursor_but_refreshes_scanned_at(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    watched_id = _watch_chat(watched_chat_repo)
    watched_chat_repo.update_scan_cursor_state(
        USER_ID,
        watched_id,
        last_scanned_message_id=20,
        scan_resume_offset_id=None,
        scan_pending_cursor=None,
        scanned_at='2026-08-01T00:00:00+00:00',
    )
    messages = [_message(20, date=NOW - datetime.timedelta(hours=1), video=_real_video())]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    client.download_media.assert_not_awaited()
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 20  # unchanged
    assert fetched.last_scanned_at != '2026-08-01T00:00:00+00:00'  # refreshed


@pytest.mark.anyio
async def test_filtered_out_message_is_scanned_but_not_downloaded(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    watched_id = _watch_chat(watched_chat_repo, media_types=['audio'])  # watcher wants audio, message is video
    messages = [_message(1, date=NOW - datetime.timedelta(hours=1), video=_real_video())]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    client.download_media.assert_not_awaited()
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 1


@pytest.mark.anyio
async def test_already_downloaded_message_is_deduped_via_unique_constraint(
    watched_chat_repo: TgWatchedChatRepository,
    downloaded_media_repo: TgDownloadedMediaRepository,
    downloader: TgDownloadWatcher,
) -> None:
    _watch_chat(watched_chat_repo)
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

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    client.download_media.assert_not_awaited()


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

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)  # no watched chat inserted at all

    assert pool.requested_for == []


@pytest.mark.anyio
async def test_disabled_watched_chat_is_skipped(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    _watch_chat(watched_chat_repo, enabled=False)
    client = _FakeHistoryClient([])
    pool = _FakeClientPool(client)
    service = _service(pool, watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    assert pool.requested_for == []


@pytest.mark.anyio
async def test_deleted_watched_chat_is_skipped(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    watched_id = _watch_chat(watched_chat_repo)
    watched_chat_repo.delete(USER_ID, watched_id)
    client = _FakeHistoryClient([])
    pool = _FakeClientPool(client)
    service = _service(pool, watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)  # must not raise — chat is gone

    assert pool.requested_for == []


@pytest.mark.anyio
async def test_client_pool_returns_none_leaves_cursor_untouched(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    """Session revoked/expired (client_pool.get returns None) -> log and return, never raise."""
    watched_id = _watch_chat(watched_chat_repo)
    pool = _FakeClientPool(None)
    service = _service(pool, watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)  # must not raise

    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id is None
    assert fetched.last_scanned_at is None


@pytest.mark.anyio
async def test_scan_error_leaves_cursor_untouched_and_does_not_raise(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    watched_id = _watch_chat(watched_chat_repo)
    watched_chat_repo.update_scan_cursor_state(
        USER_ID,
        watched_id,
        last_scanned_message_id=5,
        scan_resume_offset_id=None,
        scan_pending_cursor=None,
        scanned_at='2026-08-01T00:00:00+00:00',
    )

    class _BoomClient:
        async def get_chat(self, chat_id: int) -> types.SimpleNamespace:  # noqa: ARG002 — warmup succeeds
            return types.SimpleNamespace(id=chat_id)

        async def get_chat_history(self, chat_id: int, *args: object, **kwargs: object):  # noqa: ANN201, ARG002
            raise RuntimeError('FLOOD_WAIT')
            yield  # pragma: no cover — makes this an async generator function

    service = _service(_FakeClientPool(_BoomClient()), watched_chat_repo, downloader)  # type: ignore[arg-type]

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)  # must not raise, unlike TgBackfillService.run

    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 5  # unchanged
    assert fetched.last_scanned_at == '2026-08-01T00:00:00+00:00'  # unchanged


# ---------------------------------------------------------------------------
# Per-run scan cap — resumable walk (regression coverage for the livelock bug)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cap_hit_persists_resume_marker_and_pending_cursor_without_advancing_cursor(
    monkeypatch: pytest.MonkeyPatch, watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    monkeypatch.setattr(catchup_module, '_MAX_MESSAGES_PER_SCAN', 3)
    watched_id = _watch_chat(watched_chat_repo)
    # 5 messages, all within the cutoff — the cap (3), not the cutoff, must be what stops the walk.
    messages = [_message(i, date=NOW - datetime.timedelta(minutes=i), video=_real_video()) for i in range(5, 0, -1)]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    assert client.download_media.await_count == 3  # capped, not all 5
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    # last_scanned_message_id must NOT advance — this is a first-ever scan
    # (no prior cursor) with a sweep still in progress, so it must stay
    # None. Coalescing it to any placeholder here is exactly the bug the
    # resume mechanism replaced — see run_one's "Cursor selection" comment.
    assert fetched.last_scanned_message_id is None
    assert fetched.last_scanned_at is not None  # still refreshed for observability

    state = watched_chat_repo.get_scan_cursor_state(USER_ID, watched_id)
    assert state is not None
    assert state.scan_resume_offset_id == 3  # lowest id processed (5, 4, 3 were handled)
    assert state.scan_pending_cursor == 5  # newest id seen when the sweep started


@pytest.mark.anyio
async def test_cap_hit_with_existing_cursor_leaves_it_exactly_unchanged(
    monkeypatch: pytest.MonkeyPatch, watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    monkeypatch.setattr(catchup_module, '_MAX_MESSAGES_PER_SCAN', 2)
    watched_id = _watch_chat(watched_chat_repo)
    watched_chat_repo.update_scan_cursor_state(
        USER_ID,
        watched_id,
        last_scanned_message_id=1,
        scan_resume_offset_id=None,
        scan_pending_cursor=None,
        scanned_at='2026-08-01T00:00:00+00:00',
    )
    messages = [_message(i, date=NOW - datetime.timedelta(minutes=i), video=_real_video()) for i in range(5, 1, -1)]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)

    assert client.download_media.await_count == 2  # capped
    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 1  # the pre-existing cursor, untouched

    state = watched_chat_repo.get_scan_cursor_state(USER_ID, watched_id)
    assert state is not None
    assert state.scan_resume_offset_id == 4  # lowest of {5, 4} processed this run
    assert state.scan_pending_cursor == 5


@pytest.mark.anyio
async def test_resumed_run_uses_offset_id_and_does_not_rewalk_the_processed_range(
    monkeypatch: pytest.MonkeyPatch, watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    monkeypatch.setattr(catchup_module, '_MAX_MESSAGES_PER_SCAN', 3)
    watched_id = _watch_chat(watched_chat_repo)
    messages = [_message(i, date=NOW - datetime.timedelta(minutes=i), video=_real_video()) for i in range(5, 0, -1)]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)  # tick 1: processes 5, 4, 3 (cap hit)
    assert client.download_media.await_count == 3

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)  # tick 2: resumes from offset_id=3

    # get_chat_history must have been called with offset_id=3 the second time — NOT 0/unset
    # (which would re-walk from the top and re-yield 5, 4, 3).
    assert client.history_calls == [0, 3]
    downloaded_ids = sorted(call.args[0].id for call in client.download_media.call_args_list)
    assert downloaded_ids == [1, 2, 3, 4, 5]  # every id exactly once — no duplicates, nothing missing
    assert len(client.download_media.call_args_list) == 5

    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 5  # sweep completed: committed to its original pending cursor
    state = watched_chat_repo.get_scan_cursor_state(USER_ID, watched_id)
    assert state is not None
    assert state.scan_resume_offset_id is None
    assert state.scan_pending_cursor is None


@pytest.mark.anyio
async def test_new_messages_arriving_during_an_in_flight_sweep_are_caught_by_the_next_tick(
    monkeypatch: pytest.MonkeyPatch, watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    """A sweep's scan_pending_cursor is captured once, at the moment the sweep starts.
    Messages that arrive in the chat AFTER that point are not visible to the in-progress
    sweep's resumed (offset_id-bounded) walks — they are picked up by the next REGULAR
    (non-resuming) tick instead, which starts fresh from the chat's actual current top.
    Nothing is silently dropped; it just lands one tick later than messages that existed
    before the sweep began — see catchup.py's module docstring for why."""
    monkeypatch.setattr(catchup_module, '_MAX_MESSAGES_PER_SCAN', 3)
    watched_id = _watch_chat(watched_chat_repo)
    messages = [_message(i, date=NOW - datetime.timedelta(minutes=i), video=_real_video()) for i in range(5, 0, -1)]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)  # tick 1: cap hit, sweep in progress (pending=5)
    assert client.download_media.await_count == 3

    # New messages 6 and 7 arrive in the chat while the sweep is still in progress.
    # Insert in ascending id order so each insert(0, ...) leaves the list newest-first
    # overall (7 must end up ahead of 6, matching real get_chat_history ordering).
    client.messages.insert(0, _message(6, date=NOW + datetime.timedelta(minutes=1), video=_real_video()))
    client.messages.insert(0, _message(7, date=NOW + datetime.timedelta(minutes=1), video=_real_video()))

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)  # tick 2: resumes from offset_id=3, sweep completes

    downloaded_after_tick2 = sorted(call.args[0].id for call in client.download_media.call_args_list)
    assert downloaded_after_tick2 == [1, 2, 3, 4, 5]  # 6 and 7 NOT part of the resumed walk

    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 5  # committed at the sweep's original baseline, not 7

    await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)  # tick 3: fresh, non-resuming walk from the real top

    downloaded_after_tick3 = sorted(call.args[0].id for call in client.download_media.call_args_list)
    assert downloaded_after_tick3 == [1, 2, 3, 4, 5, 6, 7]  # 6 and 7 caught on the following tick

    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == 7


@pytest.mark.anyio
async def test_convergence_after_multiple_capped_runs_downloads_every_message_exactly_once(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    """Regression test for the cap-hit livelock bug: a backlog of
    2 * _MAX_MESSAGES_PER_SCAN + 500 messages must fully converge — every message id
    downloaded exactly once — after enough successive run_one calls, ending with the
    cursor at the newest id and both resume columns cleared. Uses the real,
    unmonkeypatched production cap so this exercises the exact scale that originally
    livelocked (proven via a standalone repro before this fix: flatlined at exactly
    _MAX_MESSAGES_PER_SCAN enqueued forever, 60% of a 2500-message backlog never seen)."""
    total = 2 * catchup_module._MAX_MESSAGES_PER_SCAN + 500
    watched_id = _watch_chat(watched_chat_repo)
    messages = [
        _message(i, date=NOW - datetime.timedelta(seconds=total - i), video=_real_video()) for i in range(total, 0, -1)
    ]
    client = _FakeHistoryClient(messages)
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)

    for _ in range(10):  # generous upper bound — must converge in exactly 3 ticks at this scale
        await service.run_one(USER_ID, CHAT_ID, catchup_hours=24)
        state = watched_chat_repo.get_scan_cursor_state(USER_ID, watched_id)
        assert state is not None
        if state.scan_resume_offset_id is None and state.scan_pending_cursor is None:
            break

    downloaded_ids = sorted(call.args[0].id for call in client.download_media.call_args_list)
    assert downloaded_ids == list(range(1, total + 1))  # every id downloaded exactly once, none missing

    fetched = watched_chat_repo.get_by_id(USER_ID, watched_id)
    assert fetched is not None
    assert fetched.last_scanned_message_id == total
    state = watched_chat_repo.get_scan_cursor_state(USER_ID, watched_id)
    assert state is not None
    assert state.scan_resume_offset_id is None
    assert state.scan_pending_cursor is None


# ---------------------------------------------------------------------------
# run_all() — sweep across chats, per-chat isolation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_all_scans_every_enabled_chat_across_users(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    _watch_chat(watched_chat_repo, chat_id=1)
    _watch_chat(watched_chat_repo, chat_id=2)
    other_repo_created_id = watched_chat_repo.insert(
        'user-2',
        TgWatchedChatCreate(
            chat_id=3,
            chat_title='另一個頻道',
            media_types=['video'],
            enabled=True,
        ),
    ).id

    messages_by_chat = {
        1: [_message(1, date=NOW - datetime.timedelta(minutes=1), video=_real_video(), chat_id=1)],
        2: [_message(1, date=NOW - datetime.timedelta(minutes=1), video=_real_video(), chat_id=2)],
        3: [_message(1, date=NOW - datetime.timedelta(minutes=1), video=_real_video(), chat_id=3)],
    }

    class _MultiChatClient:
        def __init__(self) -> None:
            self.get_chat = unittest.mock.AsyncMock(return_value=types.SimpleNamespace(id=0))
            self.download_media = unittest.mock.AsyncMock(return_value='DEST_RESULT_PATH')

        async def get_chat_history(self, chat_id: int, *args: object, **kwargs: object):  # noqa: ANN201, ARG002
            for message in messages_by_chat[chat_id]:
                yield message

    client = _MultiChatClient()
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)  # type: ignore[arg-type]

    await service.run_all(catchup_hours=24)

    assert client.download_media.await_count == 3
    assert watched_chat_repo.get(USER_ID, 1).last_scanned_message_id == 1  # type: ignore[union-attr]
    assert watched_chat_repo.get(USER_ID, 2).last_scanned_message_id == 1  # type: ignore[union-attr]
    assert watched_chat_repo.get_by_id('user-2', other_repo_created_id).last_scanned_message_id == 1  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_run_all_isolates_a_failing_chat_from_the_rest_of_the_sweep(
    watched_chat_repo: TgWatchedChatRepository, downloader: TgDownloadWatcher
) -> None:
    _watch_chat(watched_chat_repo, chat_id=1)  # will fail
    _watch_chat(watched_chat_repo, chat_id=2)  # must still be scanned

    class _MixedClient:
        def __init__(self) -> None:
            self.get_chat = unittest.mock.AsyncMock(return_value=types.SimpleNamespace(id=0))
            self.download_media = unittest.mock.AsyncMock(return_value='DEST_RESULT_PATH')

        async def get_chat_history(self, chat_id: int, *args: object, **kwargs: object):  # noqa: ANN201, ARG002
            if chat_id == 1:
                raise RuntimeError('FLOOD_WAIT')
                yield  # pragma: no cover — makes this an async generator function
            yield _message(1, date=NOW - datetime.timedelta(minutes=1), video=_real_video(), chat_id=chat_id)

    client = _MixedClient()
    service = _service(_FakeClientPool(client), watched_chat_repo, downloader)  # type: ignore[arg-type]

    await service.run_all(catchup_hours=24)  # must not raise despite chat 1 failing

    assert client.download_media.await_count == 1  # only chat 2's message
    assert watched_chat_repo.get(USER_ID, 1).last_scanned_message_id is None  # type: ignore[union-attr]
    assert watched_chat_repo.get(USER_ID, 2).last_scanned_message_id == 1  # type: ignore[union-attr]

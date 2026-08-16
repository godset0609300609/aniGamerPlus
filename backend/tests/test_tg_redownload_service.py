"""Tests for ``TgRedownloadService`` — the on-demand "強制重新下載" pipeline's
session/chat/message resolution and its explicit failure modes.

Wired against a real (sqlite-backed) ``TgDownloadedMediaRepository`` and a
real ``TgDownloadWatcher`` (mirrors ``test_tg_backfill_service.py``'s
"genuine filter/dedup/persistence logic" choice) — only the
hydrogram-touching client pool / client are fakes.
"""

from __future__ import annotations

import pathlib
import types
import unittest.mock

import hydrogram.enums
import hydrogram.errors
import hydrogram.types
import pytest

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths
from app.persistence.tg_downloaded_media_repo import TgDownloadedMediaRepository
from app.tg_downloader.downloader import TgDownloadWatcher
from app.tg_downloader.redownload import TgRedownloadService

USER_ID = 'user-1'
OTHER_USER_ID = 'user-2'
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
def downloaded_media_repo(database: Database) -> TgDownloadedMediaRepository:
    return TgDownloadedMediaRepository(database)


@pytest.fixture
def downloader(database: Database, downloaded_media_repo: TgDownloadedMediaRepository, tmp_path: pathlib.Path):
    from app.persistence.tg_watched_chat_repo import TgWatchedChatRepository

    return TgDownloadWatcher(TgWatchedChatRepository(database), downloaded_media_repo, tmp_path / 'bangumi')


class _FakeLogger:
    """Captures ``.error()`` calls so tests can assert on the specific,
    user-facing failure-mode message without a real Logger's file I/O."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, sn: object, tag: str, message: str, *, display: bool = True) -> None:  # noqa: ARG002
        self.errors.append(message)

    def info(self, sn: object, tag: str, message: str, *, display: bool = True) -> None:  # noqa: ARG002
        pass


class _FakeClientPool:
    def __init__(self, client: object | None) -> None:
        self._client = client
        self.requested_for: list[str] = []

    async def get(self, user_id: str) -> object | None:
        self.requested_for.append(user_id)
        return self._client


def _existing_entry(repo: TgDownloadedMediaRepository, local_path: pathlib.Path, *, user_id: str = USER_ID):
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(b'old-content')
    entry = repo.insert_if_new(
        user_id,
        chat_id=CHAT_ID,
        message_id=MESSAGE_ID,
        file_id='old-unique-id',
        file_name='episode01.mp4',
        file_size=11,
        local_path=str(local_path),
    )
    assert entry is not None
    return entry


def _real_video() -> hydrogram.types.Video:
    return hydrogram.types.Video(
        file_id='video-file-id',
        file_unique_id='video-unique-id',
        width=1920,
        height=1080,
        duration=1200,
        file_name='episode01.mp4',
        file_size=1234,
    )


def _real_message(*, empty: bool = False, video: hydrogram.types.Video | None = None) -> hydrogram.types.Message:
    chat = hydrogram.types.Chat(id=CHAT_ID, type=hydrogram.enums.ChatType.CHANNEL)
    if empty:
        return hydrogram.types.Message(id=MESSAGE_ID, empty=True)
    return hydrogram.types.Message(id=MESSAGE_ID, chat=chat, video=video)


class _FakeClient:
    """Minimal hydrogram.Client stand-in — get_chat / get_messages / download_media."""

    def __init__(
        self,
        *,
        get_chat_error: Exception | None = None,
        message: hydrogram.types.Message | None = None,
        get_messages_error: Exception | None = None,
        download_content: bytes = b'fresh-content',
    ) -> None:
        self._get_chat_error = get_chat_error
        self._message = message
        self._get_messages_error = get_messages_error
        self.get_chat_calls: list[int] = []
        self.get_messages_calls: list[tuple[int, int]] = []

        async def _download_media(_message: object, *, file_name: str, progress: object = None) -> str:
            path = pathlib.Path(file_name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(download_content)
            return str(path)

        self.download_media = unittest.mock.AsyncMock(side_effect=_download_media)

    async def get_chat(self, chat_id: int) -> object:
        self.get_chat_calls.append(chat_id)
        if self._get_chat_error is not None:
            raise self._get_chat_error
        return types.SimpleNamespace(id=chat_id)

    async def get_messages(self, chat_id: int, message_id: int) -> hydrogram.types.Message | None:
        self.get_messages_calls.append((chat_id, message_id))
        if self._get_messages_error is not None:
            raise self._get_messages_error
        return self._message


def _service(
    client_pool: _FakeClientPool,
    downloaded_media_repo: TgDownloadedMediaRepository,
    downloader: TgDownloadWatcher,
    logger: _FakeLogger,
) -> TgRedownloadService:
    return TgRedownloadService(client_pool, downloaded_media_repo, downloader, logger=logger)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Happy path — genuinely lands the new file via TgDownloadWatcher.force_redownload
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_success_downloads_and_replaces_file(
    downloaded_media_repo: TgDownloadedMediaRepository, downloader: TgDownloadWatcher, tmp_path: pathlib.Path
) -> None:
    local_path = tmp_path / 'bangumi' / 'episode01.mp4'
    entry = _existing_entry(downloaded_media_repo, local_path)
    client = _FakeClient(message=_real_message(video=_real_video()), download_content=b'fresh-content')
    pool = _FakeClientPool(client)
    logger = _FakeLogger()
    service = _service(pool, downloaded_media_repo, downloader, logger)

    await service.run(USER_ID, entry.id)

    assert client.get_chat_calls == [CHAT_ID]
    assert client.get_messages_calls == [(CHAT_ID, MESSAGE_ID)]
    client.download_media.assert_awaited_once()
    assert local_path.read_bytes() == b'fresh-content'
    assert logger.errors == []


# ---------------------------------------------------------------------------
# Failure modes — each logs a specific, actionable message and returns
# quietly (no raise, no dramatiq retry to feed).
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_entry_not_found_logs_and_returns(
    downloaded_media_repo: TgDownloadedMediaRepository, downloader: TgDownloadWatcher
) -> None:
    pool = _FakeClientPool(_FakeClient(message=_real_message(video=_real_video())))
    logger = _FakeLogger()
    service = _service(pool, downloaded_media_repo, downloader, logger)

    await service.run(USER_ID, 999999)  # must not raise

    assert pool.requested_for == []  # never even tried to connect
    assert any('找不到下載紀錄' in msg for msg in logger.errors)


@pytest.mark.anyio
async def test_run_entry_belonging_to_another_user_is_treated_as_not_found(
    downloaded_media_repo: TgDownloadedMediaRepository, downloader: TgDownloadWatcher, tmp_path: pathlib.Path
) -> None:
    """Ownership must already have been enforced at the API layer (404 via
    get_by_id_for_user) — but if this ever ran for a mismatched
    (user_id, entry_id) pair anyway (e.g. a stale dispatch), it must still
    refuse rather than acting on someone else's row."""
    local_path = tmp_path / 'bangumi' / 'episode01.mp4'
    entry = _existing_entry(downloaded_media_repo, local_path, user_id=OTHER_USER_ID)
    pool = _FakeClientPool(_FakeClient(message=_real_message(video=_real_video())))
    logger = _FakeLogger()
    service = _service(pool, downloaded_media_repo, downloader, logger)

    await service.run(USER_ID, entry.id)

    assert pool.requested_for == []
    assert local_path.read_bytes() == b'old-content'  # untouched


@pytest.mark.anyio
async def test_run_session_missing_logs_and_returns(
    downloaded_media_repo: TgDownloadedMediaRepository, downloader: TgDownloadWatcher, tmp_path: pathlib.Path
) -> None:
    local_path = tmp_path / 'bangumi' / 'episode01.mp4'
    entry = _existing_entry(downloaded_media_repo, local_path)
    pool = _FakeClientPool(None)  # no active session
    logger = _FakeLogger()
    service = _service(pool, downloaded_media_repo, downloader, logger)

    await service.run(USER_ID, entry.id)  # must not raise

    assert pool.requested_for == [USER_ID]
    assert any('session 已撤銷或過期' in msg for msg in logger.errors)
    assert local_path.read_bytes() == b'old-content'


@pytest.mark.anyio
async def test_run_chat_inaccessible_logs_and_returns(
    downloaded_media_repo: TgDownloadedMediaRepository, downloader: TgDownloadWatcher, tmp_path: pathlib.Path
) -> None:
    """ChannelPrivate/ChannelInvalid — the account can no longer reach the
    chat (kicked, channel deleted, ...)."""
    local_path = tmp_path / 'bangumi' / 'episode01.mp4'
    entry = _existing_entry(downloaded_media_repo, local_path)
    client = _FakeClient(get_chat_error=hydrogram.errors.ChannelPrivate())
    pool = _FakeClientPool(client)
    logger = _FakeLogger()
    service = _service(pool, downloaded_media_repo, downloader, logger)

    await service.run(USER_ID, entry.id)

    assert client.get_messages_calls == []  # never reached — get_chat raised first
    assert any('聊天已無法存取' in msg for msg in logger.errors)
    assert local_path.read_bytes() == b'old-content'


@pytest.mark.anyio
async def test_run_message_deleted_logs_and_returns(
    downloaded_media_repo: TgDownloadedMediaRepository, downloader: TgDownloadWatcher, tmp_path: pathlib.Path
) -> None:
    """A deleted message comes back from hydrogram as Message(empty=True),
    not None and not an exception."""
    local_path = tmp_path / 'bangumi' / 'episode01.mp4'
    entry = _existing_entry(downloaded_media_repo, local_path)
    client = _FakeClient(message=_real_message(empty=True))
    pool = _FakeClientPool(client)
    logger = _FakeLogger()
    service = _service(pool, downloaded_media_repo, downloader, logger)

    await service.run(USER_ID, entry.id)

    client.download_media.assert_not_awaited()
    assert any('已在 Telegram 中被刪除' in msg for msg in logger.errors)
    assert local_path.read_bytes() == b'old-content'


@pytest.mark.anyio
async def test_run_message_none_logs_and_returns(
    downloaded_media_repo: TgDownloadedMediaRepository, downloader: TgDownloadWatcher, tmp_path: pathlib.Path
) -> None:
    local_path = tmp_path / 'bangumi' / 'episode01.mp4'
    entry = _existing_entry(downloaded_media_repo, local_path)
    client = _FakeClient(message=None)
    pool = _FakeClientPool(client)
    logger = _FakeLogger()
    service = _service(pool, downloaded_media_repo, downloader, logger)

    await service.run(USER_ID, entry.id)

    client.download_media.assert_not_awaited()
    assert any('已在 Telegram 中被刪除' in msg for msg in logger.errors)


@pytest.mark.anyio
async def test_run_generic_rpc_error_logs_and_returns(
    downloaded_media_repo: TgDownloadedMediaRepository, downloader: TgDownloadWatcher, tmp_path: pathlib.Path
) -> None:
    local_path = tmp_path / 'bangumi' / 'episode01.mp4'
    entry = _existing_entry(downloaded_media_repo, local_path)
    client = _FakeClient(get_messages_error=hydrogram.errors.RPCError())
    pool = _FakeClientPool(client)
    logger = _FakeLogger()
    service = _service(pool, downloaded_media_repo, downloader, logger)

    await service.run(USER_ID, entry.id)  # must not raise

    client.download_media.assert_not_awaited()
    assert any('Telegram 錯誤' in msg for msg in logger.errors)


@pytest.mark.anyio
async def test_run_unexpected_exception_logs_and_returns(
    downloaded_media_repo: TgDownloadedMediaRepository, downloader: TgDownloadWatcher, tmp_path: pathlib.Path
) -> None:
    local_path = tmp_path / 'bangumi' / 'episode01.mp4'
    entry = _existing_entry(downloaded_media_repo, local_path)
    client = _FakeClient(get_chat_error=ValueError('boom'))
    pool = _FakeClientPool(client)
    logger = _FakeLogger()
    service = _service(pool, downloaded_media_repo, downloader, logger)

    await service.run(USER_ID, entry.id)  # must not raise

    assert any('未預期錯誤' in msg for msg in logger.errors)

"""Tests for ``TgClientPool`` — lazy per-user connect, caching, disconnect,
and mark-expired-on-reconnect-failure.
"""

from __future__ import annotations

import pathlib
import unittest.mock

import pytest

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths
from app.persistence.tg_session_repo import TgSessionRepository
from app.tg_downloader.client_pool import TgClientPool


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
def session_repo(database: Database) -> TgSessionRepository:
    return TgSessionRepository(database)


def _fake_client() -> unittest.mock.AsyncMock:
    client = unittest.mock.AsyncMock()
    client.connect = unittest.mock.AsyncMock(return_value=True)
    client.disconnect = unittest.mock.AsyncMock()
    return client


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_get_returns_none_when_no_session(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    pool = TgClientPool(1, 'hash', session_repo)

    client = await pool.get('user-1')

    assert client is None
    assert pool.is_connected('user-1') is False


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_get_connects_and_caches_client(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    session_repo.upsert('user-1', session_string='s', phone_tail4=None, telegram_user_id=None)
    fake_client = _fake_client()
    factory = unittest.mock.Mock(return_value=fake_client)
    pool = TgClientPool(1, 'hash', session_repo, client_factory=factory)

    result1 = await pool.get('user-1')
    result2 = await pool.get('user-1')

    assert result1 is fake_client
    assert result2 is fake_client  # cached — factory + connect only called once
    factory.assert_called_once()
    fake_client.connect.assert_awaited_once()
    assert pool.is_connected('user-1') is True
    assert pool.connected_user_ids() == ['user-1']


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_get_touches_last_active_on_connect(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    entry = session_repo.upsert('user-1', session_string='s', phone_tail4=None, telegram_user_id=None)
    original = entry.last_active_at
    fake_client = _fake_client()
    pool = TgClientPool(1, 'hash', session_repo, client_factory=lambda **kw: fake_client)

    await pool.get('user-1')

    updated = session_repo.get_by_user_id('user-1')
    assert updated is not None
    assert updated.last_active_at is not None
    assert original is not None
    assert updated.last_active_at >= original


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_get_marks_expired_on_connect_failure(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    session_repo.upsert('user-1', session_string='s', phone_tail4=None, telegram_user_id=None)
    fake_client = _fake_client()
    fake_client.connect.side_effect = RuntimeError('AUTH_KEY_UNREGISTERED')
    pool = TgClientPool(1, 'hash', session_repo, client_factory=lambda **kw: fake_client)

    result = await pool.get('user-1')

    assert result is None
    assert pool.is_connected('user-1') is False
    entry = session_repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.status == 'expired'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_disconnect_removes_from_cache_and_calls_client_disconnect(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    session_repo.upsert('user-1', session_string='s', phone_tail4=None, telegram_user_id=None)
    fake_client = _fake_client()
    pool = TgClientPool(1, 'hash', session_repo, client_factory=lambda **kw: fake_client)
    await pool.get('user-1')

    await pool.disconnect('user-1')

    fake_client.disconnect.assert_awaited_once()
    assert pool.is_connected('user-1') is False


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_disconnect_unknown_user_is_noop(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    pool = TgClientPool(1, 'hash', session_repo)

    await pool.disconnect('nobody')  # must not raise


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_disconnect_all_disconnects_every_client(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    session_repo.upsert('user-1', session_string='a', phone_tail4=None, telegram_user_id=None)
    session_repo.upsert('user-2', session_string='b', phone_tail4=None, telegram_user_id=None)
    clients = {'user-1': _fake_client(), 'user-2': _fake_client()}
    pool = TgClientPool(1, 'hash', session_repo, client_factory=lambda **kw: clients[kw['name'].removeprefix('tg-')])

    await pool.get('user-1')
    await pool.get('user-2')
    await pool.disconnect_all()

    clients['user-1'].disconnect.assert_awaited_once()
    clients['user-2'].disconnect.assert_awaited_once()
    assert pool.connected_user_ids() == []


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_disconnect_client_failure_is_swallowed(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    session_repo.upsert('user-1', session_string='s', phone_tail4=None, telegram_user_id=None)
    fake_client = _fake_client()
    fake_client.disconnect.side_effect = RuntimeError('boom')
    pool = TgClientPool(1, 'hash', session_repo, client_factory=lambda **kw: fake_client)
    await pool.get('user-1')

    await pool.disconnect('user-1')  # must not raise despite disconnect() failing

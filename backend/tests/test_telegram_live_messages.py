"""Direct unit tests for LiveMessageRegistry."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.services.telegram_live_messages import LiveMessageRegistry


@pytest.fixture
def client() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def registry(client: fakeredis.aioredis.FakeRedis) -> LiveMessageRegistry:
    return LiveMessageRegistry(client)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_set_then_get_returns_tuple(anyio_backend: str, registry: LiveMessageRegistry) -> None:
    await registry.set(1, 100, message_id=200, last_edit_at=1234.5, last_rate=0.42)
    out = await registry.get(1, 100)
    assert out == (200, 1234.5, 0.42)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_get_missing_returns_none(anyio_backend: str, registry: LiveMessageRegistry) -> None:
    assert await registry.get(99, 99) is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_clear_removes_entry(anyio_backend: str, registry: LiveMessageRegistry) -> None:
    await registry.set(1, 100, message_id=200, last_edit_at=10.0, last_rate=0.5)
    await registry.clear(1, 100)
    assert await registry.get(1, 100) is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_distinct_chat_ids_isolated(anyio_backend: str, registry: LiveMessageRegistry) -> None:
    await registry.set(1, 100, message_id=10, last_edit_at=1.0, last_rate=0.1)
    await registry.set(1, 200, message_id=20, last_edit_at=2.0, last_rate=0.2)
    assert (await registry.get(1, 100)) == (10, 1.0, 0.1)
    assert (await registry.get(1, 200)) == (20, 2.0, 0.2)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_distinct_sn_isolated(anyio_backend: str, registry: LiveMessageRegistry) -> None:
    await registry.set(1, 100, message_id=10, last_edit_at=1.0, last_rate=0.1)
    await registry.set(2, 100, message_id=20, last_edit_at=2.0, last_rate=0.2)
    assert (await registry.get(1, 100)) == (10, 1.0, 0.1)
    assert (await registry.get(2, 100)) == (20, 2.0, 0.2)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_set_overwrites_existing(anyio_backend: str, registry: LiveMessageRegistry) -> None:
    await registry.set(1, 100, message_id=10, last_edit_at=1.0, last_rate=0.1)
    await registry.set(1, 100, message_id=10, last_edit_at=99.9, last_rate=0.9)
    assert await registry.get(1, 100) == (10, 99.9, 0.9)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_list_for_sn_returns_all_chats(anyio_backend: str, registry: LiveMessageRegistry) -> None:
    await registry.set(1, 100, message_id=10, last_edit_at=1.0, last_rate=0.1)
    await registry.set(1, 200, message_id=20, last_edit_at=2.0, last_rate=0.2)
    await registry.set(2, 300, message_id=30, last_edit_at=3.0, last_rate=0.3)
    out = sorted(await registry.list_for_sn(1))
    assert out == [(100, 10, 1.0, 0.1), (200, 20, 2.0, 0.2)]


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_list_for_sn_empty_when_no_entries(anyio_backend: str, registry: LiveMessageRegistry) -> None:
    assert await registry.list_for_sn(42) == []


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_ttl_is_set(
    anyio_backend: str, registry: LiveMessageRegistry, client: fakeredis.aioredis.FakeRedis
) -> None:
    """Mutation testing: catches if expire() call is dropped."""
    await registry.set(1, 100, message_id=200, last_edit_at=1.0, last_rate=0.5)
    ttl = await client.ttl('tgmsg:1:100')
    assert ttl > 0
    assert ttl <= 24 * 60 * 60

"""Direct unit tests for LiveMenuRegistry."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.services.telegram_live_menu import LiveMenuRegistry


@pytest.fixture
def client() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def registry(client: fakeredis.aioredis.FakeRedis) -> LiveMenuRegistry:
    return LiveMenuRegistry(client)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_set_then_get_round_trip(anyio_backend: str, registry: LiveMenuRegistry) -> None:
    await registry.set('user-1', 12345)
    assert await registry.get('user-1') == 12345


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_get_missing_user_returns_none(anyio_backend: str, registry: LiveMenuRegistry) -> None:
    assert await registry.get('nobody') is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_clear_removes_entry(anyio_backend: str, registry: LiveMenuRegistry) -> None:
    await registry.set('u', 1)
    await registry.clear('u')
    assert await registry.get('u') is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_set_overwrites_previous(anyio_backend: str, registry: LiveMenuRegistry) -> None:
    await registry.set('u', 1)
    await registry.set('u', 2)
    assert await registry.get('u') == 2


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_corrupt_value_returns_none(
    anyio_backend: str, client: fakeredis.aioredis.FakeRedis, registry: LiveMenuRegistry
) -> None:
    """If something else writes a non-int value to the key, get() must not crash."""
    await client.set('menu_msg:weird', 'not-an-int')
    assert await registry.get('weird') is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_ttl_is_set(
    anyio_backend: str, registry: LiveMenuRegistry, client: fakeredis.aioredis.FakeRedis
) -> None:
    """Mutation testing: catches if TTL is dropped."""
    await registry.set('u', 1)
    ttl = await client.ttl('menu_msg:u')
    assert ttl > 0
    assert ttl <= 7 * 24 * 60 * 60

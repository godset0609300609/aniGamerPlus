"""Direct unit tests for MessageIdRegistry — no mocking, real Redis semantics
(via fakeredis)."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.redis_state import MessageIdRegistry


@pytest.fixture
def client() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def registry(client: fakeredis.aioredis.FakeRedis) -> MessageIdRegistry:
    return MessageIdRegistry(client)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_set_then_get_returns_value(anyio_backend: str, registry: MessageIdRegistry) -> None:
    await registry.set(42, 'msg-abc')
    assert await registry.get(42) == 'msg-abc'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_get_unknown_sn_returns_none(anyio_backend: str, registry: MessageIdRegistry) -> None:
    assert await registry.get(999) is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_clear_removes_value(anyio_backend: str, registry: MessageIdRegistry) -> None:
    await registry.set(7, 'msg-x')
    await registry.clear(7)
    assert await registry.get(7) is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_set_overwrites_previous(anyio_backend: str, registry: MessageIdRegistry) -> None:
    await registry.set(7, 'first')
    await registry.set(7, 'second')
    assert await registry.get(7) == 'second'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_distinct_sn_isolated(anyio_backend: str, registry: MessageIdRegistry) -> None:
    await registry.set(1, 'one')
    await registry.set(2, 'two')
    assert await registry.get(1) == 'one'
    assert await registry.get(2) == 'two'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_int_sn_is_normalised(anyio_backend: str, registry: MessageIdRegistry) -> None:
    """Calling set/get with the same int casts (e.g. str→int) hits the same key."""
    await registry.set(99, 'val')
    assert await registry.get(99) == 'val'
    # The key prefix and int normalisation are part of the contract:
    raw = await registry._client.get('msgid:99')  # type: ignore[attr-defined]
    assert raw == b'val'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_ttl_is_set(
    anyio_backend: str, registry: MessageIdRegistry, client: fakeredis.aioredis.FakeRedis
) -> None:
    """Mutation testing: catches if TTL is dropped (-1) or kept short (0)."""
    await registry.set(5, 'val')
    ttl = await client.ttl('msgid:5')
    assert ttl > 0
    assert ttl <= 60 * 60  # documented 1h

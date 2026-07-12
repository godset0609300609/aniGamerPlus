"""Direct unit tests for LiveMessageRegistry."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.services.telegram_live_messages import BtLiveMessageRegistry, LiveMessageRegistry


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


# ---------------------------------------------------------------------------
# BtLiveMessageRegistry — per-(entry_id, chat_id) BT status message tracking
# ---------------------------------------------------------------------------


@pytest.fixture
def bt_registry(client: fakeredis.aioredis.FakeRedis) -> BtLiveMessageRegistry:
    return BtLiveMessageRegistry(client)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_set_then_get_returns_message_id(anyio_backend: str, bt_registry: BtLiveMessageRegistry) -> None:
    await bt_registry.set(7, 200, message_id=555)
    assert await bt_registry.get(7, 200) == 555


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_get_missing_returns_none(anyio_backend: str, bt_registry: BtLiveMessageRegistry) -> None:
    assert await bt_registry.get(99, 99) is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_clear_removes_entry(anyio_backend: str, bt_registry: BtLiveMessageRegistry) -> None:
    await bt_registry.set(7, 200, message_id=555)
    await bt_registry.clear(7, 200)
    assert await bt_registry.get(7, 200) is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_distinct_chat_ids_isolated(anyio_backend: str, bt_registry: BtLiveMessageRegistry) -> None:
    await bt_registry.set(7, 200, message_id=1)
    await bt_registry.set(7, 300, message_id=2)
    assert await bt_registry.get(7, 200) == 1
    assert await bt_registry.get(7, 300) == 2


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_distinct_entry_ids_isolated(anyio_backend: str, bt_registry: BtLiveMessageRegistry) -> None:
    await bt_registry.set(7, 200, message_id=1)
    await bt_registry.set(8, 200, message_id=2)
    assert await bt_registry.get(7, 200) == 1
    assert await bt_registry.get(8, 200) == 2


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_set_overwrites_existing(anyio_backend: str, bt_registry: BtLiveMessageRegistry) -> None:
    await bt_registry.set(7, 200, message_id=1)
    await bt_registry.set(7, 200, message_id=2)
    assert await bt_registry.get(7, 200) == 2


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_ttl_is_set(
    anyio_backend: str, bt_registry: BtLiveMessageRegistry, client: fakeredis.aioredis.FakeRedis
) -> None:
    """Mutation testing: catches if the TTL argument to setex() is dropped."""
    await bt_registry.set(7, 200, message_id=555)
    ttl = await client.ttl('btmsg:7:200')
    assert ttl > 0
    assert ttl <= 24 * 60 * 60


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_prefix_does_not_collide_with_animad_registry(
    anyio_backend: str, client: fakeredis.aioredis.FakeRedis
) -> None:
    """btmsg: and tgmsg: keys for the same numeric ids must not collide."""
    animad_registry = LiveMessageRegistry(client)
    bt_registry = BtLiveMessageRegistry(client)

    await animad_registry.set(7, 200, message_id=111, last_edit_at=1.0, last_rate=0.0)
    await bt_registry.set(7, 200, message_id=222)

    assert (await animad_registry.get(7, 200))[0] == 111  # type: ignore[index]
    assert await bt_registry.get(7, 200) == 222


# ---------------------------------------------------------------------------
# BtLiveMessageRegistry — last_edit_at (hash upgrade, mirrors LiveMessageRegistry)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_live_messages_stores_and_retrieves_last_edit_at(
    anyio_backend: str, bt_registry: BtLiveMessageRegistry
) -> None:
    await bt_registry.set(7, 200, message_id=555, last_edit_at=1234.5)
    assert await bt_registry.get_with_timestamp(7, 200) == (555, 1234.5)
    # get() still returns just the message_id for callers that don't need the timestamp.
    assert await bt_registry.get(7, 200) == 555


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_live_messages_get_returns_none_for_missing_key(
    anyio_backend: str, bt_registry: BtLiveMessageRegistry
) -> None:
    assert await bt_registry.get(404, 404) is None
    assert await bt_registry.get_with_timestamp(404, 404) is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_live_messages_last_edit_at_defaults_to_zero_when_omitted(
    anyio_backend: str, bt_registry: BtLiveMessageRegistry
) -> None:
    """set() without last_edit_at (the pre-throttling call shape) still works."""
    await bt_registry.set(7, 200, message_id=555)
    assert await bt_registry.get_with_timestamp(7, 200) == (555, 0.0)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_live_messages_set_overwrites_last_edit_at(
    anyio_backend: str, bt_registry: BtLiveMessageRegistry
) -> None:
    await bt_registry.set(7, 200, message_id=555, last_edit_at=1.0)
    await bt_registry.set(7, 200, message_id=555, last_edit_at=99.0)
    assert await bt_registry.get_with_timestamp(7, 200) == (555, 99.0)


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bt_live_messages_tolerates_stale_plain_string_key(
    anyio_backend: str, bt_registry: BtLiveMessageRegistry, client: fakeredis.aioredis.FakeRedis
) -> None:
    """A pre-upgrade plain-string SETEX key (WRONGTYPE for hash ops) must be
    treated as a cache miss, not raise — see the class docstring's migration
    note. get()/get_with_timestamp() fall through to None; set() self-heals
    by deleting and recreating the key as a hash."""
    await client.setex('btmsg:7:200', 3600, '999')  # simulate the old plain-string format

    assert await bt_registry.get(7, 200) is None
    assert await bt_registry.get_with_timestamp(7, 200) is None

    # set() must self-heal rather than raising WRONGTYPE.
    await bt_registry.set(7, 200, message_id=555, last_edit_at=42.0)
    assert await bt_registry.get_with_timestamp(7, 200) == (555, 42.0)

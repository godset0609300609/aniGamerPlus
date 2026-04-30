"""Tests for telegram_health_monitor — disk-low, cookie-expired, cooldown, mute.

No real Redis or Telegram connections.  Redis is faked with an in-memory
dict; send_message_actor.send_with_options is monkeypatched.
"""

from __future__ import annotations

import asyncio
import collections.abc
import datetime
import shutil
import types
import unittest.mock

import pytest

from app.downloader.progress import TaskProgress
from app.models import AppSettings, TelegramSettings
from app.persistence.user_repo import UserRow

# ---------------------------------------------------------------------------
# Shared event-loop helpers (avoids ResourceWarning on Windows)
# ---------------------------------------------------------------------------

_LOOP: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _LOOP  # noqa: PLW0603
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP


@pytest.fixture(autouse=True, scope='module')
def _close_loop_after_module() -> collections.abc.Generator[None]:
    yield
    global _LOOP  # noqa: PLW0603
    if _LOOP is not None and not _LOOP.is_closed():
        _LOOP.close()
    _LOOP = None


def _run(coro: collections.abc.Coroutine[object, object, object]) -> object:
    return _get_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fake Redis client (in-memory)
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal async Redis stand-in for cooldown keys."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, int]] = {}  # key -> (value, ttl_seconds)

    async def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = (value, ttl)

    def has_key(self, key: str) -> bool:
        return key in self._store


# ---------------------------------------------------------------------------
# Fake progress reader
# ---------------------------------------------------------------------------


class FakeProgressReader:
    def __init__(self, snap: dict[int, TaskProgress]) -> None:
        self._snap = snap

    async def snapshot(self) -> dict[int, TaskProgress]:
        return self._snap


# ---------------------------------------------------------------------------
# Fake user repo
# ---------------------------------------------------------------------------


class FakeUserRepo:
    def __init__(self) -> None:
        self._users: list[UserRow] = []

    def add(
        self,
        uid: str,
        *,
        role: str = 'admin',
        chat_id: int | None = 100,
        notify: bool = True,
        mute_until: datetime.datetime | None = None,
    ) -> None:
        self._users.append(
            UserRow(
                id=uid,
                username=f'user_{uid}',
                avatar_url=None,
                role=role,
                created_at=datetime.datetime.now(datetime.UTC),
                last_login_at=None,
                telegram_chat_id=chat_id,
                telegram_notify_enabled=notify,
                telegram_mute_until=mute_until,
            )
        )

    def list_all(self) -> list[UserRow]:
        return list(self._users)


# ---------------------------------------------------------------------------
# Actor spy
# ---------------------------------------------------------------------------

_send_with_options_calls: list[dict[str, object]] = []


@pytest.fixture(autouse=True)
def _reset_actor_spy(monkeypatch: pytest.MonkeyPatch) -> collections.abc.Generator[None]:
    _send_with_options_calls.clear()

    from app.tasks import telegram as tg_tasks

    def _spy(**kwargs: object) -> None:
        _send_with_options_calls.append(kwargs)

    monkeypatch.setattr(tg_tasks.send_message_actor, 'send_with_options', _spy)
    yield


# ---------------------------------------------------------------------------
# Container builder helper
# ---------------------------------------------------------------------------


def _make_container(
    *,
    redis: FakeRedis,
    progress_snap: dict[int, TaskProgress] | None = None,
    users: list[UserRow] | None = None,
    bangumi_dir: str = '/data/bangumi',
    tg_enabled: bool = True,
    health_alerts: bool = True,
    bot_token: str = 'tok',
) -> object:
    settings = AppSettings(bangumi_dir=bangumi_dir)
    settings.telegram = TelegramSettings(enabled=tg_enabled, bot_token=bot_token, health_alerts=health_alerts)

    class _FakeSettingsRepo:
        def load(self) -> AppSettings:
            return settings

    user_repo = FakeUserRepo()
    for u in (users or []):
        user_repo._users.append(u)

    return types.SimpleNamespace(
        redis_client_async=redis,
        redis_progress_reader=FakeProgressReader(progress_snap or {}),
        settings_repo=_FakeSettingsRepo(),
        user_repo=user_repo,
    )


async def _run_tick(container: object) -> None:
    from app.services.telegram_health_monitor import health_check_tick

    with unittest.mock.patch('app.services.telegram_health_monitor.build_container', return_value=container):
        # Call the raw coroutine directly — avoids needing the AsyncIO middleware event loop thread.
        await health_check_tick.fn.__wrapped__()


# ---------------------------------------------------------------------------
# Disk-low tests
# ---------------------------------------------------------------------------


_10GIB = 10 * 1024**3


def test_disk_free_above_threshold_no_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sufficient disk space → no admin DM."""
    redis = FakeRedis()
    container = _make_container(redis=redis)

    _patch_disk_free(monkeypatch, free_bytes=20 * _10GIB)
    _run(_run_tick(container))

    assert _send_with_options_calls == []


def _patch_disk_free(monkeypatch: pytest.MonkeyPatch, *, free_bytes: int) -> None:
    """Stub ``shutil.disk_usage`` to return a controlled free-space value.

    Wrapped so the test bodies stay under the 120-char ruff line limit.
    """
    def _stub(_path: object) -> object:
        total = 100 * _10GIB
        return shutil._ntuple_diskusage(total=total, used=total - free_bytes, free=free_bytes)
    monkeypatch.setattr(shutil, 'disk_usage', _stub)


def test_disk_free_below_threshold_sends_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disk free < 10 GiB → DM fires."""
    redis = FakeRedis()
    admin = UserRow(
        id='a1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
        telegram_chat_id=100,
        telegram_notify_enabled=True,
        telegram_mute_until=None,
    )
    container = _make_container(redis=redis, users=[admin])

    _patch_disk_free(monkeypatch, free_bytes=5 * 1024**3)
    _run(_run_tick(container))

    assert len(_send_with_options_calls) == 1
    assert '磁碟' in str(_send_with_options_calls[0]['kwargs'])  # type: ignore[index]


def test_disk_cooldown_prevents_second_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second tick within cooldown window → no second DM."""
    redis = FakeRedis()
    admin = UserRow(
        id='a1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
        telegram_chat_id=100,
        telegram_notify_enabled=True,
        telegram_mute_until=None,
    )
    container = _make_container(redis=redis, users=[admin])

    _patch_disk_free(monkeypatch, free_bytes=5 * 1024**3)
    _run(_run_tick(container))
    first_count = len(_send_with_options_calls)

    # Second tick — cooldown key is now set in FakeRedis
    _run(_run_tick(container))

    assert len(_send_with_options_calls) == first_count  # no new call


# ---------------------------------------------------------------------------
# Cookie-expired tests
# ---------------------------------------------------------------------------


def _failed_entry(sn: int, retries: int = 3) -> TaskProgress:
    return TaskProgress(sn=sn, rate=0.0, status='失敗', filename='f.mp4', retries=retries)


def test_zero_failed_sn_no_dm() -> None:
    redis = FakeRedis()
    admin = UserRow(
        id='a1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
        telegram_chat_id=100,
        telegram_notify_enabled=True,
        telegram_mute_until=None,
    )
    container = _make_container(redis=redis, progress_snap={}, users=[admin])
    _run(_run_tick(container))
    assert _send_with_options_calls == []


def test_two_failed_sn_no_dm() -> None:
    """2 failed sn (< threshold of 3) → no DM."""
    redis = FakeRedis()
    snap = {1: _failed_entry(1), 2: _failed_entry(2)}
    admin = UserRow(
        id='a1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
        telegram_chat_id=100,
        telegram_notify_enabled=True,
        telegram_mute_until=None,
    )
    container = _make_container(redis=redis, progress_snap=snap, users=[admin])
    _run(_run_tick(container))
    assert _send_with_options_calls == []


def test_three_failed_sn_sends_dm() -> None:
    """3+ failed sn (>= threshold) → DM fires."""
    redis = FakeRedis()
    snap = {1: _failed_entry(1), 2: _failed_entry(2), 3: _failed_entry(3)}
    admin = UserRow(
        id='a1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
        telegram_chat_id=100,
        telegram_notify_enabled=True,
        telegram_mute_until=None,
    )
    container = _make_container(redis=redis, progress_snap=snap, users=[admin])
    _run(_run_tick(container))
    assert len(_send_with_options_calls) == 1
    assert 'Cookie' in str(_send_with_options_calls[0]['kwargs'])  # type: ignore[index]


def test_cookie_cooldown_prevents_second_dm() -> None:
    """Cooldown blocks second DM after first fires."""
    redis = FakeRedis()
    snap = {1: _failed_entry(1), 2: _failed_entry(2), 3: _failed_entry(3)}
    admin = UserRow(
        id='a1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
        telegram_chat_id=100,
        telegram_notify_enabled=True,
        telegram_mute_until=None,
    )
    container = _make_container(redis=redis, progress_snap=snap, users=[admin])
    _run(_run_tick(container))
    first = len(_send_with_options_calls)

    _run(_run_tick(container))
    assert len(_send_with_options_calls) == first


def test_retries_below_3_not_counted() -> None:
    """Failed with retries < 3 should not count toward cookie threshold."""
    redis = FakeRedis()
    snap = {
        1: _failed_entry(1, retries=2),
        2: _failed_entry(2, retries=1),
        3: _failed_entry(3, retries=0),
    }
    admin = UserRow(
        id='a1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
        telegram_chat_id=100,
        telegram_notify_enabled=True,
        telegram_mute_until=None,
    )
    container = _make_container(redis=redis, progress_snap=snap, users=[admin])
    _run(_run_tick(container))
    assert _send_with_options_calls == []


# ---------------------------------------------------------------------------
# Muted admin tests
# ---------------------------------------------------------------------------


def test_muted_admin_not_dmed() -> None:
    """A muted admin should not receive health alert DMs."""
    redis = FakeRedis()
    snap = {1: _failed_entry(1), 2: _failed_entry(2), 3: _failed_entry(3)}
    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
    muted_admin = UserRow(
        id='a1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
        telegram_chat_id=100,
        telegram_notify_enabled=True,
        telegram_mute_until=future,
    )
    container = _make_container(redis=redis, progress_snap=snap, users=[muted_admin])
    _run(_run_tick(container))
    assert _send_with_options_calls == []


# ---------------------------------------------------------------------------
# health_alerts=False test
# ---------------------------------------------------------------------------


def test_health_alerts_false_no_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    """When health_alerts=False, no DMs regardless of trigger conditions."""
    redis = FakeRedis()
    snap = {1: _failed_entry(1), 2: _failed_entry(2), 3: _failed_entry(3)}
    admin = UserRow(
        id='a1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
        telegram_chat_id=100,
        telegram_notify_enabled=True,
        telegram_mute_until=None,
    )
    container = _make_container(redis=redis, progress_snap=snap, users=[admin], health_alerts=False)

    _patch_disk_free(monkeypatch, free_bytes=5 * 1024**3)
    _run(_run_tick(container))

    assert _send_with_options_calls == []


# ---------------------------------------------------------------------------
# Non-admin users not DMed
# ---------------------------------------------------------------------------


def test_non_admin_user_not_dmed() -> None:
    """Users with role != 'admin' should never receive health alerts."""
    redis = FakeRedis()
    snap = {1: _failed_entry(1), 2: _failed_entry(2), 3: _failed_entry(3)}
    downloader = UserRow(
        id='u1',
        username='downloader',
        avatar_url=None,
        role='downloader',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
        telegram_chat_id=200,
        telegram_notify_enabled=True,
        telegram_mute_until=None,
    )
    container = _make_container(redis=redis, progress_snap=snap, users=[downloader])
    _run(_run_tick(container))
    assert _send_with_options_calls == []

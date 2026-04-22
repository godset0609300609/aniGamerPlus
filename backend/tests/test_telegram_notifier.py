"""Tests for :class:`TelegramNotifier`."""

from __future__ import annotations

import asyncio
import collections.abc
import datetime
import pathlib

import pytest

from app.logging_ import Logger
from app.models import TelegramSettings
from app.persistence.user_repo import UserRow
from app.services.telegram_client import (
    TelegramBotBlockedError,
    TelegramChatNotFoundError,
)
from app.services.telegram_notifier import TelegramNotifier, _format_message

# ---------------------------------------------------------------------------
# Event-loop management
# ---------------------------------------------------------------------------
# Use one shared event loop for the whole module.  asyncio.run() creates a
# new ProactorEventLoop on Windows per call; each loop opens internal
# self-pipe sockets that aren't immediately released on GC, causing
# PytestUnraisableExceptionWarning in later tests.  A single, explicitly
# closed loop avoids the leak.

_LOOP: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _LOOP  # noqa: PLW0603
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP


@pytest.fixture(autouse=True, scope='module')
def _close_loop_after_module() -> collections.abc.Generator[None]:
    """Close the shared event loop at end of this test module."""
    yield
    global _LOOP  # noqa: PLW0603
    if _LOOP is not None and not _LOOP.is_closed():
        _LOOP.close()
    _LOOP = None


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTelegramClient:
    """Records send_message calls; optionally raises on the nth call."""

    def __init__(self, raise_on: type[Exception] | None = None) -> None:
        self.calls: list[tuple[int, str]] = []
        self._raise_on = raise_on

    async def send_message(self, chat_id: int, text: str, **_kwargs: object) -> dict[str, object]:
        if self._raise_on is not None:
            raise self._raise_on(403, 'error', 403)
        self.calls.append((chat_id, text))
        return {}


class FakeUserRepo:
    """Minimal UserRepository stand-in with in-memory rows."""

    def __init__(self) -> None:
        self._users: dict[str, object] = {}
        self.cleared: list[str] = []

    def add(
        self,
        uid: str,
        *,
        role: str = 'downloader',
        chat_id: int | None = None,
        notify: bool = True,
    ) -> None:
        self._users[uid] = UserRow(
            id=uid,
            username=f'user_{uid}',
            avatar_url=None,
            role=role,
            created_at=datetime.datetime.now(datetime.UTC),
            last_login_at=None,
            telegram_chat_id=chat_id,
            telegram_notify_enabled=notify,
        )

    def get(self, uid: str) -> object | None:
        return self._users.get(uid)

    def list_all(self) -> list[object]:
        return list(self._users.values())

    def clear_telegram_binding(self, uid: str) -> None:
        self.cleared.append(uid)


def _notifier(
    client: FakeTelegramClient,
    repo: FakeUserRepo,
    settings: TelegramSettings,
    tmp_path: pathlib.Path,
) -> TelegramNotifier:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    return TelegramNotifier(
        client=client,  # type: ignore[arg-type]
        user_repo=repo,  # type: ignore[arg-type]
        settings=settings,
        logger=logger,
    )


def _settings(**kwargs: object) -> TelegramSettings:
    base: dict[str, object] = {
        'enabled': True,
        'bot_token': 'tok',
        'notify_on': ['completed', 'failed', 'cancelled'],
        'admin_broadcast': True,
    }
    base.update(kwargs)
    return TelegramSettings(**base)  # type: ignore[arg-type]


def _run(coro: collections.abc.Coroutine[object, object, object]) -> object:
    return _get_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_format_completed_includes_all_fields() -> None:
    msg = _format_message(
        event='completed',
        bangumi_name='進擊的巨人',
        episode='01',
        resolution='1080',
        file_size_mb=500,
        error_message=None,
        episode_number=1,
    )
    assert '下載完成' in msg
    assert '進擊的巨人' in msg
    assert '1' in msg
    assert '1080' in msg
    assert '500' in msg


def test_format_completed_omits_none_fields() -> None:
    msg = _format_message(
        event='completed',
        bangumi_name='Test',
        episode=None,
        resolution=None,
        file_size_mb=None,
        error_message=None,
    )
    assert '解析度' not in msg
    assert '檔案大小' not in msg


def test_format_failed_includes_reason() -> None:
    msg = _format_message(
        event='failed',
        bangumi_name='Test',
        episode='02',
        resolution=None,
        file_size_mb=None,
        error_message='network timeout',
        episode_number=2,
    )
    assert '下載失敗' in msg
    assert 'network timeout' in msg


def test_format_cancelled() -> None:
    msg = _format_message(
        event='cancelled',
        bangumi_name='Test',
        episode='03',
        resolution=None,
        file_size_mb=None,
        error_message=None,
        episode_number=3,
    )
    assert '下載取消' in msg
    assert '3' in msg


def test_format_escapes_markdown_special_chars() -> None:
    msg = _format_message(
        event='completed',
        bangumi_name='進_擊*的[巨人]',
        episode='01',
        resolution='1080',
        file_size_mb=100,
        error_message=None,
        episode_number=1,
    )
    # Underscores and asterisks must be escaped with backslash.
    assert '\\_' in msg
    assert '\\*' in msg
    assert '\\[' in msg


# ---------------------------------------------------------------------------
# New name-line format tests
# ---------------------------------------------------------------------------


def test_format_name_line_with_all_fields() -> None:
    """Completed event with all fields: renders 第 N 季 - 第 M 集."""
    msg = _format_message(
        event='completed',
        bangumi_name='某番',
        episode='05',
        resolution='1080',
        file_size_mb=300,
        error_message=None,
        custom_name='我的名字',
        season=2,
        episode_number=5,
    )
    # custom_name takes priority over bangumi_name.
    assert '我的名字' in msg
    assert '某番' not in msg
    # Season and episode formatted correctly (- is escaped to \-).
    assert '第 2 季' in msg
    assert '第 5 集' in msg
    assert '\\-' in msg


def test_format_name_line_no_custom_name_falls_back_to_bangumi() -> None:
    """When custom_name is None, bangumi_name is used."""
    msg = _format_message(
        event='completed',
        bangumi_name='進擊的巨人',
        episode='10',
        resolution=None,
        file_size_mb=None,
        error_message=None,
        custom_name=None,
        season=1,
        episode_number=10,
    )
    assert '進擊的巨人' in msg
    assert '第 1 季' in msg
    assert '第 10 集' in msg


def test_format_name_line_episode_number_none_uses_raw_episode() -> None:
    """episode_number=None (SP/OVA) — raw episode string used, no 第 N 集 wrapper."""
    msg = _format_message(
        event='completed',
        bangumi_name='某番',
        episode='SP1',
        resolution=None,
        file_size_mb=None,
        error_message=None,
        custom_name=None,
        season=1,
        episode_number=None,
    )
    assert '某番' in msg
    assert '第 1 季' in msg
    assert 'SP1' in msg
    # Should NOT have 第 N 集 wrapper.
    assert '第 SP1 集' not in msg


def test_format_name_line_default_season_1() -> None:
    """When season is omitted it defaults to 1."""
    msg = _format_message(
        event='completed',
        bangumi_name='Test',
        episode='03',
        resolution=None,
        file_size_mb=None,
        error_message=None,
        episode_number=3,
    )
    assert '第 1 季' in msg
    assert '第 3 集' in msg


# ---------------------------------------------------------------------------
# Routing / de-dup
# ---------------------------------------------------------------------------


def test_sends_to_owner_and_admins_deduped(tmp_path: pathlib.Path) -> None:
    """Owner + two admins; owner is NOT an admin — three messages total."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)
    repo.add('admin1', role='admin', chat_id=200)
    repo.add('admin2', role='admin', chat_id=300)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=1,
            file_size_mb=100,
        )
    )
    assert len(client.calls) == 3
    sent_ids = {cid for cid, _ in client.calls}
    assert sent_ids == {100, 200, 300}


def test_owner_is_admin_deduped(tmp_path: pathlib.Path) -> None:
    """Owner is also an admin → only ONE message."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('adminowner', role='admin', chat_id=100)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='adminowner',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=2,
            file_size_mb=50,
        )
    )
    assert len(client.calls) == 1


def test_owner_id_none_still_sends_to_admins(tmp_path: pathlib.Path) -> None:
    """owner_id=None → skip owner DM; admins still get notified."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('admin1', role='admin', chat_id=200)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='failed',
            owner_id=None,
            bangumi_name='Auto',
            episode=None,
            resolution=None,
            sn=3,
            error_message='timeout',
        )
    )
    assert len(client.calls) == 1
    assert client.calls[0][0] == 200


def test_skips_when_disabled(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)

    n = _notifier(client, repo, _settings(enabled=False), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=4,
            file_size_mb=100,
        )
    )
    assert client.calls == []


def test_skips_when_event_not_in_notify_on(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)

    n = _notifier(client, repo, _settings(notify_on=['failed']), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=5,
            file_size_mb=100,
        )
    )
    assert client.calls == []


def test_skips_owner_without_chat_id(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=None)  # no binding

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=6,
            file_size_mb=10,
        )
    )
    assert client.calls == []


def test_skips_owner_with_notify_disabled(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100, notify=False)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=7,
            file_size_mb=10,
        )
    )
    assert client.calls == []


def test_admin_broadcast_false_skips_admins(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)
    repo.add('admin1', role='admin', chat_id=200)

    n = _notifier(client, repo, _settings(admin_broadcast=False), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=8,
            file_size_mb=10,
        )
    )
    # Only owner, no admin.
    assert len(client.calls) == 1
    assert client.calls[0][0] == 100


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_blocked_error_clears_binding(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient(raise_on=TelegramBotBlockedError)
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=9,
            file_size_mb=10,
        )
    )
    assert 'owner' in repo.cleared


def test_chat_not_found_clears_binding(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient(raise_on=TelegramChatNotFoundError)
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=10,
            file_size_mb=10,
        )
    )
    assert 'owner' in repo.cleared


def test_other_exception_is_swallowed(tmp_path: pathlib.Path) -> None:
    """Any non-binding error must be swallowed — never propagate."""

    class _Boom(Exception):
        def __init__(self, *_: object) -> None:
            super().__init__('boom')

    client = FakeTelegramClient(raise_on=_Boom)  # type: ignore[arg-type]
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)

    n = _notifier(client, repo, _settings(), tmp_path)
    # Should not raise.
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            sn=11,
            file_size_mb=10,
        )
    )
    # Binding not cleared for non-403 errors.
    assert repo.cleared == []

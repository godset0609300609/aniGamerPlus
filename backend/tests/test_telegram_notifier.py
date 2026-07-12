"""Tests for the rewritten :class:`TelegramNotifier`.

The notifier now supports 'started', 'completed', 'failed', 'cancelled',
and 'auto_enqueue' events.  Terminal events (completed/failed/cancelled)
use ``edit_message_actor`` when a live-message record exists, and fall back
to ``send_message_actor`` otherwise.  All actor dispatches are tested via a
stub dramatiq broker so no real Redis or Telegram connections are made.
"""

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
    TelegramApiError,
    TelegramBotBlockedError,
    TelegramChatNotFoundError,
)
from app.services.telegram_notifier import TelegramNotifier, _format_bt_message, _format_message

# ---------------------------------------------------------------------------
# Event-loop management
# ---------------------------------------------------------------------------
# Use one shared event loop for the whole module to avoid ResourceWarning
# leaks from ProactorEventLoop self-pipe sockets on Windows.

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
# Actor send spy fixture
# ---------------------------------------------------------------------------
# Instead of installing a real StubBroker (which has issues with actors
# registered on the RedisBroker at import time), we monkeypatch the actor
# .send() methods on the imported actors so we can capture calls without
# hitting Redis.

_actor_calls: list[tuple[str, tuple, dict]] = []


@pytest.fixture(autouse=True)
def _reset_actor_calls(monkeypatch: pytest.MonkeyPatch) -> collections.abc.Generator[None]:
    """Capture actor .send() calls and reset between tests."""
    _actor_calls.clear()

    from app.tasks import telegram as tg_tasks

    def _make_spy(name: str) -> collections.abc.Callable[..., None]:
        def _spy(*args: object, **kwargs: object) -> None:
            _actor_calls.append((name, args, kwargs))

        return _spy

    monkeypatch.setattr(tg_tasks.send_message_actor, 'send', _make_spy('send_message_actor'))
    monkeypatch.setattr(tg_tasks.edit_message_actor, 'send', _make_spy('edit_message_actor'))
    monkeypatch.setattr(tg_tasks.delete_message_actor, 'send', _make_spy('delete_message_actor'))
    yield


def _actor_names() -> list[str]:
    return [name for name, _, _ in _actor_calls]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTelegramClient:
    """Records send_message / edit_message_text calls; optionally raises."""

    def __init__(self, raise_on: type[Exception] | None = None) -> None:
        self.send_calls: list[tuple[int, str]] = []
        self.edit_calls: list[tuple[int, int, str]] = []
        self._raise_on = raise_on
        # Default returned message_id for send_message.
        self.next_message_id: int = 42

    async def send_message(self, chat_id: int, text: str, **_kwargs: object) -> dict[str, object]:
        if self._raise_on is not None:
            raise self._raise_on(403, 'error', 403)
        self.send_calls.append((chat_id, text))
        mid = self.next_message_id
        self.next_message_id += 1
        return {'message_id': mid}

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **_kwargs: object) -> dict[str, object]:
        if self._raise_on is not None:
            raise self._raise_on(403, 'error', 403)
        self.edit_calls.append((chat_id, message_id, text))
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
        mute_until: datetime.datetime | None = None,
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
            telegram_mute_until=mute_until,
        )

    def get(self, uid: str) -> object | None:
        return self._users.get(uid)

    def list_all(self) -> list[object]:
        return list(self._users.values())

    def clear_telegram_binding(self, uid: str) -> None:
        self.cleared.append(uid)


class FakeLiveMessages:
    """In-memory LiveMessageRegistry stand-in."""

    def __init__(self) -> None:
        self._store: dict[tuple[int, int], tuple[int, float, float]] = {}
        self.cleared: list[tuple[int, int]] = []

    async def set(
        self,
        sn: int,
        chat_id: int,
        *,
        message_id: int,
        last_edit_at: float,
        last_rate: float,
    ) -> None:
        self._store[(sn, chat_id)] = (message_id, last_edit_at, last_rate)

    async def get(self, sn: int, chat_id: int) -> tuple[int, float, float] | None:
        return self._store.get((sn, chat_id))

    async def clear(self, sn: int, chat_id: int) -> None:
        self._store.pop((sn, chat_id), None)
        self.cleared.append((sn, chat_id))


class FakeBtLiveMessages:
    """In-memory BtLiveMessageRegistry stand-in."""

    def __init__(self) -> None:
        self._store: dict[tuple[int, int], int] = {}
        self._last_edit_at: dict[tuple[int, int], float] = {}
        self.cleared: list[tuple[int, int]] = []

    async def set(self, entry_id: int, chat_id: int, *, message_id: int, last_edit_at: float | None = None) -> None:
        self._store[(entry_id, chat_id)] = message_id
        self._last_edit_at[(entry_id, chat_id)] = last_edit_at if last_edit_at is not None else 0.0

    async def get(self, entry_id: int, chat_id: int) -> int | None:
        return self._store.get((entry_id, chat_id))

    async def get_with_timestamp(self, entry_id: int, chat_id: int) -> tuple[int, float] | None:
        message_id = self._store.get((entry_id, chat_id))
        if message_id is None:
            return None
        return (message_id, self._last_edit_at.get((entry_id, chat_id), 0.0))

    async def clear(self, entry_id: int, chat_id: int) -> None:
        self._store.pop((entry_id, chat_id), None)
        self._last_edit_at.pop((entry_id, chat_id), None)
        self.cleared.append((entry_id, chat_id))


def _settings(**kwargs: object) -> TelegramSettings:
    base: dict[str, object] = {
        'enabled': True,
        'bot_token': 'tok',
        'notify_on': ['started', 'completed', 'failed', 'cancelled', 'auto_enqueue'],
        'admin_broadcast': True,
    }
    base.update(kwargs)
    return TelegramSettings(**base)  # type: ignore[arg-type]


def _notifier(
    client: FakeTelegramClient,
    repo: FakeUserRepo,
    settings: TelegramSettings,
    tmp_path: pathlib.Path,
    live_messages: FakeLiveMessages | None = None,
    bt_live_messages: FakeBtLiveMessages | None = None,
) -> TelegramNotifier:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    return TelegramNotifier(
        client=client,  # type: ignore[arg-type]
        user_repo=repo,  # type: ignore[arg-type]
        settings_provider=lambda: settings,
        live_messages=live_messages,  # type: ignore[arg-type]
        bt_live_messages=bt_live_messages,  # type: ignore[arg-type]
        logger=logger,
    )


# ---------------------------------------------------------------------------
# Formatting tests — unchanged from old test file
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


def test_format_started() -> None:
    msg = _format_message(
        event='started',
        bangumi_name='某番',
        episode='01',
        resolution='1080',
        file_size_mb=None,
        error_message=None,
        episode_number=1,
    )
    assert '下載中' in msg
    assert '某番' in msg


def test_format_auto_enqueue() -> None:
    msg = _format_message(
        event='auto_enqueue',
        bangumi_name='某番',
        episode='02',
        resolution=None,
        file_size_mb=None,
        error_message=None,
        episode_number=2,
    )
    assert '新集數加入佇列' in msg
    assert '某番' in msg


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
    assert '\\_' in msg
    assert '\\*' in msg
    assert '\\[' in msg


def test_format_name_line_with_all_fields() -> None:
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
    assert '我的名字' in msg
    assert '某番' not in msg
    assert '第 2 季' in msg
    assert '第 5 集' in msg
    assert '\\-' in msg


def test_format_name_line_no_custom_name_falls_back_to_bangumi() -> None:
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
    assert '第 SP1 集' not in msg


def test_format_name_line_default_season_1() -> None:
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
# BT downloader event formatting — animad-style header + name_line + metadata
# ---------------------------------------------------------------------------


def test_format_bt_dispatched() -> None:
    msg = _format_bt_message(
        event='bt_dispatched',
        title='Some Show - 01',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        putio_status=None,
        local_path=None,
        error_message=None,
    )
    assert msg.startswith('📥 *送出 Put\\.io*')
    assert '\\[BT\\]' not in msg  # the old '[BT] {title}' prefix is gone
    assert 'Some Show \\- 01' in msg
    assert '過濾器' in msg
    assert 'my\\-filter' in msg
    assert '來源' in msg
    assert 'my\\-feed' in msg


def test_bt_dispatched_message_uses_new_animad_style_header() -> None:
    """Header, blank line, title, then key:value metadata — mirrors _format_message."""
    msg = _format_bt_message(
        event='bt_dispatched',
        title='Some Show - 01',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        putio_status=None,
        local_path=None,
        error_message=None,
    )
    lines = msg.split('\n')
    assert lines[0] == '📥 *送出 Put\\.io*'
    assert lines[1] == ''
    assert lines[2] == 'Some Show \\- 01'
    assert lines[3] == '過濾器: my\\-filter'
    assert lines[4] == '來源: my\\-feed'


def test_format_bt_status_update_in_queue_header() -> None:
    msg = _format_bt_message(
        event='bt_status_update',
        title='Some Show - 02',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        putio_status='IN_QUEUE',
        local_path=None,
        error_message=None,
    )
    assert msg.startswith('⏳ *Put\\.io 排隊中*')


def test_bt_status_update_downloading_shows_percent_done_from_putio() -> None:
    msg = _format_bt_message(
        event='bt_status_update',
        title='Some Show - 02',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        putio_status='DOWNLOADING',
        local_path=None,
        error_message=None,
        percent_done=42,
    )
    assert msg.startswith('⬇️ *Put\\.io 下載中*')
    assert 'Put\\.io 進度: 42%' in msg


def test_format_bt_status_update_downloading_omits_percent_when_zero() -> None:
    msg = _format_bt_message(
        event='bt_status_update',
        title='Some Show - 02',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        putio_status='DOWNLOADING',
        local_path=None,
        error_message=None,
        percent_done=0,
    )
    assert '進度' not in msg


def test_format_bt_status_update_completed_header_and_size() -> None:
    msg = _format_bt_message(
        event='bt_status_update',
        title='Some Show - 02b',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        putio_status='COMPLETED',
        local_path=None,
        error_message=None,
        file_size_mb=250,
    )
    assert msg.startswith('📦 *Put\\.io 完成，準備落地*')
    assert '檔案大小: 250 MB' in msg


def test_format_bt_status_update_seeding_header_and_size() -> None:
    msg = _format_bt_message(
        event='bt_status_update',
        title='Some Show - 02c',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        putio_status='SEEDING',
        local_path=None,
        error_message=None,
        file_size_mb=250,
    )
    assert msg.startswith('📦 *Put\\.io Seeding，準備落地*')
    assert '檔案大小: 250 MB' in msg


def test_format_bt_status_update_unknown_status_falls_back_to_raw() -> None:
    msg = _format_bt_message(
        event='bt_status_update',
        title='Some Show - 02d',
        feed_name='my-feed',
        filter_name=None,
        putio_transfer_id=None,
        putio_status='WEIRD_NEW_STATUS',
        local_path=None,
        error_message=None,
    )
    assert 'WEIRD\\_NEW\\_STATUS' in msg


def test_bt_landing_progress_shows_percent_and_mb() -> None:
    msg = _format_bt_message(
        event='bt_landing_progress',
        title='Some Show - 06',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        local_path=None,
        error_message=None,
        bytes_written=50 * 1024 * 1024,
        total_bytes=200 * 1024 * 1024,
    )
    assert msg.startswith('⏬ *落地中*')
    assert '落地進度: 50/200 MB \\(25%\\)' in msg


def test_bt_landing_progress_zero_total_bytes_renders_zero_percent() -> None:
    """Defensive: total_bytes=0/None (e.g. missing Content-Length) must not divide by zero."""
    msg = _format_bt_message(
        event='bt_landing_progress',
        title='Some Show - 06b',
        feed_name='my-feed',
        filter_name=None,
        putio_transfer_id=None,
        local_path=None,
        error_message=None,
        bytes_written=1024 * 1024,
        total_bytes=0,
    )
    assert '落地進度: 1/0 MB \\(0%\\)' in msg


def test_format_bt_landed() -> None:
    msg = _format_bt_message(
        event='bt_landed',
        title='Some Show - 03',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        putio_status=None,
        local_path='Some Show - 03.mp4',
        error_message=None,
    )
    assert msg.startswith('✅ *下載完成*')
    assert '落地路徑' in msg
    assert 'Some Show \\- 03\\.mp4' in msg


def test_bt_landed_includes_resolution_when_parseable_from_title() -> None:
    msg = _format_bt_message(
        event='bt_landed',
        title='[Group] Some Show - 05 [1080p][BIG5]',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        local_path='Some Show - 05.mp4',
        error_message=None,
    )
    assert '解析度: 1080p' in msg


def test_bt_landed_omits_resolution_when_unparseable() -> None:
    msg = _format_bt_message(
        event='bt_landed',
        title='[Group] Some Show - 05 [BIG5]',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=123,
        local_path='Some Show - 05.mp4',
        error_message=None,
    )
    assert '解析度' not in msg


def test_bt_landed_resolution_4k_is_uppercased() -> None:
    msg = _format_bt_message(
        event='bt_landed',
        title='[Group] Some Show - 05 [4k][BIG5]',
        feed_name='my-feed',
        filter_name=None,
        putio_transfer_id=None,
        local_path='Some Show - 05.mp4',
        error_message=None,
    )
    assert '解析度: 4K' in msg


def test_bt_landed_includes_file_size_when_known() -> None:
    msg = _format_bt_message(
        event='bt_landed',
        title='Some Show - 05',
        feed_name='my-feed',
        filter_name=None,
        putio_transfer_id=None,
        local_path='Some Show - 05.mp4',
        error_message=None,
        file_size_mb=401,
    )
    assert '檔案大小: 401 MB' in msg


def test_format_bt_failed_includes_reason_and_filter() -> None:
    msg = _format_bt_message(
        event='bt_failed',
        title='Some Show - 04',
        feed_name='my-feed',
        filter_name='my-filter',
        putio_transfer_id=None,
        putio_status=None,
        local_path=None,
        error_message='Put.io token rejected (401)',
    )
    assert msg.startswith('❌ *下載失敗*')
    assert '原因' in msg
    assert '過濾器' in msg
    assert 'my\\-filter' in msg
    assert 'token rejected' in msg


def test_format_bt_failed_omits_filter_line_when_none() -> None:
    msg = _format_bt_message(
        event='bt_failed',
        title='Some Show - 05',
        feed_name='my-feed',
        filter_name=None,
        putio_transfer_id=None,
        putio_status=None,
        local_path=None,
        error_message='boom',
    )
    assert '過濾器' not in msg


def test_format_bt_escapes_markdown_special_chars() -> None:
    msg = _format_bt_message(
        event='bt_dispatched',
        title='進_擊*的[巨人]',
        feed_name='feed',
        filter_name=None,
        putio_transfer_id=None,
        putio_status=None,
        local_path=None,
        error_message=None,
    )
    assert '\\_' in msg
    assert '\\*' in msg
    assert '\\[' in msg


# ---------------------------------------------------------------------------
# BT downloader event routing — admin-only, no owner
# ---------------------------------------------------------------------------


def test_notify_bt_event_sends_to_admins_only(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)  # non-admin — must NOT receive a BT DM
    repo.add('admin1', role='admin', chat_id=200)
    repo.add('admin2', role='admin', chat_id=300)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_bt_event(
            event='bt_failed',
            title='Some Show - 01',
            feed_name='my-feed',
            filter_name='my-filter',
            error_message='boom',
            entry_id=1,
        )
    )

    send_calls = [c for c in _actor_calls if c[0] == 'send_message_actor']
    assert len(send_calls) == 2
    chat_ids = {c[1][0] for c in send_calls}
    assert chat_ids == {200, 300}


def test_notify_bt_event_skips_when_disabled(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('admin1', role='admin', chat_id=200)

    n = _notifier(client, repo, _settings(enabled=False), tmp_path)
    _run(
        n.notify_bt_event(
            event='bt_landed',
            title='Some Show - 01',
            feed_name='my-feed',
            filter_name='my-filter',
            local_path='Some Show - 01.mp4',
            entry_id=1,
        )
    )

    assert _actor_calls == []
    assert client.send_calls == []


def test_notify_bt_event_not_gated_by_notify_on(tmp_path: pathlib.Path) -> None:
    """BT events fire even when 'bt_dispatched' etc. aren't in notify_on —
    that list only enumerates per-download owner events (see notify_bt_event's
    docstring)."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('admin1', role='admin', chat_id=200)

    n = _notifier(client, repo, _settings(notify_on=['failed']), tmp_path)
    _run(
        n.notify_bt_event(
            event='bt_failed',
            title='Some Show - 01',
            feed_name='my-feed',
            filter_name='my-filter',
            error_message='boom',
            entry_id=1,
        )
    )

    send_calls = [c for c in _actor_calls if c[0] == 'send_message_actor']
    assert len(send_calls) == 1


def test_notify_bt_event_no_admins_bound_sends_nothing(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_bt_event(
            event='bt_failed',
            title='Some Show - 01',
            feed_name='my-feed',
            filter_name='my-filter',
            error_message='boom',
            entry_id=1,
        )
    )

    assert _actor_calls == []


# ---------------------------------------------------------------------------
# C-2 (security audit) — TG User API downloader event routing (stub)
# ---------------------------------------------------------------------------


def test_notify_tg_event_is_a_clean_no_op(tmp_path: pathlib.Path) -> None:
    """notify_tg_event must never raise and must never dispatch any DM —
    it's a logging-only stub until a real TG live-message registry exists
    (see its docstring's TODO)."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)
    repo.add('admin1', role='admin', chat_id=200)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_tg_event(
            event='tg_landed',
            chat_title='測試頻道',
            chat_id=-100999,
            message_id=42,
            file_name='ep01.mp4',
            file_size=100,
            local_path='/data/tg/ep01.mp4',
        )
    )

    assert _actor_calls == []
    assert client.send_calls == []


def test_notify_event_actor_dispatch_table_routes_tg_events_to_notify_tg_event() -> None:
    """Wiring check for the notify_event_actor dispatch table itself — a TG
    event must land on notify_tg_event, not notify_download_event (which
    requires sn/bangumi_name the TG payload never carries and would raise
    TypeError — the exact bug this fix addresses). Exercises the actual
    dispatch branches directly rather than through
    ``notify_event_actor`` itself, which — being a dramatiq async actor —
    requires the AsyncIO middleware's event-loop thread to invoke at all
    (not set up in this unit-test process)."""
    from app.tasks import telegram as tg_tasks

    assert 'tg_failed' not in tg_tasks._BT_EVENTS
    assert 'tg_failed' in tg_tasks._TG_EVENTS
    for bt_event in tg_tasks._BT_EVENTS:
        assert bt_event not in tg_tasks._TG_EVENTS
    for tg_event in tg_tasks._TG_EVENTS:
        assert tg_event not in tg_tasks._BT_EVENTS


# ---------------------------------------------------------------------------
# BT lifecycle — in-place message editing (mirrors 'started'/terminal below)
# ---------------------------------------------------------------------------


def test_bt_dispatched_creates_new_message_and_registers(tmp_path: pathlib.Path) -> None:
    """'bt_dispatched' must send via direct client (not actor) and store message_id."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('admin1', role='admin', chat_id=200)
    bt_live = FakeBtLiveMessages()

    n = _notifier(client, repo, _settings(), tmp_path, bt_live_messages=bt_live)
    _run(
        n.notify_bt_event(
            event='bt_dispatched',
            title='Some Show - 01',
            feed_name='my-feed',
            filter_name='my-filter',
            putio_transfer_id=123,
            entry_id=7,
        )
    )

    assert len(client.send_calls) == 1
    assert client.send_calls[0][0] == 200
    assert bt_live._store[(7, 200)] == 42  # FakeTelegramClient's next_message_id starts at 42
    assert _actor_calls == []


def test_bt_status_update_edits_registered_message(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('admin1', role='admin', chat_id=200)
    bt_live = FakeBtLiveMessages()
    _run(bt_live.set(7, 200, message_id=555))

    n = _notifier(client, repo, _settings(), tmp_path, bt_live_messages=bt_live)
    _run(
        n.notify_bt_event(
            event='bt_status_update',
            title='Some Show - 01',
            feed_name='my-feed',
            filter_name='my-filter',
            putio_status='DOWNLOADING',
            entry_id=7,
        )
    )

    edit_calls = [c for c in _actor_calls if c[0] == 'edit_message_actor']
    assert len(edit_calls) == 1
    assert edit_calls[0][1][0] == 200  # chat_id
    assert edit_calls[0][1][1] == 555  # message_id
    # No fresh direct send — the existing message was edited in place.
    assert client.send_calls == []
    # Registry entry untouched (still points at the same message).
    assert bt_live._store[(7, 200)] == 555


def test_bt_intermediate_falls_through_to_send_when_registry_empty(tmp_path: pathlib.Path) -> None:
    """Regression guard: if the initial 'bt_dispatched' send never registered
    (or the Redis TTL expired), a status update must still reach the user —
    send fresh + register, rather than silently dropping the update."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('admin1', role='admin', chat_id=200)
    bt_live = FakeBtLiveMessages()

    n = _notifier(client, repo, _settings(), tmp_path, bt_live_messages=bt_live)
    _run(
        n.notify_bt_event(
            event='bt_status_update',
            title='Some Show - 01',
            feed_name='my-feed',
            filter_name='my-filter',
            putio_status='DOWNLOADING',
            entry_id=7,
        )
    )

    assert len(client.send_calls) == 1
    assert bt_live._store[(7, 200)] == 42
    assert [c for c in _actor_calls if c[0] == 'edit_message_actor'] == []


def test_bt_terminal_edits_and_clears_registry(tmp_path: pathlib.Path) -> None:
    """Terminal ('bt_landed'/'bt_failed') with existing live message → edit + clear."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('admin1', role='admin', chat_id=200)
    bt_live = FakeBtLiveMessages()
    _run(bt_live.set(7, 200, message_id=555))

    n = _notifier(client, repo, _settings(), tmp_path, bt_live_messages=bt_live)
    _run(
        n.notify_bt_event(
            event='bt_landed',
            title='Some Show - 01',
            feed_name='my-feed',
            filter_name='my-filter',
            local_path='Some Show - 01.mp4',
            entry_id=7,
        )
    )

    assert (7, 200) not in bt_live._store
    assert (7, 200) in bt_live.cleared
    edit_calls = [c for c in _actor_calls if c[0] == 'edit_message_actor']
    assert len(edit_calls) == 1
    assert edit_calls[0][1][1] == 555
    assert client.send_calls == []


def test_bt_terminal_falls_back_to_send_when_no_live_message(tmp_path: pathlib.Path) -> None:
    """Terminal with no live message on record → fresh send_message_actor."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('admin1', role='admin', chat_id=200)

    n = _notifier(client, repo, _settings(), tmp_path, bt_live_messages=FakeBtLiveMessages())
    _run(
        n.notify_bt_event(
            event='bt_failed',
            title='Some Show - 01',
            feed_name='my-feed',
            filter_name='my-filter',
            error_message='boom',
            entry_id=7,
        )
    )

    assert 'send_message_actor' in _actor_names()


# ---------------------------------------------------------------------------
# 'started' event — direct send with message_id capture
# ---------------------------------------------------------------------------


def test_started_sends_directly_and_stores_message_id(tmp_path: pathlib.Path) -> None:
    """'started' must send via direct client (not actor) and store message_id."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)
    live = FakeLiveMessages()

    n = _notifier(client, repo, _settings(), tmp_path, live_messages=live)
    _run(
        n.notify_download_event(
            event='started',
            owner_id='owner',
            sn=1,
            bangumi_name='某番',
            episode='01',
            resolution='1080',
            episode_number=1,
        )
    )

    assert len(client.send_calls) == 1
    assert client.send_calls[0][0] == 100
    # message_id should be stored in live registry.
    assert (1, 100) in live._store
    assert live._store[(1, 100)][0] == 42  # FakeTelegramClient's next_message_id starts at 42


# ---------------------------------------------------------------------------
# Terminal events — edit if live message exists, else send
# ---------------------------------------------------------------------------


def test_completed_edits_live_message_when_exists(tmp_path: pathlib.Path) -> None:
    """Completed with existing live message → edit_message_actor .send() called."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)
    live = FakeLiveMessages()
    # Pre-seed a live message.
    _run(live.set(1, 100, message_id=99, last_edit_at=0.0, last_rate=0.0))

    n = _notifier(client, repo, _settings(), tmp_path, live_messages=live)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            sn=1,
            bangumi_name='某番',
            episode='01',
            resolution='1080',
            episode_number=1,
        )
    )

    # Live entry cleared.
    assert (1, 100) not in live._store
    assert (1, 100) in live.cleared
    # No direct send (actor was used instead).
    assert client.send_calls == []
    # edit_message_actor.send() was called.
    assert 'edit_message_actor' in _actor_names()


def test_completed_falls_back_to_send_when_no_live_message(tmp_path: pathlib.Path) -> None:
    """Completed with no live message → send_message_actor .send() called."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)

    n = _notifier(client, repo, _settings(), tmp_path, live_messages=None)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            sn=2,
            bangumi_name='某番',
            episode='02',
            resolution='1080',
            episode_number=2,
        )
    )

    assert 'send_message_actor' in _actor_names()


# ---------------------------------------------------------------------------
# 'auto_enqueue' — owner-only, actor-based send
# ---------------------------------------------------------------------------


def test_auto_enqueue_sends_only_to_owner(tmp_path: pathlib.Path) -> None:
    """auto_enqueue skips admin broadcast; only owner gets a message."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)
    repo.add('admin1', role='admin', chat_id=200)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='auto_enqueue',
            owner_id='owner',
            sn=5,
            bangumi_name='Test',
            episode='01',
            resolution=None,
        )
    )

    # Only one send_message_actor call (owner only, no admin).
    send_calls = [c for c in _actor_calls if c[0] == 'send_message_actor']
    assert len(send_calls) == 1


def test_auto_enqueue_noop_when_owner_id_none(tmp_path: pathlib.Path) -> None:
    """auto_enqueue with owner_id=None → no messages."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('admin1', role='admin', chat_id=200)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='auto_enqueue',
            owner_id=None,
            sn=6,
            bangumi_name='Test',
            episode='01',
            resolution=None,
        )
    )

    assert _actor_calls == []


# ---------------------------------------------------------------------------
# Routing / de-dup (completed path via actor)
# ---------------------------------------------------------------------------


def test_sends_to_owner_and_admins_deduped(tmp_path: pathlib.Path) -> None:
    """Owner + two admins; owner is NOT an admin — three actor send calls total."""
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
            sn=10,
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            file_size_mb=100,
        )
    )

    send_calls = [c for c in _actor_calls if c[0] == 'send_message_actor']
    assert len(send_calls) == 3


def test_owner_is_admin_deduped(tmp_path: pathlib.Path) -> None:
    """Owner is also an admin → only ONE actor send call."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('adminowner', role='admin', chat_id=100)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='adminowner',
            sn=11,
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            file_size_mb=50,
        )
    )

    send_calls = [c for c in _actor_calls if c[0] == 'send_message_actor']
    assert len(send_calls) == 1


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
            sn=12,
            bangumi_name='Auto',
            episode=None,
            resolution=None,
            error_message='timeout',
        )
    )

    send_calls = [c for c in _actor_calls if c[0] == 'send_message_actor']
    assert len(send_calls) == 1


def test_skips_when_disabled(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)

    n = _notifier(client, repo, _settings(enabled=False), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            sn=13,
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            file_size_mb=100,
        )
    )

    assert client.send_calls == []
    assert _actor_calls == []


def test_skips_when_event_not_in_notify_on(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)

    n = _notifier(client, repo, _settings(notify_on=['failed']), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            sn=14,
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            file_size_mb=100,
        )
    )

    assert client.send_calls == []
    assert _actor_calls == []


def test_skips_owner_without_chat_id(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    repo.add('owner', chat_id=None)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            sn=15,
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            file_size_mb=10,
        )
    )

    assert client.send_calls == []
    assert _actor_calls == []


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
            sn=16,
            bangumi_name='Test',
            episode='01',
            resolution='1080',
            file_size_mb=10,
        )
    )

    # Only owner, no admin.
    send_calls = [c for c in _actor_calls if c[0] == 'send_message_actor']
    assert len(send_calls) == 1


# ---------------------------------------------------------------------------
# Mute gating
# ---------------------------------------------------------------------------


def test_muted_user_is_excluded(tmp_path: pathlib.Path) -> None:
    """A user muted until the far future should not receive notifications."""
    client = FakeTelegramClient()
    repo = FakeUserRepo()
    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
    repo.add('owner', chat_id=100, mute_until=future)

    n = _notifier(client, repo, _settings(), tmp_path)
    _run(
        n.notify_download_event(
            event='completed',
            owner_id='owner',
            sn=20,
            bangumi_name='Test',
            episode='01',
            resolution='1080',
        )
    )

    assert client.send_calls == []
    assert _actor_calls == []


# ---------------------------------------------------------------------------
# Error handling — 'started' path (direct send)
# ---------------------------------------------------------------------------


def test_started_blocked_error_clears_binding(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient(raise_on=TelegramBotBlockedError)
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)
    live = FakeLiveMessages()

    n = _notifier(client, repo, _settings(), tmp_path, live_messages=live)
    _run(
        n.notify_download_event(
            event='started',
            owner_id='owner',
            sn=30,
            bangumi_name='Test',
            episode='01',
            resolution='1080',
        )
    )

    assert 'owner' in repo.cleared
    # No message_id stored.
    assert (30, 100) not in live._store


def test_started_chat_not_found_clears_binding(tmp_path: pathlib.Path) -> None:
    client = FakeTelegramClient(raise_on=TelegramChatNotFoundError)
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)
    live = FakeLiveMessages()

    n = _notifier(client, repo, _settings(), tmp_path, live_messages=live)
    _run(
        n.notify_download_event(
            event='started',
            owner_id='owner',
            sn=31,
            bangumi_name='Test',
            episode='01',
            resolution='1080',
        )
    )

    assert 'owner' in repo.cleared


# ---------------------------------------------------------------------------
# format_progress_body — public formatter used by progress publisher
# ---------------------------------------------------------------------------


def test_format_progress_body_zero_rate_all_empty_cells() -> None:
    from app.downloader.progress import TaskProgress
    from app.services.telegram_notifier import format_progress_body

    entry = TaskProgress(sn=1, rate=0.0, status='ok', filename='f.mp4')
    body = format_progress_body(entry)
    # 0% → 0 filled, 10 empty cells
    assert '▱▱▱▱▱▱▱▱▱▱' in body
    # Confirm total bar width is 10 cells
    assert body.count('▰') + body.count('▱') == 10


def test_format_progress_body_100_rate_all_filled_cells() -> None:
    from app.downloader.progress import TaskProgress
    from app.services.telegram_notifier import format_progress_body

    entry = TaskProgress(sn=1, rate=1.0, status='ok', filename='f.mp4')
    body = format_progress_body(entry)
    # 100% → 10 filled cells
    assert '▱' not in body
    assert '100%' in body
    assert body.count('▰') == 10


def test_format_progress_body_50_rate_speed_eta() -> None:
    from app.downloader.progress import TaskProgress
    from app.services.telegram_notifier import format_progress_body

    entry = TaskProgress(sn=1, rate=0.5, status='ok', filename='f.mp4', speed_mbps=4.2, eta_seconds=83)
    body = format_progress_body(entry)
    # Half bar — 5 filled, 5 empty
    assert '50%' in body
    # Speed line: escape_markdown_v2 escapes '.' → '\.'
    assert '4' in body and 'MB/s' in body
    # ETA: 83s = 1m 23s
    assert '1m' in body and '23s' in body


def test_format_progress_body_retries_adds_line() -> None:
    from app.downloader.progress import TaskProgress
    from app.services.telegram_notifier import format_progress_body

    entry = TaskProgress(sn=1, rate=0.3, status='ok', filename='f.mp4', retries=3)
    body = format_progress_body(entry)
    assert '重試' in body
    assert '3' in body


def test_format_progress_body_cooldown_future_overrides_bar() -> None:
    from app.downloader.progress import TaskProgress
    from app.services.telegram_notifier import format_progress_body

    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=30)
    entry = TaskProgress(sn=1, rate=0.3, status='ok', filename='f.mp4', cooldown_until=future)
    body = format_progress_body(entry)
    assert '冷卻' in body
    # No progress bar when cooling down
    assert '▰' not in body and '▱' not in body and '%' not in body


def test_format_progress_body_cooldown_past_is_ignored() -> None:
    from app.downloader.progress import TaskProgress
    from app.services.telegram_notifier import format_progress_body

    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=10)
    entry = TaskProgress(sn=1, rate=0.5, status='ok', filename='f.mp4', cooldown_until=past)
    body = format_progress_body(entry)
    # Normal progress bar shown — cooldown is past
    assert '50%' in body
    assert '冷卻' not in body


def test_format_progress_body_clamps_rate_above_one() -> None:
    """rate=73.87 (segment_downloader's 0-100 convention) renders as 73%, not 7387%."""
    from app.downloader.progress import TaskProgress
    from app.services.telegram_notifier import format_progress_body

    entry = TaskProgress(sn=1, rate=73.87, status='下載中', filename='ep.mp4', bangumi_name='X', episode='1')
    body = format_progress_body(entry)
    assert '73%' in body
    assert '7387%' not in body
    # Bar must have exactly 10 cells (filled + empty)
    bar_chars = sum(body.count(c) for c in ('▰', '▱'))
    assert bar_chars == 10


def test_format_progress_body_clamps_rate_above_100() -> None:
    """Even crazier rate=7387.0 still renders as 100% and a full bar."""
    from app.downloader.progress import TaskProgress
    from app.services.telegram_notifier import format_progress_body

    entry = TaskProgress(sn=1, rate=7387.0, status='下載中', filename='ep.mp4', bangumi_name='X', episode='1')
    body = format_progress_body(entry)
    assert '100%' in body
    bar_chars = sum(body.count(c) for c in ('▰', '▱'))
    assert bar_chars == 10


def test_format_progress_body_negative_rate_clamps_to_zero() -> None:
    """Defensive: negative rate doesn't crash or render gibberish."""
    from app.downloader.progress import TaskProgress
    from app.services.telegram_notifier import format_progress_body

    entry = TaskProgress(sn=1, rate=-0.5, status='下載中', filename='ep.mp4', bangumi_name='X', episode='1')
    body = format_progress_body(entry)
    assert '0%' in body
    bar_chars = sum(body.count(c) for c in ('▰', '▱'))
    assert bar_chars == 10


def test_format_resolution_appends_p_suffix() -> None:
    from app.services.telegram_notifier import _format_resolution

    assert _format_resolution('1080') == '1080p'
    assert _format_resolution(720) == '720p'
    assert _format_resolution('540p') == '540p'  # idempotent
    assert _format_resolution(None) == ''
    assert _format_resolution('') == ''


def test_format_completed_resolution_has_p_suffix() -> None:
    msg = _format_message(
        event='completed',
        bangumi_name='某番',
        episode='01',
        resolution='1080',
        file_size_mb=None,
        error_message=None,
        episode_number=1,
    )
    assert '1080p' in msg
    assert '解析度' in msg


def test_started_other_exception_is_swallowed(tmp_path: pathlib.Path) -> None:
    """Non-permanent errors during 'started' should be swallowed."""

    class _Boom(TelegramApiError):
        def __init__(self, *_: object) -> None:
            Exception.__init__(self, 'boom')
            self.status_code = 500
            self.description = 'boom'
            self.error_code = None

    client = FakeTelegramClient(raise_on=_Boom)
    repo = FakeUserRepo()
    repo.add('owner', chat_id=100)

    n = _notifier(client, repo, _settings(), tmp_path)
    # Should not raise.
    _run(
        n.notify_download_event(
            event='started',
            owner_id='owner',
            sn=32,
            bangumi_name='Test',
            episode='01',
            resolution='1080',
        )
    )

    assert repo.cleared == []

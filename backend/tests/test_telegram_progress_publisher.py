"""Tests for telegram_progress_publisher — throttle logic, render, keyboard.

No real Redis or Telegram connections.  LiveMessageRegistry is faked
in-memory; edit_message_actor.send_with_options is monkeypatched.
"""

from __future__ import annotations

import asyncio
import collections.abc
import datetime

import pytest

from app.downloader.progress import TaskProgress
from app.services.telegram_progress_publisher import (
    _cancel_keyboard,
    _render_progress_message,
    _should_show_cancel,
)

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
# Fake LiveMessageRegistry
# ---------------------------------------------------------------------------


class FakeLiveMessages:
    """In-memory LiveMessageRegistry stand-in that also exposes list_for_sn."""

    def __init__(self) -> None:
        # (sn, chat_id) -> (message_id, last_edit_at, last_rate)
        self._store: dict[tuple[int, int], tuple[int, float, float]] = {}

    def seed(
        self,
        sn: int,
        chat_id: int,
        *,
        message_id: int,
        last_edit_at: float,
        last_rate: float,
    ) -> None:
        self._store[(sn, chat_id)] = (message_id, last_edit_at, last_rate)

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

    async def list_for_sn(self, sn: int) -> list[tuple[int, int, float, float]]:
        result = []
        for (s, chat_id), (message_id, last_edit_at, last_rate) in self._store.items():
            if s == sn:
                result.append((chat_id, message_id, last_edit_at, last_rate))
        return result

    async def clear(self, sn: int, chat_id: int) -> None:
        self._store.pop((sn, chat_id), None)


# ---------------------------------------------------------------------------
# Actor send spy
# ---------------------------------------------------------------------------

_send_with_options_calls: list[dict[str, object]] = []


@pytest.fixture(autouse=True)
def _reset_actor_spy(monkeypatch: pytest.MonkeyPatch) -> collections.abc.Generator[None]:
    _send_with_options_calls.clear()

    from app.tasks import telegram as tg_tasks

    def _spy(**kwargs: object) -> None:
        _send_with_options_calls.append(kwargs)

    monkeypatch.setattr(tg_tasks.edit_message_actor, 'send_with_options', _spy)
    yield


# ---------------------------------------------------------------------------
# Fake container + tick runner
# ---------------------------------------------------------------------------


def _make_entry(
    sn: int = 1,
    rate: float = 0.5,
    status: str = '正在下載',
    finished_at: datetime.datetime | None = None,
) -> TaskProgress:
    return TaskProgress(
        sn=sn,
        rate=rate,
        status=status,
        filename='test.mp4',
        bangumi_name='某番',
        episode='01',
        finished_at=finished_at,
    )


async def _run_tick(
    snap: dict[int, TaskProgress],
    live: FakeLiveMessages,
    *,
    bot_token: str = 'tok',
    enabled: bool = True,
) -> None:
    """Drive progress_publish_tick with a fake container."""
    import types

    from app.models import TelegramSettings
    from app.services.telegram_progress_publisher import progress_publish_tick

    settings = TelegramSettings(enabled=enabled, bot_token=bot_token)

    class _FakeSettingsRepo:
        def load(self) -> object:
            return types.SimpleNamespace(telegram=settings)

    class _FakeProgressReader:
        async def snapshot(self) -> dict[int, TaskProgress]:
            return snap

    class _FakeTelegramClient:
        pass

    fake_container = types.SimpleNamespace(
        redis_progress_reader=_FakeProgressReader(),
        live_messages=live,
        telegram_client=_FakeTelegramClient(),
        settings_repo=_FakeSettingsRepo(),
    )

    import unittest.mock

    with unittest.mock.patch('app.services.telegram_progress_publisher.build_container', return_value=fake_container):
        # Call the raw coroutine directly — avoids needing the AsyncIO middleware event loop thread.
        await progress_publish_tick.fn.__wrapped__()


# ---------------------------------------------------------------------------
# Throttle rule tests
# ---------------------------------------------------------------------------


def test_no_edit_when_within_interval_and_small_rate_delta() -> None:
    """Within 15s and rate delta < 5% → no edit dispatched."""
    import time

    live = FakeLiveMessages()
    now = time.time()
    sn, chat_id = 1, 100
    # last_edit_at = 5 seconds ago (well within 15s threshold)
    live.seed(sn, chat_id, message_id=42, last_edit_at=now - 5.0, last_rate=0.50)

    entry = _make_entry(sn=sn, rate=0.50)  # rate delta = 0 < 5%
    _run(_run_tick({sn: entry}, live))

    assert _send_with_options_calls == []


def test_edit_fires_after_interval_elapsed() -> None:
    """After 15s have elapsed, an edit should be dispatched."""
    import time

    live = FakeLiveMessages()
    now = time.time()
    sn, chat_id = 2, 200
    live.seed(sn, chat_id, message_id=99, last_edit_at=now - 20.0, last_rate=0.30)

    entry = _make_entry(sn=sn, rate=0.30)
    _run(_run_tick({sn: entry}, live))

    assert len(_send_with_options_calls) == 1
    call_kwargs = _send_with_options_calls[0]['kwargs']  # type: ignore[index]
    assert call_kwargs['chat_id'] == chat_id  # type: ignore[index]
    assert call_kwargs['message_id'] == 99  # type: ignore[index]


def test_edit_fires_on_large_rate_delta_within_interval() -> None:
    """Rate delta >= 5% triggers edit even within the 15s window."""
    import time

    live = FakeLiveMessages()
    now = time.time()
    sn, chat_id = 3, 300
    live.seed(sn, chat_id, message_id=77, last_edit_at=now - 3.0, last_rate=0.10)

    entry = _make_entry(sn=sn, rate=0.20)  # delta = 0.10 >= 0.05
    _run(_run_tick({sn: entry}, live))

    assert len(_send_with_options_calls) == 1


def test_edit_updates_last_edit_at_and_last_rate() -> None:
    """After an edit fires, last_edit_at and last_rate are updated in the registry."""
    import time

    live = FakeLiveMessages()
    now = time.time()
    sn, chat_id = 4, 400
    live.seed(sn, chat_id, message_id=55, last_edit_at=now - 20.0, last_rate=0.0)

    entry = _make_entry(sn=sn, rate=0.6)
    _run(_run_tick({sn: entry}, live))

    updated = _run(live.get(sn, chat_id))
    assert updated is not None
    _msg_id, _last_edit_at, _last_rate = updated
    assert _last_rate == pytest.approx(0.6)
    assert _last_edit_at >= now - 1.0  # just set


def test_finished_tasks_are_skipped() -> None:
    """Tasks with finished_at set should not trigger any edit."""
    import time

    live = FakeLiveMessages()
    now = time.time()
    sn, chat_id = 5, 500
    live.seed(sn, chat_id, message_id=11, last_edit_at=now - 60.0, last_rate=0.0)

    finished_entry = _make_entry(sn=sn, finished_at=datetime.datetime.now(datetime.UTC))
    _run(_run_tick({sn: finished_entry}, live))

    assert _send_with_options_calls == []


def test_disabled_telegram_noop() -> None:
    """When telegram.enabled=False, no edits should be dispatched."""
    import time

    live = FakeLiveMessages()
    now = time.time()
    sn, chat_id = 6, 600
    live.seed(sn, chat_id, message_id=22, last_edit_at=now - 60.0, last_rate=0.0)

    entry = _make_entry(sn=sn)
    _run(_run_tick({sn: entry}, live, enabled=False))

    assert _send_with_options_calls == []


# ---------------------------------------------------------------------------
# _cancel_keyboard
# ---------------------------------------------------------------------------


def test_cancel_keyboard_callback_data() -> None:
    kb = _cancel_keyboard(42)
    button = kb['inline_keyboard'][0][0]  # type: ignore[index]
    assert button['callback_data'] == 'm:cancel_yes:42'  # type: ignore[index]


def test_cancel_keyboard_uses_int_sn() -> None:
    kb = _cancel_keyboard(7)
    button = kb['inline_keyboard'][0][0]  # type: ignore[index]
    assert button['callback_data'] == 'm:cancel_yes:7'  # type: ignore[index]


# ---------------------------------------------------------------------------
# _render_progress_message
# ---------------------------------------------------------------------------


def test_render_progress_message_contains_header() -> None:
    entry = _make_entry(sn=1, rate=0.5)
    text = _render_progress_message(entry)
    assert '下載中' in text


def test_render_progress_message_contains_progress_bar() -> None:
    entry = _make_entry(sn=1, rate=0.5)
    text = _render_progress_message(entry)
    # 50% → 5 filled cells (▰) + 5 empty cells (▱)
    assert '▰▰▰▰▰' in text


def test_render_progress_message_contains_bangumi_name() -> None:
    entry = _make_entry(sn=1, rate=0.3)
    text = _render_progress_message(entry)
    assert '某番' in text


def test_render_progress_message_uses_canonical_episode_form() -> None:
    """Numeric episode label like '29' should render as '第 29 集' (matches the
    completed/started DM format), not the bare '29' fallback."""
    entry = TaskProgress(
        sn=1,
        rate=0.4,
        status='正在下載',
        filename='ep.mp4',
        bangumi_name='Dr.STONE 新石紀',
        episode='29',
    )
    text = _render_progress_message(entry)
    assert '第 29 集' in text


def test_render_progress_message_non_numeric_episode_uses_raw_label() -> None:
    """Labels like 'OVA' have no integer to extract — fall back to the
    raw episode string (no '第 N 集' wrapper)."""
    entry = TaskProgress(
        sn=1,
        rate=0.4,
        status='正在下載',
        filename='ep.mp4',
        bangumi_name='Some Anime',
        episode='OVA',
    )
    text = _render_progress_message(entry)
    assert 'OVA' in text
    assert '第 OVA 集' not in text  # raw label, not the canonical wrapper


def test_render_progress_message_speed_and_eta() -> None:
    entry = TaskProgress(
        sn=1,
        rate=0.4,
        status='正在下載',
        filename='test.mp4',
        bangumi_name='速度番',
        episode='02',
        speed_mbps=4.2,
        eta_seconds=83,
    )
    text = _render_progress_message(entry)
    # escape_markdown_v2 turns '.' into '\.' so speed appears as '4\.2 MB/s'
    assert '4\\.2' in text or 'MB/s' in text
    assert '1m' in text


# ---------------------------------------------------------------------------
# format_progress_body helpers (via render)
# ---------------------------------------------------------------------------


def test_format_progress_body_omits_speed_when_none() -> None:
    entry = TaskProgress(sn=1, rate=0.2, status='ok', filename='f.mp4', speed_mbps=None)
    from app.services.telegram_notifier import format_progress_body

    body = format_progress_body(entry)
    assert '速度' not in body


def test_format_progress_body_shows_retries() -> None:
    entry = TaskProgress(sn=1, rate=0.2, status='ok', filename='f.mp4', retries=2)
    from app.services.telegram_notifier import format_progress_body

    body = format_progress_body(entry)
    assert '重試' in body
    assert '2' in body


def test_format_progress_body_cooldown_overrides_bar() -> None:
    entry = TaskProgress(
        sn=1,
        rate=0.2,
        status='ok',
        filename='f.mp4',
        cooldown_until=datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=30),
    )
    from app.services.telegram_notifier import format_progress_body

    body = format_progress_body(entry)
    assert '冷卻' in body
    assert '▰' not in body and '▱' not in body


# ---------------------------------------------------------------------------
# _should_show_cancel
# ---------------------------------------------------------------------------


def test_no_cancel_button_when_rate_at_100_percent() -> None:
    # rate as 0-1 fraction (exactly 1.0 = 100%)
    entry = TaskProgress(sn=1, rate=1.0, status='解密合并', filename='ep.mp4')
    assert _should_show_cancel(entry) is False
    # rate as 0-100 percent scale
    entry2 = TaskProgress(sn=2, rate=100.0, status='正在解密', filename='ep.mp4')
    assert _should_show_cancel(entry2) is False


def test_no_cancel_button_during_post_download_phases() -> None:
    for status in ('解密合并', '移動檔案', '正在上傳', '正在合并', '正在解密'):
        entry = TaskProgress(sn=1, rate=0.5, status=status, filename='ep.mp4')
        assert _should_show_cancel(entry) is False, f'status={status} should hide cancel'


def test_cancel_button_visible_during_active_download() -> None:
    entry = TaskProgress(sn=1, rate=0.3, status='下載中', filename='ep.mp4')
    assert _should_show_cancel(entry) is True


def test_edit_tick_includes_reply_markup_key() -> None:
    """The edit call must always include a reply_markup key (either keyboard or None)."""
    import time

    live = FakeLiveMessages()
    now = time.time()
    sn, chat_id = 10, 1000
    live.seed(sn, chat_id, message_id=55, last_edit_at=now - 20.0, last_rate=0.0)

    entry = _make_entry(sn=sn, rate=0.3, status='正在下載')
    _run(_run_tick({sn: entry}, live))

    assert len(_send_with_options_calls) == 1
    call_kwargs = _send_with_options_calls[0]['kwargs']  # type: ignore[index]
    assert 'reply_markup' in call_kwargs  # type: ignore[index]


def test_edit_tick_no_cancel_button_at_post_download_status() -> None:
    """During post-download status the cancel keyboard should be suppressed (reply_markup=None)."""
    import time

    live = FakeLiveMessages()
    now = time.time()
    sn, chat_id = 11, 1100
    live.seed(sn, chat_id, message_id=66, last_edit_at=now - 20.0, last_rate=0.0)

    entry = _make_entry(sn=sn, rate=0.99, status='正在解密')
    _run(_run_tick({sn: entry}, live))

    assert len(_send_with_options_calls) == 1
    call_kwargs = _send_with_options_calls[0]['kwargs']  # type: ignore[index]
    assert call_kwargs['reply_markup'] is None  # type: ignore[index]

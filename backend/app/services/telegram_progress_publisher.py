"""Progress-publisher tick — edits the live progress DM for every active task.

Runs as a dramatiq actor on queue ``meta``; APScheduler triggers it every
5 seconds.  For each active sn × bound recipient, it computes whether to
edit (15s elapsed OR rate delta >= 5%) and dispatches an
``edit_message_actor`` if so.

Throttling state is stored per-(sn, chat_id) in Redis via
``LiveMessageRegistry`` so a scheduler restart resumes seamlessly.
"""

from __future__ import annotations

import time
import typing as T

import dramatiq

from .. import dramatiq_setup as _setup
from ..core import build_container

_setup.init_broker()

# Module-level constants for tunability and to make the throttle rule visible
# to readers / tests.
_MIN_EDIT_INTERVAL_SECONDS = 15.0
_MIN_RATE_DELTA = 0.05  # 5%

if T.TYPE_CHECKING:
    from ..downloader.progress import TaskProgress


@dramatiq.actor(queue_name='meta', max_retries=0, time_limit=60_000)
async def progress_publish_tick() -> None:
    """One pass — for each active task, edit the live progress message
    if the throttle rule says we should."""
    container = build_container()
    if container.redis_progress_reader is None or container.live_messages is None:
        return
    if container.telegram_client is None:
        return

    settings = container.settings_repo.load().telegram
    if not settings.enabled:
        return

    snap = await container.redis_progress_reader.snapshot()
    now = time.time()

    from ..tasks.telegram import edit_message_actor

    for sn, entry in snap.items():
        if entry.finished_at is not None:
            continue  # progress publishing only for in-flight tasks
        # iterate every recipient (LiveMessageRegistry knows who has a live msg)
        recipients = await container.live_messages.list_for_sn(sn)
        for chat_id, message_id, last_edit_at, last_rate in recipients:
            if not _should_edit(now, last_edit_at, entry.rate, last_rate):
                continue
            text = _render_progress_message(entry)
            edit_message_actor.send_with_options(
                kwargs={
                    'chat_id': chat_id,
                    'message_id': message_id,
                    'text': text,
                    'bot_token': settings.bot_token,
                    'reply_markup': _cancel_keyboard(sn),
                },
            )
            await container.live_messages.set(
                sn,
                chat_id,
                message_id=message_id,
                last_edit_at=now,
                last_rate=entry.rate,
            )


def _should_edit(now: float, last_edit_at: float, rate: float, last_rate: float) -> bool:
    """Return True when the throttle rule allows a live-message edit."""
    return (now - last_edit_at) >= _MIN_EDIT_INTERVAL_SECONDS or abs(rate - last_rate) >= _MIN_RATE_DELTA


def _render_progress_message(entry: 'TaskProgress') -> str:
    """Build the MarkdownV2 progress message for an in-flight task."""
    from .telegram_notifier import build_name_line, format_progress_body

    name_line = build_name_line(
        bangumi_name=entry.bangumi_name or '',
        episode=entry.episode,
        custom_name=None,
        season=1,
        episode_number=None,
    )
    body = format_progress_body(entry)
    return f'⏬ *下載中*\n\n{name_line}\n{body}'


def _cancel_keyboard(sn: int) -> dict[str, object]:
    """Inline keyboard with a single ❌ 取消 cancel button."""
    return {
        'inline_keyboard': [[
            {'text': '❌ 取消', 'callback_data': f'm:cancel_yes:{int(sn)}'},
        ]],
    }

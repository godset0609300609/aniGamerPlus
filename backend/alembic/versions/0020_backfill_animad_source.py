"""Backfill legacy NULL task_history.source to 'animad'.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-12

The ``source`` column was added to ``task_history`` in migration 0012, well
after animad (動畫瘋) was the app's only download source. At that time every
write path (``app.downloader.anime.Anime.download`` via
``app.scheduler.update_loop`` / ``app.scheduler.manual_runner``) called
``ProgressBus.start()`` / ``TaskHistoryRepository.record_start()`` without a
``source`` argument, so those rows persisted ``source = NULL``. bilibili
(migration 0012), BT (0014), and Telegram (0016) all set ``source``
explicitly from the moment their columns/tables were introduced — see
``app/downloader/bilibili/runner.py``, ``app/services/bt_downloader_service.py``,
``app/services/bt_manual_dispatch_service.py``, and
``app/tg_downloader/downloader.py``. animad itself only started passing
``source='animad'`` explicitly as of the fix in PR #12
(``app/downloader/anime.py``, ``app/scheduler/manual_runner.py``,
``app/scheduler/update_loop.py``). So every ``task_history`` row with
``source IS NULL`` predates that fix and is definitionally an animad
download.

Before PR #12, the frontend's ``sourceBadge`` mapping treated a null/
undefined source as 動畫瘋, so these legacy rows displayed correctly by
coincidence. PR #12 changed that fallback to a neutral "未知" (unknown)
badge — correct for genuinely unknown sources, but it left every legacy
animad row in the MonitorView 來源 (source) column mislabeled as 未知. This
migration backfills those rows so they render as 動畫瘋 again.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent — WHERE source IS NULL means re-running this is harmless.
    op.execute("UPDATE task_history SET source = 'animad' WHERE source IS NULL")


def downgrade() -> None:
    # Irreversible in principle: once backfilled, a row with source='animad'
    # can no longer be distinguished from a row that was genuinely written
    # with source='animad' by the app (post-PR #12). Downgrade is a no-op
    # rather than blindly setting rows back to NULL.
    pass

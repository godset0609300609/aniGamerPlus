"""Tests for ProgressService.snapshot — RBAC filtering and terminal-entry inclusion.

Guards against the regression where ``_TERMINAL_STATUSES`` was used to strip
finished entries from the WS snapshot, causing a visible delay in the 近期完成
column until the 60-second DB history poll fired.
"""

from __future__ import annotations

import dataclasses
import datetime
import typing as T

import pytest

from app.downloader.progress import ProgressBus
from app.persistence.user_repo import UserRow
from app.services.progress_service import ProgressService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _admin(uid: str = 'admin-1') -> UserRow:
    return UserRow(
        id=uid,
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )


def _downloader(uid: str = 'user-1') -> UserRow:
    return UserRow(
        id=uid,
        username='alice',
        avatar_url=None,
        role='downloader',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )


def _make_service(bus: ProgressBus) -> ProgressService:
    return ProgressService(progress_bus=bus, user_repo=None, scheduler_proxy=None)


# ---------------------------------------------------------------------------
# Terminal entries must appear in the snapshot
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_snapshot_for_admin_includes_terminal_entry() -> None:
    """Admin snapshot must include recently-finished entries (status='下載完成')."""
    bus = ProgressBus()
    bus.start(1001, 'ep01.mp4', status='正在下載', owner_id='user-1')
    bus.update_status(1001, '下載完成')
    bus.finish(1001)

    svc = _make_service(bus)
    snap = await svc.snapshot(_admin())

    assert '1001' in snap.tasks, 'terminal entry must not be stripped from admin snapshot'
    entry = snap.tasks['1001']
    assert entry.status == '下載完成'
    assert entry.finished_at is not None, 'finished_at must be present in DTO'


@pytest.mark.anyio
async def test_snapshot_for_downloader_includes_own_terminal_entry() -> None:
    """Downloader snapshot must include their own recently-finished entries."""
    bus = ProgressBus()
    bus.start(1002, 'ep02.mp4', status='正在下載', owner_id='user-1')
    bus.update_status(1002, '下載完成')
    bus.finish(1002)

    svc = _make_service(bus)
    snap = await svc.snapshot(_downloader('user-1'))

    assert '1002' in snap.tasks, "owner's terminal entry must appear in downloader snapshot"
    entry = snap.tasks['1002']
    assert entry.status == '下載完成'
    assert entry.finished_at is not None


@pytest.mark.anyio
async def test_snapshot_for_downloader_excludes_other_users_terminal_entry() -> None:
    """Downloader snapshot must not include other users' tasks (even terminal ones)."""
    bus = ProgressBus()
    bus.start(1003, 'ep03.mp4', status='正在下載', owner_id='user-other')
    bus.update_status(1003, '下載完成')
    bus.finish(1003)

    svc = _make_service(bus)
    snap = await svc.snapshot(_downloader('user-1'))

    assert '1003' not in snap.tasks


@pytest.mark.anyio
async def test_snapshot_finished_at_iso_string_format() -> None:
    """finished_at in the DTO must be an ISO-8601 string matching the bus value."""
    bus = ProgressBus()
    before = datetime.datetime.now(datetime.UTC)
    bus.start(1004, 'ep04.mp4', status='正在下載', owner_id='admin-1')
    bus.update_status(1004, '下載完成')
    bus.finish(1004)
    after = datetime.datetime.now(datetime.UTC)

    svc = _make_service(bus)
    snap = await svc.snapshot(_admin())

    dto = snap.tasks['1004']
    assert dto.finished_at is not None
    parsed = datetime.datetime.fromisoformat(dto.finished_at)
    # Ensure it's within the expected range — confirms it's a real timestamp.
    assert before <= parsed <= after


@pytest.mark.anyio
async def test_snapshot_active_entry_has_no_finished_at() -> None:
    """An in-flight task must have finished_at=None in the DTO."""
    bus = ProgressBus()
    bus.start(1005, 'ep05.mp4', status='正在下載', owner_id='admin-1')

    svc = _make_service(bus)
    snap = await svc.snapshot(_admin())

    dto = snap.tasks['1005']
    assert dto.finished_at is None


@pytest.mark.anyio
async def test_snapshot_multiple_terminal_statuses_included() -> None:
    """All recognised terminal statuses are included, not just 下載完成."""
    bus = ProgressBus()
    # 任務完成
    bus.start(2001, 'ep01.mp4', status='任務完成', owner_id='admin-1')
    bus.finish(2001)
    # 下載完成
    bus.start(2002, 'ep02.mp4', status='正在下載', owner_id='admin-1')
    bus.update_status(2002, '下載完成')
    bus.finish(2002)

    svc = _make_service(bus)
    snap = await svc.snapshot(_admin())

    assert '2001' in snap.tasks
    assert '2002' in snap.tasks

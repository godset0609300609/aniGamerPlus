"""Tests for ProgressService.snapshot — RBAC filtering and terminal-entry inclusion.

Guards against the regression where ``_TERMINAL_STATUSES`` was used to strip
finished entries from the WS snapshot, causing a visible delay in the 近期完成
column until the 60-second DB history poll fired.
"""

from __future__ import annotations

import datetime
import pathlib

import fastapi
import pytest

from app.downloader.progress import ProgressBus, TaskProgress
from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.user_repo import UserRepository, UserRow
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
    return ProgressService(progress_bus=bus, user_repo=None)


def _make_db(tmp_path: pathlib.Path, name: str = 'test.db') -> Database:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{(tmp_path / name).as_posix()}', logger)
    db.run_baseline_migrations()
    return db


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


# ---------------------------------------------------------------------------
# Regression: source / external_id must survive the bus → ProgressService path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_snapshot_propagates_source_and_external_id_for_admin() -> None:
    """Admin snapshot must include source and external_id from the bus entry."""
    bus = ProgressBus()
    bus.start(3001, 'BV1xxx.mp4', owner_id='admin-1', source='bilibili', external_id='BV1xxx')

    svc = _make_service(bus)
    snap = await svc.snapshot(_admin())

    assert '3001' in snap.tasks
    dto = snap.tasks['3001']
    assert dto.source == 'bilibili'
    assert dto.external_id == 'BV1xxx'


@pytest.mark.anyio
async def test_snapshot_propagates_source_and_external_id_for_downloader() -> None:
    """Downloader snapshot also carries source and external_id through."""
    bus = ProgressBus()
    bus.start(3002, 'BV1yyy.mp4', owner_id='user-1', source='bilibili', external_id='BV1yyy')

    svc = _make_service(bus)
    snap = await svc.snapshot(_downloader('user-1'))

    assert '3002' in snap.tasks
    dto = snap.tasks['3002']
    assert dto.source == 'bilibili'
    assert dto.external_id == 'BV1yyy'


# ---------------------------------------------------------------------------
# owner_avatar_url — MonitorView owner column Discord avatar (fix #4)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_snapshot_populates_owner_avatar_url_for_admin(tmp_path: pathlib.Path) -> None:
    """Admin snapshot must carry the owner's Discord avatar_url through."""
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        user_repo.upsert(
            id='user-1',
            username='alice',
            avatar_url='https://cdn.discordapp.com/avatars/user-1/abc123.png',
            role='downloader',
        )

        bus = ProgressBus()
        bus.start(4001, 'ep01.mp4', owner_id='user-1')

        svc = ProgressService(progress_bus=bus, user_repo=user_repo)
        snap = await svc.snapshot(_admin())

        dto = snap.tasks['4001']
        assert dto.owner_username == 'alice'
        assert dto.owner_avatar_url == 'https://cdn.discordapp.com/avatars/user-1/abc123.png'
    finally:
        db.dispose()


@pytest.mark.anyio
async def test_snapshot_owner_avatar_url_none_when_user_has_no_avatar(tmp_path: pathlib.Path) -> None:
    """A user without a custom Discord avatar yields owner_avatar_url=None, not an error."""
    db = _make_db(tmp_path)
    try:
        user_repo = UserRepository(db)
        user_repo.upsert(id='user-2', username='bob', avatar_url=None, role='downloader')

        bus = ProgressBus()
        bus.start(4002, 'ep02.mp4', owner_id='user-2')

        svc = ProgressService(progress_bus=bus, user_repo=user_repo)
        snap = await svc.snapshot(_admin())

        dto = snap.tasks['4002']
        assert dto.owner_username == 'bob'
        assert dto.owner_avatar_url is None
    finally:
        db.dispose()


@pytest.mark.anyio
async def test_snapshot_owner_avatar_url_none_for_downloader_view() -> None:
    """Downloader snapshots never leak owner_avatar_url, matching owner_username/owner_id."""
    bus = ProgressBus()
    bus.start(4003, 'ep03.mp4', owner_id='user-1')

    svc = _make_service(bus)
    snap = await svc.snapshot(_downloader('user-1'))

    dto = snap.tasks['4003']
    assert dto.owner_avatar_url is None


# ---------------------------------------------------------------------------
# force_finish — MonitorView dismiss ('X') button backend
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_force_finish_admin_can_dismiss_any_owners_task() -> None:
    bus = ProgressBus()
    bus.start(5001, 'ep01.mp4', status='正在下載', owner_id='some-downloader')

    svc = _make_service(bus)
    await svc.force_finish(5001, _admin(), status='已取消')

    entry = bus.snapshot()[5001]
    assert entry.status == '已取消'
    assert entry.finished_at is not None


@pytest.mark.anyio
async def test_force_finish_owner_can_dismiss_their_own_task() -> None:
    bus = ProgressBus()
    bus.start(5002, 'ep02.mp4', status='正在下載', owner_id='user-1')

    svc = _make_service(bus)
    await svc.force_finish(5002, _downloader('user-1'), status='已取消')

    assert bus.snapshot()[5002].status == '已取消'


@pytest.mark.anyio
async def test_force_finish_raises_403_for_non_owner_non_admin() -> None:
    bus = ProgressBus()
    bus.start(5003, 'ep03.mp4', status='正在下載', owner_id='user-1')

    svc = _make_service(bus)
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await svc.force_finish(5003, _downloader('user-2'), status='已取消')

    assert exc_info.value.status_code == 403
    # Must not have been touched.
    assert bus.snapshot()[5003].status == '正在下載'


@pytest.mark.anyio
async def test_force_finish_raises_404_when_sn_not_visible() -> None:
    bus = ProgressBus()

    svc = _make_service(bus)
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await svc.force_finish(9999, _admin(), status='已取消')

    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_force_finish_is_a_noop_on_an_already_finished_entry() -> None:
    """Idempotency: dismissing an entry that has already reached a real
    terminal outcome (e.g. genuinely completed between the WS snapshot and
    the click) must not clobber that outcome with '已取消'."""
    bus = ProgressBus()
    bus.start(5004, 'ep04.mp4', status='正在下載', owner_id='admin-1')
    bus.update_status(5004, '下載完成')
    bus.finish(5004)

    svc = _make_service(bus)
    await svc.force_finish(5004, _admin(), status='已取消')

    entry = bus.snapshot()[5004]
    assert entry.status == '下載完成', 'a real terminal outcome must survive a dismiss click'


@pytest.mark.anyio
async def test_force_finish_repeated_dismiss_is_idempotent() -> None:
    """X on an already-dismissed row is a no-op — calling force_finish twice
    must not raise or change the outcome the second time."""
    bus = ProgressBus()
    bus.start(5005, 'ep05.mp4', status='正在下載', owner_id='admin-1')

    svc = _make_service(bus)
    await svc.force_finish(5005, _admin(), status='已取消')
    first_finished_at = bus.snapshot()[5005].finished_at

    # Second dismiss call must be a silent no-op: the API's raw snapshot now
    # sees the already-terminal entry and the idempotency guard short-circuits.
    await svc.force_finish(5005, _admin(), status='已取消')

    entry = bus.snapshot()[5005]
    assert entry.status == '已取消'
    assert entry.finished_at == first_finished_at


class _FakeRawSnapshotReader:
    """Stands in for RedisProgressReader — the real cross-process source that
    lets the API process see entries live in *either* bus (both write through
    the same Redis mirror; see Container.bt_progress_bus's docstring)."""

    def __init__(self, snapshot: dict[int, TaskProgress]) -> None:
        self._snapshot = snapshot

    async def snapshot(self) -> dict[int, TaskProgress]:
        return dict(self._snapshot)


@pytest.mark.anyio
async def test_force_finish_routes_bt_sourced_sn_to_the_bt_bus() -> None:
    """A sn tagged source='bt' in the raw snapshot must be force-finished on
    the BT bus, not the shared one, mirroring how BtProgressReconciler
    routes BT rows."""
    bt_entry = TaskProgress(
        sn=5006, rate=0.4, status='落地中', filename='bt-entry.mkv', owner_id='admin-1', source='bt'
    )
    reader = _FakeRawSnapshotReader({5006: bt_entry})
    bus = ProgressBus()
    bt_bus = ProgressBus()

    svc = ProgressService(progress_bus=bus, user_repo=None, redis_reader=reader, bt_progress_bus=bt_bus)  # type: ignore[arg-type]
    await svc.force_finish(5006, _admin(), status='已取消')

    assert bt_bus.snapshot()[5006].status == '已取消'
    assert bus.snapshot() == {}, 'must not have touched the shared (non-BT) bus'


# ---------------------------------------------------------------------------
# force_finish — best-effort dramatiq_abort signal (live task vs. ghost)
# ---------------------------------------------------------------------------


class _FakeMessageIdRegistry:
    """Stands in for MessageIdRegistry — mirrors its async ``get()`` API."""

    def __init__(self, message_id: str | None) -> None:
        self._message_id = message_id

    async def get(self, sn: int) -> str | None:
        return self._message_id


@pytest.mark.anyio
async def test_force_finish_calls_dramatiq_abort_when_message_id_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live task (message_id registered) must get a best-effort dramatiq_abort
    signal before the mirror is closed, so the worker actually stops downloading
    instead of later overwriting the mirror with a rosy '下載完成'."""
    import dramatiq_abort
    import dramatiq_abort.middleware

    bus = ProgressBus()
    bus.start(6001, 'ep01.mp4', status='正在下載', owner_id='admin-1')

    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_abort(message_id: str, **kwargs: object) -> None:
        calls.append((message_id, kwargs))

    monkeypatch.setattr(dramatiq_abort, 'abort', _fake_abort)

    registry = _FakeMessageIdRegistry('msg-abc')
    svc = ProgressService(progress_bus=bus, user_repo=None, message_id_registry=registry)  # type: ignore[arg-type]
    await svc.force_finish(6001, _admin(), status='已取消')

    assert len(calls) == 1, 'dramatiq_abort.abort must be called exactly once'
    message_id, kwargs = calls[0]
    assert message_id == 'msg-abc'
    assert kwargs['mode'] == dramatiq_abort.middleware.AbortMode.ABORT
    assert kwargs['abort_timeout'] == 5000

    entry = bus.snapshot()[6001]
    assert entry.status == '已取消'
    assert entry.finished_at is not None


@pytest.mark.anyio
async def test_force_finish_silently_skips_abort_when_no_message_id_in_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ghost task has no message_id left in the registry — abort must not be
    called, but the mirror must still be force-finished so the card disappears."""
    import dramatiq_abort

    bus = ProgressBus()
    bus.start(6002, 'ep02.mp4', status='正在下載', owner_id='admin-1')

    called = False

    def _fake_abort(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(dramatiq_abort, 'abort', _fake_abort)

    registry = _FakeMessageIdRegistry(None)
    svc = ProgressService(progress_bus=bus, user_repo=None, message_id_registry=registry)  # type: ignore[arg-type]
    await svc.force_finish(6002, _admin(), status='已取消')

    assert called is False, 'no message_id means nothing to abort'
    entry = bus.snapshot()[6002]
    assert entry.status == '已取消'
    assert entry.finished_at is not None


@pytest.mark.anyio
async def test_force_finish_swallows_abort_failure_and_still_closes_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis blip / broker error during the abort attempt must not propagate —
    the mirror cleanup is unconditional so the card still disappears either way."""
    import dramatiq_abort

    bus = ProgressBus()
    bus.start(6003, 'ep03.mp4', status='正在下載', owner_id='admin-1')

    def _raising_abort(*args: object, **kwargs: object) -> None:
        raise RuntimeError('redis blip')

    monkeypatch.setattr(dramatiq_abort, 'abort', _raising_abort)

    registry = _FakeMessageIdRegistry('msg-xyz')
    svc = ProgressService(progress_bus=bus, user_repo=None, message_id_registry=registry)  # type: ignore[arg-type]
    await svc.force_finish(6003, _admin(), status='已取消')  # must not raise

    entry = bus.snapshot()[6003]
    assert entry.status == '已取消'
    assert entry.finished_at is not None


@pytest.mark.anyio
async def test_force_finish_without_registry_dependency_still_works() -> None:
    """Regression guard: constructing ProgressService without a message_id_registry
    (as most call sites / tests do) must continue to force-finish normally."""
    bus = ProgressBus()
    bus.start(6004, 'ep04.mp4', status='正在下載', owner_id='admin-1')

    svc = _make_service(bus)  # message_id_registry defaults to None
    await svc.force_finish(6004, _admin(), status='已取消')

    entry = bus.snapshot()[6004]
    assert entry.status == '已取消'
    assert entry.finished_at is not None

"""Unit tests for service classes (independent of FastAPI)."""

from __future__ import annotations

import asyncio
import base64
import datetime
import time

import pytest

from app.models import ManualTaskRequest, WebSettings
from app.persistence.user_repo import UserRow
from app.services.auth import AuthService
from app.services.config_service import ConfigService
from app.services.progress_service import ProgressService
from app.services.snlist_service import SnListService
from app.services.task_service import TaskService

from .conftest import FakeContainer, FakeManualRunner, FakeSchedulerProxy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _admin_user(uid: str = 'svc-test-admin') -> UserRow:
    return UserRow(
        id=uid,
        username='admin_test',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )


def _downloader_user(uid: str = 'svc-test-dl') -> UserRow:
    return UserRow(
        id=uid,
        username='dl_test',
        avatar_url=None,
        role='downloader',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )


# ---------------------------------------------------------------------------
# ConfigService
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_config_service_reads_whitelisted_keys(
    fake_container: FakeContainer,
) -> None:
    service = ConfigService(fake_container.settings_repo)
    settings = await service.read()
    assert isinstance(settings, WebSettings)
    assert settings.download_resolution == '1080'
    assert settings.multi_thread == 1


@pytest.mark.anyio
async def test_config_service_round_trip(fake_container: FakeContainer) -> None:
    service = ConfigService(fake_container.settings_repo)
    new = (await service.read()).model_copy(update={'multi_thread': 4})
    await service.write(new)
    persisted = fake_container.settings_repo.load()
    assert persisted.multi_thread == 4


@pytest.mark.anyio
async def test_config_service_preserves_nested_models(
    fake_container: FakeContainer,
) -> None:
    """Writing a WebSettings payload must not clobber the nested models
    (``dashboard``, ``ftp``) on the full AppSettings.

    Legacy ``model_copy(update=...)`` was a shallow merge — if a future
    payload ever carried one of these keys, the entire sub-model would be
    replaced. We exercise the case here by pre-seeding a non-default
    dashboard host and confirming it survives a ``write`` that only
    touches the web-visible ``multi_thread`` field.
    """
    current = fake_container.settings_repo.load()
    fake_container.settings_repo.save(
        current.model_copy(
            update={'dashboard': current.dashboard.model_copy(update={'host': '10.0.0.5', 'port': 9000})}
        )
    )

    service = ConfigService(fake_container.settings_repo)
    payload = (await service.read()).model_copy(update={'multi_thread': 4})
    await service.write(payload)

    persisted = fake_container.settings_repo.load()
    # Nested dashboard survived the partial overlay.
    assert persisted.dashboard.host == '10.0.0.5'
    assert persisted.dashboard.port == 9000
    # The requested change actually went through.
    assert persisted.multi_thread == 4


# ---------------------------------------------------------------------------
# SnListService
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_snlist_service_roundtrip(fake_container: FakeContainer) -> None:
    service = SnListService(fake_container.sn_list_repo)
    assert await service.read() == ''
    await service.write('11111 latest\n22222 all')
    assert await service.read() == '11111 latest\n22222 all'


# ---------------------------------------------------------------------------
# TaskService
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_task_service_enqueues_normalised_call(
    fake_container: FakeContainer,
) -> None:
    """Without a proxy wired, enqueue falls back to in-process ManualRunner."""
    runner = FakeManualRunner()
    # No proxy: falls back to direct ManualRunner call in a daemon thread.
    service = TaskService(fake_container.settings_repo, runner, scheduler_proxy=None)
    user = _admin_user()
    await service.enqueue(
        ManualTaskRequest(
            sn='12345',
            resolution='720',
            mode='single',
            thread=10,
            classify=True,
            danmu=True,
        ),
        user,
    )

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not runner.run_calls:
        await asyncio.sleep(0.01)

    assert len(runner.run_calls) == 1
    call = runner.run_calls[0]
    assert call['sn'] == 12345
    assert call['resolution'] == '720'
    assert call['mode'] == 'single'
    # Clamped to TaskService._MAX_MULTI_THREAD == 5.
    assert call['thread_limit'] == 5
    assert call['cui_danmu'] is True
    assert call['realtime_show'] is False
    assert call['owner_id'] == user.id


@pytest.mark.anyio
async def test_task_service_falls_back_to_config_resolution(
    fake_container: FakeContainer,
) -> None:
    runner = FakeManualRunner()
    service = TaskService(fake_container.settings_repo, runner, scheduler_proxy=None)
    user = _admin_user()

    # Bypass pydantic validation to exercise the service-level guard.
    request = ManualTaskRequest.model_construct(
        sn='1',
        resolution='9999',
        mode='single',
        thread=1,
        classify=True,
        danmu=False,
    )
    await service.enqueue(request, user)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not runner.run_calls:
        await asyncio.sleep(0.01)

    assert runner.run_calls[0]['resolution'] == '1080'


@pytest.mark.anyio
async def test_task_service_enqueue_bubbles_scheduler_unreachable_as_503(
    fake_container: FakeContainer,
) -> None:
    """enqueue_manual raising SchedulerUnreachable must surface as HTTP 503.

    This is the primary guard against the WS-reconnect false-positive: the
    service must not pre-check is_scheduler_up(); instead it catches the
    concrete HTTP error from the proxy.
    """
    import fastapi

    from app.api._scheduler_proxy import SchedulerUnreachable

    # Proxy is technically "up=False" (WS stale) but that must NOT matter.
    proxy = FakeSchedulerProxy(up=False)
    proxy.enqueue_raises = SchedulerUnreachable('Scheduler HTTP unreachable: connect refused')
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        scheduler_proxy=proxy,
    )
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await service.enqueue(
            ManualTaskRequest(sn='1', resolution='1080', mode='single'),
            _admin_user(),
        )
    assert exc_info.value.status_code == 503
    assert '排程服務暫時無回應' in exc_info.value.detail


@pytest.mark.anyio
async def test_task_service_enqueue_succeeds_even_when_ws_stale(
    fake_container: FakeContainer,
) -> None:
    """enqueue must succeed when WS is stale but HTTP call succeeds.

    This is the core fix: a short WS reconnect window (is_scheduler_up=False)
    must no longer block task submission if the HTTP round-trip works.
    """
    # Proxy has stale WS (up=False) but enqueue_manual will succeed.
    proxy = FakeSchedulerProxy(up=False)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        scheduler_proxy=proxy,
    )
    user = _admin_user('uid-ws-stale')
    # Should not raise — HTTP succeeds even though WS is considered stale.
    await service.enqueue(
        ManualTaskRequest(sn='777', resolution='1080', mode='single'),
        user,
    )
    assert len(proxy.enqueue_calls) == 1
    assert proxy.enqueue_calls[0]['owner_id'] == 'uid-ws-stale'


@pytest.mark.anyio
async def test_task_service_delegates_to_proxy_when_up(
    fake_container: FakeContainer,
) -> None:
    """When proxy is up, enqueue_manual is called (not the local runner)."""
    proxy = FakeSchedulerProxy(up=True)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        scheduler_proxy=proxy,
    )
    user = _admin_user('uid-999')
    await service.enqueue(
        ManualTaskRequest(sn='888', resolution='720', mode='single'),
        user,
    )
    assert len(proxy.enqueue_calls) == 1
    assert proxy.enqueue_calls[0]['owner_id'] == 'uid-999'
    # Local runner should NOT have been called.
    assert not fake_container.manual_runner.run_calls


# ---------------------------------------------------------------------------
# ProgressService
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_progress_service_reads_bus_entries_admin(
    fake_container: FakeContainer,
) -> None:
    fake_container.progress_bus.start(1, 'a', status='x')
    fake_container.progress_bus.update_rate(1, 10.0)
    user = _admin_user()
    snap = await ProgressService(fake_container.progress_bus).snapshot(user)
    assert list(snap.tasks) == ['1']
    assert snap.tasks['1'].rate == 10.0
    assert snap.tasks['1'].status == 'x'
    assert snap.tasks['1'].filename == 'a'


@pytest.mark.anyio
async def test_progress_service_empty_when_no_entries(
    fake_container: FakeContainer,
) -> None:
    user = _admin_user()
    snap = await ProgressService(fake_container.progress_bus).snapshot(user)
    assert snap.tasks == {}


@pytest.mark.anyio
async def test_progress_service_includes_terminal_statuses(
    fake_container: FakeContainer,
) -> None:
    """Terminal-status entries must now be INCLUDED in the snapshot so the
    frontend can place them in the 近期完成 column via the WS push (≤ 1 s
    latency) without waiting for the 60-second DB history poll.

    Previously these were filtered out here; that caused the UX delay.
    """
    bus = fake_container.progress_bus
    bus.start(1, 'active.mp4', status='正在下載')
    bus.start(2, 'finished.mp4', status='下載完成')
    bus.start(3, 'waiting.mp4', status='等待下載')

    user = _admin_user()
    snap = await ProgressService(bus).snapshot(user)
    # All three entries must be present — including the terminal one.
    assert set(snap.tasks.keys()) == {'1', '2', '3'}
    assert snap.tasks['1'].status == '正在下載'
    assert snap.tasks['2'].status == '下載完成'
    assert snap.tasks['3'].status == '等待下載'


@pytest.mark.anyio
async def test_progress_service_includes_all_terminal_markers(
    fake_container: FakeContainer,
) -> None:
    """All recognised terminal statuses are included in the snapshot."""
    bus = fake_container.progress_bus
    bus.start(10, 'a.mp4', status='下載完成')
    bus.start(11, 'b.mp4', status='上傳完成')
    bus.start(12, 'c.mp4', status='任務完成')
    bus.start(13, 'd.mp4', status='正在上傳')

    user = _admin_user()
    snap = await ProgressService(bus).snapshot(user)
    # All four entries must appear; the frontend is responsible for routing
    # terminal vs. active entries into the correct UI column.
    assert set(snap.tasks.keys()) == {'10', '11', '12', '13'}
    assert snap.tasks['13'].status == '正在上傳'


@pytest.mark.anyio
async def test_progress_service_downloader_sees_only_own_tasks(
    fake_container: FakeContainer,
) -> None:
    """Downloader role only sees tasks whose owner_id matches."""
    bus = fake_container.progress_bus
    user = _downloader_user('dl-1')
    other_uid = 'other-dl'
    bus.start(100, 'mine.mp4', status='正在下載', owner_id=user.id)
    bus.start(101, 'theirs.mp4', status='正在下載', owner_id=other_uid)

    snap = await ProgressService(bus).snapshot(user)
    assert '100' in snap.tasks
    assert '101' not in snap.tasks


@pytest.mark.anyio
async def test_progress_service_admin_sees_all_tasks(
    fake_container: FakeContainer,
) -> None:
    """Admin role sees every in-flight task regardless of owner."""
    bus = fake_container.progress_bus
    user = _admin_user()
    bus.start(200, 'a.mp4', status='正在下載', owner_id='dl-1')
    bus.start(201, 'b.mp4', status='正在下載', owner_id='dl-2')

    snap = await ProgressService(bus).snapshot(user)
    assert '200' in snap.tasks
    assert '201' in snap.tasks


# ---------------------------------------------------------------------------
# AuthService
# ---------------------------------------------------------------------------


def _flip_basic_auth(fake_container: FakeContainer, *, user: str = 'u', pw: str = 'p') -> None:
    current = fake_container.settings_repo.load()
    fake_container.settings_repo.save(
        current.model_copy(
            update={
                'dashboard': current.dashboard.model_copy(update={'BasicAuth': True, 'username': user, 'password': pw})
            }
        )
    )


@pytest.mark.anyio
async def test_auth_service_anonymous_when_disabled(
    fake_container: FakeContainer,
) -> None:
    auth = AuthService(fake_container.settings_repo)
    assert not await auth.is_enabled()
    assert await auth.verify_http(None) == 'anonymous'
    assert await auth.verify_ws(None) is True


@pytest.mark.anyio
async def test_auth_service_rejects_bad_ws_header(
    fake_container: FakeContainer,
) -> None:
    _flip_basic_auth(fake_container)
    auth = AuthService(fake_container.settings_repo)
    assert await auth.verify_ws(None) is False
    assert await auth.verify_ws('Bearer xyz') is False
    assert await auth.verify_ws('Basic !!!not-base64!!!') is False


@pytest.mark.anyio
async def test_auth_service_accepts_valid_ws_header(
    fake_container: FakeContainer,
) -> None:
    _flip_basic_auth(fake_container, user='u', pw='p')
    auth = AuthService(fake_container.settings_repo)
    header = 'Basic ' + base64.b64encode(b'u:p').decode()
    assert await auth.verify_ws(header) is True


# ---------------------------------------------------------------------------
# TaskService.cancel_task
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_task_owner_can_cancel(
    fake_container: FakeContainer,
) -> None:
    """A downloader can cancel their own task."""
    from app.services.progress_service import ProgressService

    user = _downloader_user('dl-owner')
    fake_container.progress_bus.start(300, 'ep300.mp4', status='正在下載', owner_id=user.id)

    proxy = FakeSchedulerProxy(up=True)
    # ProgressService reads from the local bus (no proxy) so the seeded task
    # is visible; TaskService still delegates cancel to the proxy.
    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        scheduler_proxy=proxy,
        progress_service=progress_service,
    )
    await service.cancel_task(300, user)

    assert 300 in proxy.cancel_calls


@pytest.mark.anyio
async def test_cancel_task_non_owner_gets_404(
    fake_container: FakeContainer,
) -> None:
    """A downloader cannot cancel a task owned by someone else — gets 404."""
    import fastapi

    from app.services.progress_service import ProgressService

    owner = _downloader_user('dl-owner')
    caller = _downloader_user('dl-other')
    # Seed the task as owned by 'owner', not 'caller'.
    fake_container.progress_bus.start(400, 'ep400.mp4', status='正在下載', owner_id=owner.id)

    proxy = FakeSchedulerProxy(up=True)
    # ProgressService reads from the local bus (no proxy) so the seeded task
    # is visible; TaskService still delegates cancel to the proxy.
    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        scheduler_proxy=proxy,
        progress_service=progress_service,
    )

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await service.cancel_task(400, caller)
    assert exc_info.value.status_code == 404
    # Proxy should NOT have been called.
    assert 400 not in proxy.cancel_calls


@pytest.mark.anyio
async def test_cancel_task_admin_can_cancel_any(
    fake_container: FakeContainer,
) -> None:
    """Admin can cancel any task regardless of owner."""
    from app.services.progress_service import ProgressService

    user = _admin_user()
    fake_container.progress_bus.start(500, 'ep500.mp4', status='正在下載', owner_id='some-dl-user')

    proxy = FakeSchedulerProxy(up=True)
    # ProgressService reads from the local bus (no proxy) so the seeded task
    # is visible; TaskService still delegates cancel to the proxy.
    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        scheduler_proxy=proxy,
        progress_service=progress_service,
    )
    await service.cancel_task(500, user)

    assert 500 in proxy.cancel_calls


@pytest.mark.anyio
async def test_cancel_task_raises_503_when_scheduler_down(
    fake_container: FakeContainer,
) -> None:
    """cancel_task raises 503 when the proxy reports scheduler is down."""
    import fastapi

    from app.services.progress_service import ProgressService

    user = _admin_user()
    fake_container.progress_bus.start(600, 'ep600.mp4', status='正在下載', owner_id=user.id)

    proxy = FakeSchedulerProxy(up=False)
    # ProgressService reads from the local bus (no proxy) so the seeded task
    # is visible; TaskService still delegates cancel to the proxy (which is down).
    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        scheduler_proxy=proxy,
        progress_service=progress_service,
    )

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await service.cancel_task(600, user)
    assert exc_info.value.status_code == 503

"""Unit tests for service classes (independent of FastAPI)."""

from __future__ import annotations

import asyncio
import datetime
import time
import unittest.mock

import anyio
import pytest

from app.models import ManualTaskRequest, WebSettings
from app.persistence.user_repo import UserRow
from app.services.config_service import ConfigService
from app.services.progress_service import ProgressService
from app.services.snlist_service import SnListService
from app.services.task_service import TaskService

from .conftest import FakeContainer, FakeManualRunner

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
    """Without a dramatiq broker, enqueue falls back to in-process ManualRunner."""
    runner = FakeManualRunner()
    # No broker: falls back to direct ManualRunner call in a daemon thread.
    service = TaskService(fake_container.settings_repo, runner)
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
async def test_task_service_enqueue_passes_bilingual_to_runner_fallback(
    fake_container: FakeContainer,
) -> None:
    """``bilingual`` on ManualTaskRequest must reach the in-process ManualRunner fallback."""
    runner = FakeManualRunner()
    service = TaskService(fake_container.settings_repo, runner)
    user = _admin_user()
    await service.enqueue(
        ManualTaskRequest(
            sn='321',
            resolution='1080',
            mode='all',
            bilingual=True,
        ),
        user,
    )

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not runner.run_calls:
        await asyncio.sleep(0.01)

    assert len(runner.run_calls) == 1
    assert runner.run_calls[0]['bilingual'] is True


@pytest.mark.anyio
async def test_task_service_enqueue_passes_bilingual_to_dramatiq_actor(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bilingual`` on ManualTaskRequest must reach ``run_download.send_with_options`` kwargs."""
    from app.tasks import download as download_tasks

    calls: list[dict] = []

    def _spy(*, kwargs: dict, **_: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(download_tasks.run_download, 'send_with_options', _spy)

    service = TaskService(fake_container.settings_repo, fake_container.manual_runner)
    user = _admin_user()
    await service.enqueue(
        ManualTaskRequest(sn='654', resolution='1080', mode='all', bilingual=True),
        user,
    )

    assert len(calls) == 1
    assert calls[0]['bilingual'] is True
    # Dramatiq dispatch succeeded — the in-process fallback must not have run.
    assert fake_container.manual_runner.run_calls == []


@pytest.mark.anyio
async def test_task_service_falls_back_to_config_resolution(
    fake_container: FakeContainer,
) -> None:
    runner = FakeManualRunner()
    service = TaskService(fake_container.settings_repo, runner)
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


# ---------------------------------------------------------------------------
# TaskService.enqueue — per-user in-flight cap (fix #7)
# ---------------------------------------------------------------------------


def _seed_inflight(fake_container: FakeContainer, *, count: int, owner_id: str, start_sn: int = 10_000) -> None:
    """Seed *count* running (not finished) tasks owned by *owner_id*."""
    for i in range(count):
        fake_container.progress_bus.start(start_sn + i, f'ep{start_sn + i}.mp4', status='正在下載', owner_id=owner_id)


@pytest.mark.anyio
async def test_enqueue_rejects_21st_task_for_downloader(
    fake_container: FakeContainer,
) -> None:
    """A downloader with 20 in-flight tasks gets 429 on the 21st submission."""
    import fastapi

    user = _downloader_user('cap-dl')
    _seed_inflight(fake_container, count=20, owner_id=user.id)

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        progress_service=progress_service,
    )

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await service.enqueue(ManualTaskRequest(sn='1', resolution='1080', mode='single'), user)
    assert exc_info.value.status_code == 429
    assert '任務過多' in exc_info.value.detail


@pytest.mark.anyio
async def test_enqueue_allows_task_under_the_cap(
    fake_container: FakeContainer,
) -> None:
    """A downloader with 19 in-flight tasks can still submit a 20th."""
    runner = FakeManualRunner()
    user = _downloader_user('cap-dl-ok')
    _seed_inflight(fake_container, count=19, owner_id=user.id)

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        runner,
        progress_service=progress_service,
    )
    await service.enqueue(ManualTaskRequest(sn='2', resolution='1080', mode='single'), user)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not runner.run_calls:
        await asyncio.sleep(0.01)
    assert len(runner.run_calls) == 1


@pytest.mark.anyio
async def test_enqueue_downloader_cap_is_scoped_to_own_tasks(
    fake_container: FakeContainer,
) -> None:
    """Another user's in-flight tasks never count against a downloader's cap."""
    runner = FakeManualRunner()
    user = _downloader_user('cap-dl-scoped')
    _seed_inflight(fake_container, count=20, owner_id='someone-else')

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        runner,
        progress_service=progress_service,
    )
    await service.enqueue(ManualTaskRequest(sn='3', resolution='1080', mode='single'), user)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not runner.run_calls:
        await asyncio.sleep(0.01)
    assert len(runner.run_calls) == 1


@pytest.mark.anyio
async def test_enqueue_admin_has_a_higher_cap(
    fake_container: FakeContainer,
) -> None:
    """Admin's cap (50) is higher than a downloader's (20) — 21 tasks are fine."""
    runner = FakeManualRunner()
    user = _admin_user('cap-admin')
    _seed_inflight(fake_container, count=21, owner_id='some-dl')

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        runner,
        progress_service=progress_service,
    )
    await service.enqueue(ManualTaskRequest(sn='4', resolution='1080', mode='single'), user)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not runner.run_calls:
        await asyncio.sleep(0.01)
    assert len(runner.run_calls) == 1


@pytest.mark.anyio
async def test_enqueue_admin_rejects_51st_task(
    fake_container: FakeContainer,
) -> None:
    """Admin's cap (50) still applies once enough tasks are in flight across all users."""
    import fastapi

    user = _admin_user('cap-admin-over')
    _seed_inflight(fake_container, count=50, owner_id='some-dl')

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        progress_service=progress_service,
    )

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await service.enqueue(ManualTaskRequest(sn='5', resolution='1080', mode='single'), user)
    assert exc_info.value.status_code == 429


@pytest.mark.anyio
async def test_enqueue_no_cap_check_without_progress_service(
    fake_container: FakeContainer,
) -> None:
    """Without a progress_service wired (CLI / stub env), the cap check is skipped."""
    runner = FakeManualRunner()
    user = _downloader_user('cap-no-svc')
    service = TaskService(fake_container.settings_repo, runner)  # no progress_service

    await service.enqueue(ManualTaskRequest(sn='6', resolution='1080', mode='single'), user)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not runner.run_calls:
        await asyncio.sleep(0.01)
    assert len(runner.run_calls) == 1


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

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        progress_bus=fake_container.progress_bus,
        progress_service=progress_service,
    )
    await service.cancel_task(300, user)

    assert fake_container.progress_bus.snapshot()[300].status == '已取消'


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

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        progress_bus=fake_container.progress_bus,
        progress_service=progress_service,
    )

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await service.cancel_task(400, caller)
    assert exc_info.value.status_code == 404
    # The task must not have been touched.
    assert fake_container.progress_bus.snapshot()[400].status == '正在下載'


@pytest.mark.anyio
async def test_cancel_task_admin_can_cancel_any(
    fake_container: FakeContainer,
) -> None:
    """Admin can cancel any task regardless of owner."""
    from app.services.progress_service import ProgressService

    user = _admin_user()
    fake_container.progress_bus.start(500, 'ep500.mp4', status='正在下載', owner_id='some-dl-user')

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        progress_bus=fake_container.progress_bus,
        progress_service=progress_service,
    )
    await service.cancel_task(500, user)

    assert fake_container.progress_bus.snapshot()[500].status == '已取消'


# ---------------------------------------------------------------------------
# TaskService — Bilibili source branch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_task_service_bilibili_dispatches_via_runner(
    fake_container: FakeContainer,
) -> None:
    """source='bilibili' branch allocates task_sn and calls bilibili_runner.run()."""
    bilibili_run_calls: list[dict] = []

    class FakeBilibiliRunner:
        def run(self, task_sn: int, *, bvid: str, resolution: str, classify: bool, owner_id: str | None = None) -> None:
            bilibili_run_calls.append({'task_sn': task_sn, 'bvid': bvid, 'resolution': resolution})

    user = _admin_user('bilibili-user')
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        task_id_map_repo=fake_container.task_id_map_repo,
        bilibili_runner=FakeBilibiliRunner(),  # type: ignore[arg-type]
    )

    request = ManualTaskRequest(
        sn='BV1xx411c7mD',
        resolution='1080',
        mode='single',
        thread=1,
        classify=True,
        danmu=False,
        source='bilibili',
    )

    with unittest.mock.patch('app.downloader.bilibili.url_parser.parse_bilibili_input') as mock_parse:
        mock_parse.return_value = ('BV1xx411c7mD', 170001, False)
        await service.enqueue(request, user)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not bilibili_run_calls:
        await asyncio.sleep(0.01)

    assert bilibili_run_calls, 'bilibili_runner.run was never called'
    call = bilibili_run_calls[0]
    assert call['bvid'] == 'BV1xx411c7mD'
    assert call['task_sn'] > 2**31


@pytest.mark.anyio
async def test_task_service_bilibili_bad_url_returns_400(
    fake_container: FakeContainer,
) -> None:
    """Invalid Bilibili URL → 400 HTTPException."""
    import fastapi

    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        task_id_map_repo=fake_container.task_id_map_repo,
    )

    request = ManualTaskRequest(
        sn='not-a-bilibili-url',
        resolution='1080',
        mode='single',
        thread=1,
        classify=True,
        danmu=False,
        source='bilibili',
    )

    with (
        unittest.mock.patch('app.downloader.bilibili.url_parser.parse_bilibili_input', side_effect=ValueError('bad')),
        pytest.raises(fastapi.HTTPException) as exc_info,
    ):
        await service.enqueue(request, user=_admin_user())

    assert exc_info.value.status_code == 400
    assert '無法解析' in exc_info.value.detail


@pytest.mark.anyio
async def test_task_service_b23_link_defers_resolution_off_request_path(
    fake_container: FakeContainer,
) -> None:
    """fix #20: a b23.tv short link must NOT be resolved synchronously inside
    enqueue() — that requires a synchronous HTTP redirect (up to 10s). The
    resolution is deferred to the fallback thread (no broker in tests, so the
    in-process path runs); task_sn is allocated against the raw link since
    the bvid isn't known yet at allocation time."""
    import threading

    bilibili_run_calls: list[dict] = []
    resolve_event = threading.Event()

    class FakeBilibiliRunner:
        def run(self, task_sn: int, *, bvid: str, resolution: str, classify: bool, owner_id: str | None = None) -> None:
            bilibili_run_calls.append({'task_sn': task_sn, 'bvid': bvid, 'resolution': resolution})

    user = _admin_user('b23-user')
    service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        task_id_map_repo=fake_container.task_id_map_repo,
        bilibili_runner=FakeBilibiliRunner(),  # type: ignore[arg-type]
    )

    raw_link = 'https://b23.tv/abcd1234'
    request = ManualTaskRequest(
        sn=raw_link,
        resolution='1080',
        mode='single',
        thread=1,
        classify=True,
        danmu=False,
        source='bilibili',
    )

    def _blocking_parse(s: str) -> tuple[str, int, bool]:
        # Simulates the network-bound b23 redirect: blocks until the test
        # signals it (bounded by its own 2s timeout so a regression here
        # can't hang the suite — if enqueue() awaited this synchronously,
        # the "not bilibili_run_calls" assertion below would simply fail
        # once the internal wait times out and returns anyway).
        resolve_event.wait(timeout=2.0)
        return 'BV1yy422d8nE', 280002, False

    with unittest.mock.patch('app.downloader.bilibili.url_parser.parse_bilibili_input', side_effect=_blocking_parse):
        await service.enqueue(request, user)

        # enqueue() returned already, but resolve_event hasn't been set yet —
        # proves the resolution (and thus bilibili_runner.run) hasn't run.
        assert not bilibili_run_calls, 'resolution must not complete before enqueue() returns'

        resolve_event.set()  # let the deferred resolution + run proceed

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not bilibili_run_calls:
            await anyio.sleep(0.01)  # anyio (not asyncio) sleep — must work under both the asyncio and trio backends

    assert bilibili_run_calls, 'bilibili_runner.run was never called after deferred resolution'
    call = bilibili_run_calls[0]
    assert call['bvid'] == 'BV1yy422d8nE'

    # task_sn was allocated against the raw b23 link (the resolved bvid
    # wasn't known yet at allocation time) — allocate() is idempotent per
    # (source, external_id), so calling it again returns the same task_sn.
    mapped_sn = fake_container.task_id_map_repo.allocate(source='bilibili', external_id=raw_link)
    assert mapped_sn == call['task_sn']

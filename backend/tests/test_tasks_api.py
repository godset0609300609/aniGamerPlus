"""Tests for ``/api/tasks/manual``, ``DELETE /api/tasks/{sn}``,
and ``GET /api/tasks/history`` endpoints.
"""

from __future__ import annotations

import datetime
import time

import fastapi.testclient

from app.api.tasks_api import get_task_history_repo
from app.persistence.task_history_repo import TaskHistoryEntry

from .conftest import FakeContainer, FakeManualRunner, FakeSchedulerProxy

# ---------------------------------------------------------------------------
# Fake TaskHistoryRepository for API tests
# ---------------------------------------------------------------------------


class FakeHistoryRepo:
    """Minimal fake for GET /tasks/history tests."""

    def __init__(self, rows: list[TaskHistoryEntry] | None = None) -> None:
        self._rows = rows or []

    def list_recent(self, days: int = 7, user_id: str | None = None) -> list[TaskHistoryEntry]:
        if user_id is None:
            return list(self._rows)
        return [r for r in self._rows if r.owner_id == user_id]


def _make_entry(sn: int = 1, owner_id: str | None = None) -> TaskHistoryEntry:
    now = datetime.datetime.now(datetime.UTC)
    return TaskHistoryEntry(
        id=sn,
        sn=sn,
        owner_id=owner_id,
        filename=f'ep{sn}.mp4',
        bangumi_name='AoT',
        episode='第01話',
        resolution='1080p',
        final_status='下載完成',
        started_at=now - datetime.timedelta(hours=1),
        finished_at=now,
        retries=0,
    )


def _wait_for_run(runner: FakeManualRunner, expected: int, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(runner.run_calls) >= expected:
            return
        time.sleep(0.01)
    raise AssertionError(f'Expected {expected} run call(s); saw {len(runner.run_calls)}')


def test_manual_task_happy_path(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    payload = {
        'sn': '12345',
        'resolution': '720',
        'mode': 'single',
        'thread': 2,
        'classify': True,
        'danmu': False,
    }

    r = client.post('/api/tasks/manual', json=payload)
    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}

    _wait_for_run(fake_container.manual_runner, 1)
    call = fake_container.manual_runner.run_calls[0]
    assert call['sn'] == 12345
    assert call['resolution'] == '720'
    assert call['mode'] == 'single'
    assert call['thread_limit'] == 2
    assert call['classify'] is True
    assert call['cui_danmu'] is False
    assert call['realtime_show'] is False
    # Sentinel admin owner_id is propagated.
    assert call['owner_id'] == '__anonymous_admin__'


def test_manual_task_rejects_invalid_resolution(
    client: fastapi.testclient.TestClient,
) -> None:
    r = client.post(
        '/api/tasks/manual',
        json={'sn': '1', 'resolution': '9999', 'mode': 'single'},
    )
    assert r.status_code == 422


def test_manual_task_rejects_invalid_mode(
    client: fastapi.testclient.TestClient,
) -> None:
    r = client.post(
        '/api/tasks/manual',
        json={'sn': '1', 'resolution': '1080', 'mode': 'bogus'},
    )
    assert r.status_code == 422


def test_manual_task_rejects_thread_over_range(
    client: fastapi.testclient.TestClient,
) -> None:
    r = client.post(
        '/api/tasks/manual',
        json={'sn': '1', 'resolution': '1080', 'mode': 'single', 'thread': 999},
    )
    assert r.status_code == 422


def test_manual_task_thread_is_clamped_to_max_multi_thread(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    r = client.post(
        '/api/tasks/manual',
        json={'sn': '1', 'resolution': '1080', 'mode': 'single', 'thread': 10},
    )
    assert r.status_code == 200
    _wait_for_run(fake_container.manual_runner, 1)
    # TaskService clamps to the hard-coded ``_MAX_MULTI_THREAD`` of 5.
    assert fake_container.manual_runner.run_calls[0]['thread_limit'] == 5


# ---------------------------------------------------------------------------
# DELETE /api/tasks/{sn} — cancel task
# ---------------------------------------------------------------------------


def _seed_progress(fake_container: FakeContainer, sn: int, owner_id: str) -> None:
    """Seed a running task in the progress bus under the given owner."""
    fake_container.progress_bus.start(sn, f'ep{sn}.mp4', status='正在下載', owner_id=owner_id)


def test_cancel_task_admin_can_cancel_any(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    """The sentinel admin (which the client fixture always uses) can cancel any task."""
    proxy = FakeSchedulerProxy(up=True)
    fake_container.scheduler_proxy = proxy  # type: ignore[assignment]

    _seed_progress(fake_container, sn=555, owner_id='some-other-user')

    # Re-build the app with the proxy wired — done via dependency_overrides.
    # ProgressService reads from the local bus (no proxy) so the seeded task
    # is visible; TaskService still delegates cancel to the proxy.
    from app.services.progress_service import ProgressService
    from app.services.task_service import TaskService, get_task_service

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    task_service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        proxy,
        progress_service=progress_service,
    )
    client.app.dependency_overrides[get_task_service] = lambda: task_service  # type: ignore[attr-defined]

    r = client.delete('/api/tasks/555')
    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}
    assert 555 in proxy.cancel_calls


def test_cancel_task_404_when_not_in_snapshot(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """DELETE /api/tasks/{sn} returns 404 when the task is not in the user's snapshot."""
    proxy = FakeSchedulerProxy(up=True)

    from app.services.progress_service import ProgressService
    from app.services.task_service import TaskService, get_task_service

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo, proxy)
    task_service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        proxy,
        progress_service=progress_service,
    )
    client.app.dependency_overrides[get_task_service] = lambda: task_service  # type: ignore[attr-defined]

    # sn=9999 is not in the progress bus at all — 404 expected.
    r = client.delete('/api/tasks/9999')
    assert r.status_code == 404


def test_cancel_task_503_when_scheduler_down(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """DELETE /api/tasks/{sn} returns 503 when the scheduler is unreachable."""
    proxy = FakeSchedulerProxy(up=False)

    _seed_progress(fake_container, sn=777, owner_id='__anonymous_admin__')

    # ProgressService reads from the local bus (no proxy) so the seeded task
    # is visible; TaskService still delegates cancel to the proxy (which is down).
    from app.services.progress_service import ProgressService
    from app.services.task_service import TaskService, get_task_service

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    task_service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        proxy,
        progress_service=progress_service,
    )
    client.app.dependency_overrides[get_task_service] = lambda: task_service  # type: ignore[attr-defined]

    r = client.delete('/api/tasks/777')
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/tasks/history
# ---------------------------------------------------------------------------


def test_task_history_returns_entries(
    client: fastapi.testclient.TestClient,
) -> None:
    """GET /api/tasks/history returns serialised history entries."""
    entry = _make_entry(sn=1, owner_id='__anonymous_admin__')
    fake_repo = FakeHistoryRepo(rows=[entry])
    client.app.dependency_overrides[get_task_history_repo] = lambda: fake_repo  # type: ignore[attr-defined]

    r = client.get('/api/tasks/history')
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]['sn'] == 1
    assert data[0]['final_status'] == '下載完成'
    assert data[0]['filename'] == 'ep1.mp4'

    # Clean up override.
    client.app.dependency_overrides.pop(get_task_history_repo, None)  # type: ignore[attr-defined]


def test_task_history_days_query_param(
    client: fastapi.testclient.TestClient,
) -> None:
    """GET /api/tasks/history?days=14 accepts custom days parameter."""
    fake_repo = FakeHistoryRepo(rows=[])
    client.app.dependency_overrides[get_task_history_repo] = lambda: fake_repo  # type: ignore[attr-defined]

    r = client.get('/api/tasks/history?days=14')
    assert r.status_code == 200

    client.app.dependency_overrides.pop(get_task_history_repo, None)  # type: ignore[attr-defined]


def test_task_history_days_must_be_positive(
    client: fastapi.testclient.TestClient,
) -> None:
    """GET /api/tasks/history?days=0 should return 422."""
    r = client.get('/api/tasks/history?days=0')
    assert r.status_code == 422


def test_task_history_admin_sees_all_users(
    client: fastapi.testclient.TestClient,
) -> None:
    """Sentinel admin (no auth) should receive all users' history."""
    entries = [
        _make_entry(sn=10, owner_id='alice'),
        _make_entry(sn=11, owner_id='bob'),
    ]
    fake_repo = FakeHistoryRepo(rows=entries)
    client.app.dependency_overrides[get_task_history_repo] = lambda: fake_repo  # type: ignore[attr-defined]

    r = client.get('/api/tasks/history')
    assert r.status_code == 200
    data = r.json()
    # Sentinel admin → user_filter=None → all rows returned.
    assert len(data) == 2

    client.app.dependency_overrides.pop(get_task_history_repo, None)  # type: ignore[attr-defined]

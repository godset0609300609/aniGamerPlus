"""Tests for ``/api/tasks/manual``, ``DELETE /api/tasks/{sn}``,
and ``GET /api/tasks/history`` endpoints.
"""

from __future__ import annotations

import datetime
import time

import fastapi.testclient
import pytest

from app.api.tasks_api import get_task_history_repo
from app.persistence.task_history_repo import TaskHistoryEntry

from .conftest import FakeContainer, FakeManualRunner

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


def test_manual_task_bilingual_flag_reaches_service(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """``bilingual: true`` in the request body must reach ManualRunner.run."""
    payload = {
        'sn': '99001',
        'resolution': '1080',
        'mode': 'all',
        'thread': 1,
        'classify': True,
        'danmu': False,
        'bilingual': True,
    }

    r = client.post('/api/tasks/manual', json=payload)
    assert r.status_code == 200

    _wait_for_run(fake_container.manual_runner, 1)
    call = fake_container.manual_runner.run_calls[0]
    assert call['sn'] == 99001
    assert call['bilingual'] is True


def test_manual_task_bilingual_defaults_to_false(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """Omitting ``bilingual`` in the request body must default to False."""
    payload = {
        'sn': '99002',
        'resolution': '1080',
        'mode': 'single',
        'thread': 1,
        'classify': True,
        'danmu': False,
    }

    r = client.post('/api/tasks/manual', json=payload)
    assert r.status_code == 200

    _wait_for_run(fake_container.manual_runner, 1)
    call = fake_container.manual_runner.run_calls[0]
    assert call['sn'] == 99002
    assert call['bilingual'] is False


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


def test_manual_task_burst_past_rate_limit_returns_429(
    client: fastapi.testclient.TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/tasks/manual is rate-limited (fix #17) — burst past the env-configured cap."""
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TASKS_MANUAL', '2/minute')

    payload = {'sn': '1', 'resolution': '1080', 'mode': 'single'}
    r1 = client.post('/api/tasks/manual', json=payload)
    r2 = client.post('/api/tasks/manual', json=payload)
    r3 = client.post('/api/tasks/manual', json=payload)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


def test_manual_task_429_when_caller_has_too_many_inflight(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """POST /api/tasks/manual returns 429 once the caller's cap (fix #7) is hit.

    The ``client`` fixture always acts as the sentinel admin, whose cap is
    50 (see ``TaskService._MAX_INFLIGHT_PER_ADMIN``).
    """
    from app.services.progress_service import ProgressService
    from app.services.task_service import TaskService, get_task_service

    for i in range(50):
        fake_container.progress_bus.start(20_000 + i, f'ep{i}.mp4', status='正在下載', owner_id='__anonymous_admin__')

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    task_service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        progress_service=progress_service,
    )
    client.app.dependency_overrides[get_task_service] = lambda: task_service  # type: ignore[attr-defined]

    r = client.post(
        '/api/tasks/manual',
        json={'sn': '1', 'resolution': '1080', 'mode': 'single'},
    )
    assert r.status_code == 429
    assert '任務過多' in r.json()['detail']


# ---------------------------------------------------------------------------
# DELETE /api/tasks/{sn} — cancel task
# ---------------------------------------------------------------------------


def _seed_progress(fake_container: FakeContainer, sn: int, owner_id: str) -> None:
    """Seed a running task in the progress bus under the given owner."""
    fake_container.progress_bus.start(sn, f'ep{sn}.mp4', status='正在下載', owner_id=owner_id)


def test_cancel_task_admin_can_cancel_any(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    """The sentinel admin (which the client fixture always uses) can cancel any task."""
    _seed_progress(fake_container, sn=555, owner_id='some-other-user')

    from app.services.progress_service import ProgressService
    from app.services.task_service import TaskService, get_task_service

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    task_service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        progress_bus=fake_container.progress_bus,
        progress_service=progress_service,
    )
    client.app.dependency_overrides[get_task_service] = lambda: task_service  # type: ignore[attr-defined]

    r = client.delete('/api/tasks/555')
    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}
    assert fake_container.progress_bus.snapshot()[555].status == '已取消'


def test_cancel_task_404_when_not_in_snapshot(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """DELETE /api/tasks/{sn} returns 404 when the task is not in the user's snapshot."""
    from app.services.progress_service import ProgressService
    from app.services.task_service import TaskService, get_task_service

    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    task_service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
        progress_bus=fake_container.progress_bus,
        progress_service=progress_service,
    )
    client.app.dependency_overrides[get_task_service] = lambda: task_service  # type: ignore[attr-defined]

    # sn=9999 is not in the progress bus at all — 404 expected.
    r = client.delete('/api/tasks/9999')
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/monitor/progress/{sn}/force-finish — dismiss ghost/stuck cards
# ---------------------------------------------------------------------------


def test_dismiss_progress_by_sn_calls_force_finish(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """POST .../force-finish marks the entry terminal (status='已取消') so the
    next WS snapshot excludes it — the fix for ghost cards that a plain
    cancel() cannot reach because their owning process is already dead."""
    _seed_progress(fake_container, sn=888, owner_id='__anonymous_admin__')

    r = client.post('/api/monitor/progress/888/force-finish')

    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}
    entry = fake_container.progress_bus.snapshot()[888]
    assert entry.status == '已取消'
    assert entry.finished_at is not None


def test_dismiss_progress_admin_can_dismiss_any_owners_task(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """The sentinel admin (which the client fixture always uses) can dismiss any task."""
    _seed_progress(fake_container, sn=890, owner_id='some-other-user')

    r = client.post('/api/monitor/progress/890/force-finish')

    assert r.status_code == 200
    assert fake_container.progress_bus.snapshot()[890].status == '已取消'


def test_dismiss_forbidden_for_non_owner_non_admin(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """A downloader cannot dismiss another user's task — 403, and the entry is untouched."""
    _seed_progress(fake_container, sn=889, owner_id='some-other-user')

    from app.api.deps import current_user_opt
    from app.persistence.user_repo import UserRow

    caller = UserRow(
        id='caller-user',
        username='caller',
        avatar_url=None,
        role='downloader',
        created_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
        last_login_at=None,
    )
    client.app.dependency_overrides[current_user_opt] = lambda: caller  # type: ignore[attr-defined]

    r = client.post('/api/monitor/progress/889/force-finish')

    assert r.status_code == 403
    assert fake_container.progress_bus.snapshot()[889].status == '正在下載'


def test_dismiss_progress_404_when_not_in_snapshot(
    client: fastapi.testclient.TestClient,
) -> None:
    """POST .../force-finish returns 404 when the sn is not tracked at all."""
    r = client.post('/api/monitor/progress/777777/force-finish')
    assert r.status_code == 404


def test_dismiss_progress_is_idempotent_on_already_terminal_entry(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """Dismissing an already-terminal entry is a no-op, not an error — clicking X
    twice (or after the task genuinely finished) must not clobber the real outcome."""
    _seed_progress(fake_container, sn=891, owner_id='__anonymous_admin__')
    fake_container.progress_bus.update_status(891, '下載完成')
    fake_container.progress_bus.finish(891)

    r = client.post('/api/monitor/progress/891/force-finish')

    assert r.status_code == 200
    assert fake_container.progress_bus.snapshot()[891].status == '下載完成'


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


# ---------------------------------------------------------------------------
# Regression: source / external_id must survive the repo → API path
# ---------------------------------------------------------------------------


def test_task_history_returns_source_and_external_id(
    client: fastapi.testclient.TestClient,
) -> None:
    """GET /api/tasks/history must include source and external_id when present."""
    now = datetime.datetime.now(datetime.UTC)
    entry = TaskHistoryEntry(
        id=99,
        sn=99,
        owner_id='__anonymous_admin__',
        filename='BV1xxx.mp4',
        bangumi_name='鬼滅の刃',
        episode='第01話',
        resolution='1080p',
        final_status='下載完成',
        started_at=now - datetime.timedelta(hours=1),
        finished_at=now,
        retries=0,
        source='bilibili',
        external_id='BV1xxx',
    )
    fake_repo = FakeHistoryRepo(rows=[entry])
    client.app.dependency_overrides[get_task_history_repo] = lambda: fake_repo  # type: ignore[attr-defined]

    r = client.get('/api/tasks/history')
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    row = data[0]
    assert row['source'] == 'bilibili'
    assert row['external_id'] == 'BV1xxx'

    client.app.dependency_overrides.pop(get_task_history_repo, None)  # type: ignore[attr-defined]


def test_task_history_source_and_external_id_null_when_absent(
    client: fastapi.testclient.TestClient,
) -> None:
    """GET /api/tasks/history returns null for source/external_id when they are not set."""
    entry = _make_entry(sn=1, owner_id='__anonymous_admin__')
    fake_repo = FakeHistoryRepo(rows=[entry])
    client.app.dependency_overrides[get_task_history_repo] = lambda: fake_repo  # type: ignore[attr-defined]

    r = client.get('/api/tasks/history')
    assert r.status_code == 200
    data = r.json()
    assert data[0]['source'] is None
    assert data[0]['external_id'] is None

    client.app.dependency_overrides.pop(get_task_history_repo, None)  # type: ignore[attr-defined]

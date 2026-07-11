"""Parametrised permission tests for every protected route.

Strategy
--------
Each test uses ``app.dependency_overrides[current_user_opt]`` to inject a
specific user (anonymous=None, downloader, admin) without touching the real
session or DB auth layer.  This is simpler and faster than setting up real
Discord sessions while still exercising the full RBAC path.

``auth.enabled`` is implicitly ``True`` for anonymous tests because
``current_user_opt`` returns ``None`` → ``require_any_user`` raises 401.
For admin / downloader tests the override short-circuits auth entirely.
"""

from __future__ import annotations

import datetime
from typing import Any

import fastapi
import fastapi.testclient
import pytest

from app.api.deps import current_user_opt
from app.persistence.user_repo import UserRow

# ---------------------------------------------------------------------------
# User factories
# ---------------------------------------------------------------------------


def _make_user(role: str, uid: str = 'test-user-1') -> UserRow:
    return UserRow(
        id=uid,
        username=f'test_{role}',
        avatar_url=None,
        role=role,
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )


_ADMIN_USER = _make_user('admin', uid='test-admin-1')
_DOWNLOADER_USER = _make_user('downloader', uid='test-downloader-1')

# ``None`` simulates no session (anonymous).
_ANONYMOUS: UserRow | None = None


# ---------------------------------------------------------------------------
# Fixture: build a test app with all deps overridden at fake_container.
# ---------------------------------------------------------------------------


@pytest.fixture
def _perm_app(fake_container: Any, monkeypatch: pytest.MonkeyPatch) -> fastapi.FastAPI:
    """Build a fresh FastAPI app bound to ``fake_container``, no auth bypass."""
    monkeypatch.setenv('ANIGAMERPLUS_DISABLE_SCHEDULER', '1')

    from app.api.health import HealthService, get_health_service
    from app.main import DashboardApp
    from app.services import (
        AnimeListService,
        ConfigService,
        ProgressService,
        SnListService,
        TaskService,
        get_animelist_service,
        get_config_service,
        get_progress_service,
        get_snlist_service,
        get_task_service,
    )

    from .conftest import _container_proxy

    container_proxy = _container_proxy(fake_container)
    app = DashboardApp(container_proxy).app

    config_service = ConfigService(fake_container.settings_repo)
    snlist_service = SnListService(fake_container.sn_list_repo)
    animelist_service = AnimeListService(
        fake_container.sn_list_repo,
        fake_container.anime_repo,
        fake_container.anime_list_entry_repo,
        fake_container.user_repo,
    )
    task_service = TaskService(fake_container.settings_repo, fake_container.manual_runner)
    progress_service = ProgressService(fake_container.progress_bus, fake_container.user_repo)
    health_service = HealthService(fake_container.paths)

    app.dependency_overrides[get_config_service] = lambda: config_service
    app.dependency_overrides[get_snlist_service] = lambda: snlist_service
    app.dependency_overrides[get_animelist_service] = lambda: animelist_service
    app.dependency_overrides[get_task_service] = lambda: task_service
    app.dependency_overrides[get_progress_service] = lambda: progress_service
    app.dependency_overrides[get_health_service] = lambda: health_service

    return app


def _make_client(app: fastapi.FastAPI, user: UserRow | None) -> fastapi.testclient.TestClient:
    """Return a TestClient with ``current_user_opt`` fixed to ``user``."""
    app.dependency_overrides[current_user_opt] = lambda: user
    return fastapi.testclient.TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Parametrised permission matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'path,method,body,expected_anon,expected_downloader,expected_admin',
    [
        # Config read — any authenticated user
        ('/api/config', 'GET', None, 401, 200, 200),
        # Config write — admin only
        (
            '/api/config',
            'PUT',
            {
                'bangumi_dir': '',
                'temp_dir': '',
                'classify_bangumi': True,
                'lock_resolution': False,
                'segment_download_mode': True,
                'add_bangumi_name_to_video_filename': True,
                'add_resolution_to_video_filename': True,
                'download_resolution': '1080',
                'default_download_mode': 'latest',
                'check_frequency': 5,
                'multi-thread': 1,
                'multi_downloading_segment': 2,
                'customized_video_filename_prefix': '',
                'customized_video_filename_suffix': '',
                'ua': '',
                'use_mobile_api': False,
                'danmu': False,
                'use_proxy': False,
                'proxy': '',
                'read_sn_list_when_checking_update': True,
                'read_config_when_checking_update': True,
                'save_logs': True,
                'quantity_of_logs': 7,
                'download_cd': 60,
                'parse_sn_cd': 5,
            },
            401,
            403,
            200,
        ),
        # Legacy sn_list — admin only
        ('/api/sn_list', 'GET', None, 401, 403, 200),
        ('/api/sn_list', 'PUT', None, 401, 403, 200),
        # Anime list — any authenticated user (filtered by role)
        ('/api/anime-list', 'GET', None, 401, 200, 200),
        ('/api/anime-list', 'PUT', {'entries': []}, 401, 200, 200),
        # Manual task — any authenticated user
        (
            '/api/tasks/manual',
            'POST',
            {
                'sn': '12345',
                'resolution': '1080',
                'mode': 'single',
                'thread': 1,
                'classify': True,
                'danmu': False,
            },
            401,
            200,
            200,
        ),
        # Health — public
        ('/api/health', 'GET', None, 200, 200, 200),
    ],
)
def test_route_permissions(
    _perm_app: fastapi.FastAPI,
    path: str,
    method: str,
    body: dict | None,
    expected_anon: int,
    expected_downloader: int,
    expected_admin: int,
) -> None:
    """Each route returns the expected status code for each role."""
    for user, expected in [
        (_ANONYMOUS, expected_anon),
        (_DOWNLOADER_USER, expected_downloader),
        (_ADMIN_USER, expected_admin),
    ]:
        client = _make_client(_perm_app, user)
        if method == 'GET':
            resp = client.get(path)
        elif method == 'PUT':
            if body is None:
                resp = client.put(path, content=b'', headers={'Content-Type': 'text/plain'})
            else:
                resp = client.put(path, json=body)
        elif method == 'POST':
            resp = client.post(path, json=body or {})
        else:
            raise NotImplementedError(method)

        role_label = user.role if user is not None else 'anonymous'
        assert resp.status_code == expected, (
            f'{method} {path} as {role_label}: expected {expected}, got {resp.status_code}. Body: {resp.text}'
        )


# ---------------------------------------------------------------------------
# Downloader entry ownership tests
# ---------------------------------------------------------------------------


def test_downloader_cannot_write_other_users_entries(
    _perm_app: fastapi.FastAPI,
) -> None:
    """A downloader's PUT is rejected when payload contains foreign owner_id."""
    client = _make_client(_perm_app, _DOWNLOADER_USER)
    payload = {
        'entries': [
            {
                'sn': 12345,
                'enabled': True,
                'mode': None,
                'tag': '',
                'season': 1,
                'comment': '',
                'owner_id': 'some-other-user-id',  # not the downloader's id
            }
        ]
    }
    resp = client.put('/api/anime-list', json=payload)
    assert resp.status_code == 400


def test_downloader_can_write_own_entries(_perm_app: fastapi.FastAPI, fake_container: Any) -> None:
    """A downloader can PUT entries with no owner_id (auto-assigned) or own id."""
    # Insert the user so the DB FK is satisfied when checking.
    fake_container.user_repo.upsert(
        id=_DOWNLOADER_USER.id,
        username=_DOWNLOADER_USER.username,
        avatar_url=None,
        role='downloader',
    )

    client = _make_client(_perm_app, _DOWNLOADER_USER)
    payload = {
        'entries': [
            {
                'sn': 99999,
                'enabled': True,
                'mode': None,
                'tag': '',
                'season': 1,
                'comment': '',
                'owner_id': None,  # auto-assign to caller
            }
        ]
    }
    resp = client.put('/api/anime-list', json=payload)
    assert resp.status_code == 200

    # Downloader can only see own entries.
    resp2 = client.get('/api/anime-list')
    assert resp2.status_code == 200
    entries = resp2.json()['entries']
    assert len(entries) == 1
    assert entries[0]['sn'] == 99999


def test_admin_sees_all_entries(_perm_app: fastapi.FastAPI, fake_container: Any) -> None:
    """An admin can see entries from all users."""
    # Seed two users' entries via their own clients.
    downloader2 = _make_user('downloader', uid='downloader-2')

    fake_container.user_repo.upsert(
        id=_DOWNLOADER_USER.id,
        username=_DOWNLOADER_USER.username,
        avatar_url=None,
        role='downloader',
    )
    fake_container.user_repo.upsert(
        id=downloader2.id,
        username=downloader2.username,
        avatar_url=None,
        role='downloader',
    )

    # Note: _make_client mutates app.dependency_overrides, so re-bind the
    # override right before each call rather than holding client objects.
    _make_client(_perm_app, _DOWNLOADER_USER).put(
        '/api/anime-list',
        json={
            'entries': [
                {'sn': 11111, 'enabled': True, 'mode': None, 'tag': '', 'season': 1, 'comment': ''},
            ]
        },
    )
    _make_client(_perm_app, downloader2).put(
        '/api/anime-list',
        json={
            'entries': [
                {'sn': 22222, 'enabled': True, 'mode': None, 'tag': '', 'season': 1, 'comment': ''},
            ]
        },
    )

    resp = _make_client(_perm_app, _ADMIN_USER).get('/api/anime-list')
    assert resp.status_code == 200
    sns = {e['sn'] for e in resp.json()['entries']}
    assert 11111 in sns
    assert 22222 in sns


# ---------------------------------------------------------------------------
# Progress snapshot filtering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_downloader_sees_only_own_progress(_perm_app: fastapi.FastAPI, fake_container: Any) -> None:
    """Downloader's progress snapshot only contains their own tasks."""
    bus = fake_container.progress_bus
    bus.start(1001, 'task_A.mp4', owner_id=_DOWNLOADER_USER.id)
    bus.start(1002, 'task_B.mp4', owner_id='other-user-id')

    from app.services import get_progress_service
    from app.services.progress_service import ProgressService

    progress_service = ProgressService(bus, fake_container.user_repo)
    _perm_app.dependency_overrides[get_progress_service] = lambda: progress_service

    # We can't easily test the WS endpoint here; test the service directly.
    snapshot = await progress_service.snapshot(_DOWNLOADER_USER)
    assert '1001' in snapshot.tasks
    assert '1002' not in snapshot.tasks


@pytest.mark.anyio
async def test_admin_sees_all_progress(_perm_app: fastapi.FastAPI, fake_container: Any) -> None:
    """Admin's progress snapshot contains tasks from all users."""
    bus = fake_container.progress_bus
    bus.start(2001, 'task_C.mp4', owner_id=_DOWNLOADER_USER.id)
    bus.start(2002, 'task_D.mp4', owner_id='other-user-id')

    from app.services.progress_service import ProgressService

    progress_service = ProgressService(bus, fake_container.user_repo)
    snapshot = await progress_service.snapshot(_ADMIN_USER)
    assert '2001' in snapshot.tasks
    assert '2002' in snapshot.tasks

"""Tests for ``/api/config`` endpoints."""

from __future__ import annotations

import datetime
import pathlib

import fastapi
import fastapi.testclient

from app.api.deps import current_user_opt
from app.persistence.user_repo import UserRow

from .conftest import FakeContainer

# ---------------------------------------------------------------------------
# Helper: build a downloader-role user and a client that uses it.
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


def _make_downloader_client(
    base_client: fastapi.testclient.TestClient,
) -> fastapi.testclient.TestClient:
    """Return a new TestClient bound to the same app but impersonating a downloader.

    We reach into the ASGI app through the TestClient's ``app`` attribute and
    temporarily override ``current_user_opt`` to return a downloader user.
    """
    app = base_client.app
    app.dependency_overrides[current_user_opt] = lambda: _make_user('downloader')
    return fastapi.testclient.TestClient(app, raise_server_exceptions=True)


def test_schema_returns_whitelist(client: fastapi.testclient.TestClient) -> None:
    r = client.get('/api/config/schema')
    assert r.status_code == 200
    body = r.json()
    assert 'bangumi_dir' in body['keys']
    assert 'proxy' in body['keys']
    assert 'multi-thread' in body['keys']


def test_get_config_returns_only_whitelisted_keys(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
    tmp_path: pathlib.Path,
) -> None:
    # Seed a setting so we can check it comes back on read. The
    # repository's ``_normalise`` replaces a non-existent bangumi_dir with
    # the workspace default; pick a real path so the value survives.
    new_dir = tmp_path / 'custom_bangumi'
    new_dir.mkdir()
    current = fake_container.settings_repo.load()
    updated = current.model_copy(update={'bangumi_dir': str(new_dir)})
    fake_container.settings_repo.save(updated)

    r = client.get('/api/config')
    assert r.status_code == 200
    body = r.json()

    # Non-web keys like ``ftp`` must not leak onto the wire.
    assert 'ftp' not in body
    assert body['bangumi_dir'] == str(new_dir)
    assert body['download_resolution'] == '1080'
    # The wire name is ``multi-thread`` (pydantic alias).
    assert body['multi-thread'] == 1


def test_put_config_round_trips_through_pydantic(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
    tmp_path: pathlib.Path,
) -> None:
    new_bangumi = tmp_path / 'new_bangumi'
    new_bangumi.mkdir()
    new_temp = tmp_path / 'new_temp'
    new_temp.mkdir()

    payload = {
        'bangumi_dir': str(new_bangumi),
        'temp_dir': str(new_temp),
        'classify_bangumi': True,
        'lock_resolution': False,
        'segment_download_mode': True,
        'add_bangumi_name_to_video_filename': True,
        'add_resolution_to_video_filename': True,
        'download_resolution': '720',
        'default_download_mode': 'all',
        'check_frequency': 5,
        'multi-thread': 3,
        'multi_downloading_segment': 2,
        'customized_video_filename_prefix': '',
        'customized_video_filename_suffix': '',
        'ua': 'Mozilla/5.0',
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
    }

    r = client.put('/api/config', json=payload)
    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}

    persisted = fake_container.settings_repo.load()
    assert persisted.bangumi_dir == str(new_bangumi)
    assert persisted.multi_thread == 3
    assert persisted.download_resolution == '720'


def test_put_config_rejects_invalid_resolution(
    client: fastapi.testclient.TestClient,
) -> None:
    payload = {'download_resolution': '9999'}
    r = client.put('/api/config', json=payload)
    assert r.status_code == 422


def test_put_config_rejects_negative_thread(
    client: fastapi.testclient.TestClient,
) -> None:
    payload = {'multi-thread': 0}
    r = client.put('/api/config', json=payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Cookie endpoints
# ---------------------------------------------------------------------------


def test_put_cookie_admin_writes(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
) -> None:
    """Admin PUT /config/cookie → 200 and cookie file written to disk."""
    from app.api.config_api import get_cookie_repo

    client.app.dependency_overrides[get_cookie_repo] = lambda: fake_container.cookie_repo  # type: ignore[attr-defined]

    cookie_value = 'BAHAMUT_SESSID=abc123; other=val'
    r = client.put('/api/config/cookie', json={'cookie': cookie_value})
    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}

    # Verify the cookie was actually written to disk.
    assert fake_container.cookie_repo.exists_and_nonempty()
    cookie_dict = fake_container.cookie_repo.load()
    assert cookie_dict.get('BAHAMUT_SESSID') == 'abc123'


def test_put_cookie_downloader_forbidden(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
) -> None:
    """Downloader PUT /config/cookie → 403."""
    from app.api.config_api import get_cookie_repo

    downloader_client = _make_downloader_client(client)
    downloader_client.app.dependency_overrides[get_cookie_repo] = lambda: fake_container.cookie_repo  # type: ignore[attr-defined]

    r = downloader_client.put('/api/config/cookie', json={'cookie': 'BAHAMUT=x'})
    assert r.status_code == 403


def test_put_cookie_requires_non_empty(
    client: fastapi.testclient.TestClient,
) -> None:
    """Empty cookie string → 422 (pydantic min_length=1)."""
    r = client.put('/api/config/cookie', json={'cookie': ''})
    assert r.status_code == 422


def test_cookie_status_returns_configured_true(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
) -> None:
    """GET /config/cookie/status returns configured=true when cookie.txt exists."""
    from app.api.config_api import get_cookie_repo

    client.app.dependency_overrides[get_cookie_repo] = lambda: fake_container.cookie_repo  # type: ignore[attr-defined]

    # Write a cookie first.
    fake_container.cookie_repo.write('BAHAMUT=hello')

    r = client.get('/api/config/cookie/status')
    assert r.status_code == 200
    assert r.json() == {'configured': True}


def test_cookie_status_returns_configured_false(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
) -> None:
    """GET /config/cookie/status returns configured=false when cookie.txt is absent."""
    from app.api.config_api import get_cookie_repo

    client.app.dependency_overrides[get_cookie_repo] = lambda: fake_container.cookie_repo  # type: ignore[attr-defined]

    # Ensure cookie.txt does not exist.
    cookie_path = fake_container.paths.cookie_path
    if cookie_path.exists():
        cookie_path.unlink()

    r = client.get('/api/config/cookie/status')
    assert r.status_code == 200
    assert r.json() == {'configured': False}

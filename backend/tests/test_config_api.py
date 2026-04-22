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


def test_get_config_includes_telegram_subobject(
    client: fastapi.testclient.TestClient,
) -> None:
    """GET /config response must include a 'telegram' sub-object with all expected fields."""
    r = client.get('/api/config')
    assert r.status_code == 200
    body = r.json()

    assert 'telegram' in body
    tg = body['telegram']
    for field in (
        'enabled',
        'bot_token',
        'webhook_secret',
        'public_url',
        'notify_on',
        'admin_broadcast',
        'rate_limit_per_minute',
        'allow_localhost',
    ):
        assert field in tg, f'telegram.{field} missing from GET /config response'


def test_put_config_telegram_round_trips(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
) -> None:
    """PUT /config with telegram payload persists the telegram fields."""
    payload = {
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
        'parse_cd': 3,
        'telegram': {
            'enabled': True,
            'bot_token': 'test_bot_token_12345',
            'webhook_secret': 'deadsecret',
            'public_url': 'https://example.com',
            'notify_on': ['completed'],
            'admin_broadcast': False,
            'rate_limit_per_minute': 60,
            'allow_localhost': True,
        },
    }

    r = client.put('/api/config', json=payload)
    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}

    persisted = fake_container.settings_repo.load()
    assert persisted.telegram.enabled is True
    assert persisted.telegram.bot_token == 'test_bot_token_12345'
    assert persisted.telegram.webhook_secret == 'deadsecret'
    assert persisted.telegram.public_url == 'https://example.com'
    assert persisted.telegram.notify_on == ['completed']
    assert persisted.telegram.admin_broadcast is False
    assert persisted.telegram.rate_limit_per_minute == 60
    assert persisted.telegram.allow_localhost is True


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


# ---------------------------------------------------------------------------
# Non-admin read access (Issue 1 backend requirement)
# ---------------------------------------------------------------------------


def test_get_config_accessible_by_downloader(
    client: fastapi.testclient.TestClient,
) -> None:
    """GET /api/config must succeed for any authenticated user (not admin-gated).

    Non-admin users need telegram.enabled from this endpoint to gate the
    binding UI.  Route uses require_any_user, so downloader role → 200.
    """
    downloader_client = _make_downloader_client(client)
    r = downloader_client.get('/api/config')
    assert r.status_code == 200
    body = r.json()
    # Basic shape check: telegram subobject must be present.
    assert 'telegram' in body
    assert 'enabled' in body['telegram']


def test_put_config_rejects_downloader(
    client: fastapi.testclient.TestClient,
) -> None:
    """PUT /api/config is still admin-only — downloader gets 403."""
    downloader_client = _make_downloader_client(client)
    r = downloader_client.put('/api/config', json={})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Fix 2: notify_on must have at least one item (BUG-4)
# ---------------------------------------------------------------------------


def _base_payload() -> dict:
    """Minimal valid WebSettings payload used as a base for telegram tests."""
    return {
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
        'parse_cd': 3,
    }


def test_put_config_rejects_empty_notify_on(
    client: fastapi.testclient.TestClient,
) -> None:
    """telegram.notify_on=[] → 422 (min_length=1 constraint)."""
    payload = _base_payload()
    payload['telegram'] = {
        'enabled': True,
        'bot_token': 'tok',
        'webhook_secret': 'sec',
        'public_url': '',
        'notify_on': [],
        'admin_broadcast': True,
        'rate_limit_per_minute': 30,
        'allow_localhost': False,
    }
    r = client.put('/api/config', json=payload)
    assert r.status_code == 422


def test_put_config_accepts_single_notify_on(
    client: fastapi.testclient.TestClient,
) -> None:
    """telegram.notify_on=['completed'] → 200 (list with one item is valid)."""
    payload = _base_payload()
    payload['telegram'] = {
        'enabled': True,
        'bot_token': 'tok',
        'webhook_secret': 'sec',
        'public_url': '',
        'notify_on': ['completed'],
        'admin_broadcast': True,
        'rate_limit_per_minute': 30,
        'allow_localhost': False,
    }
    r = client.put('/api/config', json=payload)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Fix 3: rate_limit_per_minute must be in [1, 300] (BUG-5)
# ---------------------------------------------------------------------------


def test_put_config_rejects_rate_limit_zero(
    client: fastapi.testclient.TestClient,
) -> None:
    """telegram.rate_limit_per_minute=0 → 422 (ge=1 constraint)."""
    payload = _base_payload()
    payload['telegram'] = {
        'enabled': True,
        'bot_token': 'tok',
        'webhook_secret': 'sec',
        'public_url': '',
        'notify_on': ['completed'],
        'admin_broadcast': True,
        'rate_limit_per_minute': 0,
        'allow_localhost': False,
    }
    r = client.put('/api/config', json=payload)
    assert r.status_code == 422


def test_put_config_rejects_rate_limit_over_300(
    client: fastapi.testclient.TestClient,
) -> None:
    """telegram.rate_limit_per_minute=350 → 422 (le=300 constraint)."""
    payload = _base_payload()
    payload['telegram'] = {
        'enabled': True,
        'bot_token': 'tok',
        'webhook_secret': 'sec',
        'public_url': '',
        'notify_on': ['completed'],
        'admin_broadcast': True,
        'rate_limit_per_minute': 350,
        'allow_localhost': False,
    }
    r = client.put('/api/config', json=payload)
    assert r.status_code == 422


def test_put_config_accepts_rate_limit_150(
    client: fastapi.testclient.TestClient,
) -> None:
    """telegram.rate_limit_per_minute=150 → 200 (within valid range)."""
    payload = _base_payload()
    payload['telegram'] = {
        'enabled': True,
        'bot_token': 'tok',
        'webhook_secret': 'sec',
        'public_url': '',
        'notify_on': ['completed'],
        'admin_broadcast': True,
        'rate_limit_per_minute': 150,
        'allow_localhost': False,
    }
    r = client.put('/api/config', json=payload)
    assert r.status_code == 200

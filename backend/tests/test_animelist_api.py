"""Tests for ``/api/anime-list`` endpoints.

These tests use the DB-backed ``anime_list_entry_repo`` path (RBAC mode).
The ``client`` fixture provides a sentinel admin user so all entries are
visible and writable without a real Discord session.
"""

from __future__ import annotations

import datetime
import typing as T

import fastapi.testclient
import pytest

from app.api.deps import current_user_opt
from app.persistence.user_repo import UserRow


def _make_user(role: str, uid: str, username: str = '') -> UserRow:
    return UserRow(
        id=uid,
        username=username or f'user_{uid}',
        avatar_url=None,
        role=role,
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )


def test_get_empty_anime_list(client: fastapi.testclient.TestClient) -> None:
    r = client.get('/api/anime-list')
    assert r.status_code == 200
    assert r.json() == {'entries': []}


def test_put_and_get_entries_round_trip(
    client: fastapi.testclient.TestClient,
) -> None:
    """PUT entries and GET them back — full round-trip through the DB."""
    payload = {
        'entries': [
            {
                'sn': 11111,
                'enabled': True,
                'mode': 'latest',
                'tag': '2024冬季番',
                'season': 1,
                'comment': '第一部',
            },
            {
                'sn': 33333,
                'enabled': True,
                'mode': None,
                'tag': '2024春季番',
                'season': 2,
                'comment': '',
            },
        ]
    }
    r = client.put('/api/anime-list', json=payload)
    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}

    r2 = client.get('/api/anime-list')
    assert r2.status_code == 200
    entries = r2.json()['entries']
    assert len(entries) == 2
    assert entries[0]['sn'] == 11111
    assert entries[0]['mode'] == 'latest'
    assert entries[0]['tag'] == '2024冬季番'
    assert entries[0]['comment'] == '第一部'
    assert entries[1]['sn'] == 33333
    assert entries[1]['mode'] is None
    assert entries[1]['tag'] == '2024春季番'
    assert entries[1]['season'] == 2


def test_put_anime_list_disabled_entry(
    client: fastapi.testclient.TestClient,
) -> None:
    """Entries with enabled=False are stored and returned correctly."""
    payload = {
        'entries': [
            {
                'sn': 22222,
                'enabled': False,
                'mode': 'all',
                'tag': '2024冬季番',
                'season': 1,
                'comment': '',
            },
        ]
    }
    r = client.put('/api/anime-list', json=payload)
    assert r.status_code == 200

    r2 = client.get('/api/anime-list')
    entries = r2.json()['entries']
    assert len(entries) == 1
    e = entries[0]
    assert e['sn'] == 22222
    assert e['enabled'] is False
    assert e['season'] == 1
    assert e['mode'] == 'all'


def test_put_then_get_round_trip_via_api(
    client: fastapi.testclient.TestClient,
) -> None:
    payload = {
        'entries': [
            {
                'sn': 55555,
                'enabled': True,
                'mode': 'largest-sn',
                'tag': '',
                'season': 3,
                'comment': '春番備注',
            },
        ]
    }
    put_r = client.put('/api/anime-list', json=payload)
    assert put_r.status_code == 200

    get_r = client.get('/api/anime-list')
    assert get_r.status_code == 200
    entries = get_r.json()['entries']
    assert len(entries) == 1
    e = entries[0]
    assert e['sn'] == 55555
    assert e['mode'] == 'largest-sn'
    assert e['season'] == 3
    assert e['comment'] == '春番備注'
    assert e['tag'] == ''


# ---------------------------------------------------------------------------
# Multi-user / RBAC tests
# ---------------------------------------------------------------------------


@pytest.fixture
def _admin_user() -> UserRow:
    return _make_user('admin', uid='admin-1', username='admin_alice')


@pytest.fixture
def _downloader_user() -> UserRow:
    return _make_user('downloader', uid='dl-2', username='downloader_bob')


def _seed_entries_for_user(
    app: T.Any,
    fake_container: T.Any,
    user: UserRow,
    entries: list[dict[str, T.Any]],
) -> None:
    """Write entries directly into the repo bypassing the HTTP layer."""
    from app.persistence.anime_list_repo import AnimeListEntryDTO

    dtos = [
        AnimeListEntryDTO(
            sn=e['sn'],
            enabled=e.get('enabled', True),
            mode=e.get('mode'),
            tag=e.get('tag', ''),
            season=e.get('season', 1),
            comment=e.get('comment', ''),
            sort_order=idx,
        )
        for idx, e in enumerate(entries)
    ]
    fake_container.anime_list_entry_repo.replace_all_for_user(user.id, dtos)
    # Also upsert the user into the users table so the service can look up username.
    fake_container.user_repo.upsert(
        id=user.id,
        username=user.username,
        avatar_url=user.avatar_url,
        role=user.role,
    )


def test_admin_get_returns_all_users_entries_with_owner_fields(
    fake_container: T.Any,
    client: fastapi.testclient.TestClient,
    _admin_user: UserRow,
    _downloader_user: UserRow,
) -> None:
    """Admin GET /api/anime-list returns entries from all users with owner_id + owner_username."""
    # Re-use the `client` fixture which already runs as the sentinel admin.
    # We seed entries for two different users directly via the repo.
    _seed_entries_for_user(
        client.app,
        fake_container,
        _admin_user,
        [{'sn': 10001, 'tag': 'admin-tag'}],
    )
    _seed_entries_for_user(
        client.app,
        fake_container,
        _downloader_user,
        [{'sn': 20001, 'tag': 'dl-tag'}],
    )

    # Override the client to use the real admin user (not sentinel) so
    # username lookup resolves from the DB.
    client.app.dependency_overrides[current_user_opt] = lambda: _admin_user

    r = client.get('/api/anime-list')
    assert r.status_code == 200
    entries = r.json()['entries']

    assert len(entries) == 2

    # Every entry must carry owner_id and owner_username.
    for e in entries:
        assert e['owner_id'] is not None, 'owner_id should be set'
        assert e['owner_username'] is not None, 'owner_username should be set'

    sns = {e['sn'] for e in entries}
    assert 10001 in sns
    assert 20001 in sns

    admin_entry = next(e for e in entries if e['sn'] == 10001)
    dl_entry = next(e for e in entries if e['sn'] == 20001)

    assert admin_entry['owner_id'] == _admin_user.id
    assert admin_entry['owner_username'] == _admin_user.username
    assert dl_entry['owner_id'] == _downloader_user.id
    assert dl_entry['owner_username'] == _downloader_user.username


def test_non_admin_get_returns_only_own_entries_with_owner_fields(
    fake_container: T.Any,
    client: fastapi.testclient.TestClient,
    _admin_user: UserRow,
    _downloader_user: UserRow,
) -> None:
    """Non-admin GET /api/anime-list returns only the caller's own entries (fix #15).

    Returning every user's watchlist to any downloader would leak other
    users' private anime lists, so the response is scoped to the caller's
    own ``owner_id`` — the admin's entry seeded below must not appear.
    """
    _seed_entries_for_user(
        client.app,
        fake_container,
        _admin_user,
        [{'sn': 10002}],
    )
    _seed_entries_for_user(
        client.app,
        fake_container,
        _downloader_user,
        [{'sn': 20002}, {'sn': 20003}],
    )

    # Pretend the caller is the downloader.
    client.app.dependency_overrides[current_user_opt] = lambda: _downloader_user

    r = client.get('/api/anime-list')
    assert r.status_code == 200
    entries = r.json()['entries']

    # Only the downloader's own two entries — the admin's sn=10002 must not leak.
    assert len(entries) == 2
    sns = {e['sn'] for e in entries}
    assert sns == {20002, 20003}

    # Every entry must carry owner_id, scoped to the caller.
    for e in entries:
        assert e['owner_id'] == _downloader_user.id
        assert e['owner_username'] == _downloader_user.username


def test_admin_get_includes_own_entries_alongside_other_users(
    fake_container: T.Any,
    client: fastapi.testclient.TestClient,
    _admin_user: UserRow,
    _downloader_user: UserRow,
) -> None:
    """Admin entries appear alongside other users' entries in the response."""
    _seed_entries_for_user(
        client.app,
        fake_container,
        _admin_user,
        [{'sn': 10003}, {'sn': 10004}],
    )
    _seed_entries_for_user(
        client.app,
        fake_container,
        _downloader_user,
        [{'sn': 20004}],
    )

    client.app.dependency_overrides[current_user_opt] = lambda: _admin_user

    r = client.get('/api/anime-list')
    assert r.status_code == 200
    entries = r.json()['entries']

    assert len(entries) == 3
    sns = {e['sn'] for e in entries}
    assert sns == {10003, 10004, 20004}

    # Admin's own entries carry the admin's username.
    admin_entries = [e for e in entries if e['owner_id'] == _admin_user.id]
    assert len(admin_entries) == 2
    for e in admin_entries:
        assert e['owner_username'] == _admin_user.username


# ---------------------------------------------------------------------------
# Feature A: write-permission enforcement
# ---------------------------------------------------------------------------


def test_admin_can_update_any_entry(
    fake_container: T.Any,
    client: fastapi.testclient.TestClient,
    _admin_user: UserRow,
    _downloader_user: UserRow,
) -> None:
    """Admin can save/replace entries owned by other users without error."""
    _seed_entries_for_user(
        client.app,
        fake_container,
        _downloader_user,
        [{'sn': 30001}],
    )

    client.app.dependency_overrides[current_user_opt] = lambda: _admin_user

    # Admin saves an entry with owner_id pointing to the downloader.
    payload = {
        'entries': [
            {
                'sn': 30001,
                'enabled': True,
                'tag': 'admin-edited',
                'season': 1,
                'comment': 'admin changed this',
                'owner_id': _downloader_user.id,
            }
        ]
    }
    r = client.put('/api/anime-list', json=payload)
    assert r.status_code == 200

    # Verify the entry was updated.
    dl_entries = fake_container.anime_list_entry_repo.list_for_user(_downloader_user.id)
    assert len(dl_entries) == 1
    assert dl_entries[0].tag == 'admin-edited'


def test_non_admin_can_update_own_entries(
    fake_container: T.Any,
    client: fastapi.testclient.TestClient,
    _downloader_user: UserRow,
) -> None:
    """Non-admin can save their own entries."""
    _seed_entries_for_user(
        client.app,
        fake_container,
        _downloader_user,
        [{'sn': 40001}],
    )

    client.app.dependency_overrides[current_user_opt] = lambda: _downloader_user

    payload = {
        'entries': [
            {
                'sn': 40001,
                'enabled': False,
                'tag': 'updated-tag',
                'season': 2,
                'comment': '',
                'owner_id': _downloader_user.id,
            }
        ]
    }
    r = client.put('/api/anime-list', json=payload)
    assert r.status_code == 200

    entries = fake_container.anime_list_entry_repo.list_for_user(_downloader_user.id)
    assert len(entries) == 1
    assert entries[0].tag == 'updated-tag'
    assert entries[0].season == 2


def test_non_admin_cannot_update_another_users_entry(
    fake_container: T.Any,
    client: fastapi.testclient.TestClient,
    _admin_user: UserRow,
    _downloader_user: UserRow,
) -> None:
    """Non-admin trying to save an entry with another user's owner_id → 400."""
    _seed_entries_for_user(
        client.app,
        fake_container,
        _admin_user,
        [{'sn': 50001}],
    )

    client.app.dependency_overrides[current_user_opt] = lambda: _downloader_user

    # Attempt to save an entry owned by _admin_user.
    payload = {
        'entries': [
            {
                'sn': 50001,
                'enabled': True,
                'tag': 'hacked',
                'season': 1,
                'comment': '',
                'owner_id': _admin_user.id,  # foreign owner!
            }
        ]
    }
    r = client.put('/api/anime-list', json=payload)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Feature B: duplicate bangumi_name auto-disable
# ---------------------------------------------------------------------------


def test_duplicate_entry_is_auto_disabled(
    fake_container: T.Any,
    client: fastapi.testclient.TestClient,
    _admin_user: UserRow,
    _downloader_user: UserRow,
) -> None:
    """Saving an entry whose anime_name matches an existing one forces enabled=False.

    The frontend always sends back ``anime_name`` as it received it from GET, so
    the service preserves it through the PUT.  We simulate this by including
    ``anime_name`` in the PUT payload.
    """
    from app.persistence.anime_list_repo import AnimeListEntryDTO

    # Seed an existing entry with a known anime_name.
    fake_container.user_repo.upsert(
        id=_admin_user.id,
        username=_admin_user.username,
        avatar_url=None,
        role=_admin_user.role,
    )
    fake_container.anime_list_entry_repo.replace_all_for_user(
        _admin_user.id,
        [AnimeListEntryDTO(sn=60001, anime_name='劍風傳奇')],
    )

    _seed_entries_for_user(
        client.app,
        fake_container,
        _downloader_user,
        [],
    )
    client.app.dependency_overrides[current_user_opt] = lambda: _downloader_user

    # Include anime_name in the payload (mirrors what the frontend sends back
    # after the initial GET populates it via UpdateLoop).
    payload = {
        'entries': [
            {
                'sn': 60002,
                'enabled': True,
                'tag': '',
                'season': 1,
                'comment': '',
                'anime_name': '劍風傳奇',  # same name as the existing entry
            }
        ]
    }
    r = client.put('/api/anime-list', json=payload)
    assert r.status_code == 200

    # The downloader's entry should now be disabled and flagged.
    dl_entries = fake_container.anime_list_entry_repo.list_for_user(_downloader_user.id)
    assert len(dl_entries) == 1
    assert dl_entries[0].enabled is False
    assert dl_entries[0].duplicate_of_entry_id is not None


def test_cannot_enable_duplicate_entry(
    fake_container: T.Any,
    client: fastapi.testclient.TestClient,
    _admin_user: UserRow,
    _downloader_user: UserRow,
) -> None:
    """Attempting to PUT an entry with duplicate_of_entry_id set and enabled=True → 400."""
    from app.persistence.anime_list_repo import AnimeListEntryDTO

    fake_container.user_repo.upsert(
        id=_admin_user.id,
        username=_admin_user.username,
        avatar_url=None,
        role=_admin_user.role,
    )
    fake_container.anime_list_entry_repo.replace_all_for_user(
        _admin_user.id,
        [AnimeListEntryDTO(sn=70001, anime_name='進擊的巨人')],
    )

    # Seed the downloader's entry as a known duplicate.
    fake_container.user_repo.upsert(
        id=_downloader_user.id,
        username=_downloader_user.username,
        avatar_url=None,
        role=_downloader_user.role,
    )
    # Get the admin entry's id.
    admin_entries = fake_container.anime_list_entry_repo.list_for_user(_admin_user.id)
    assert len(admin_entries) == 1
    source_id = admin_entries[0].id

    fake_container.anime_list_entry_repo.replace_all_for_user(
        _downloader_user.id,
        [AnimeListEntryDTO(sn=70002, enabled=False, duplicate_of_entry_id=source_id)],
    )

    client.app.dependency_overrides[current_user_opt] = lambda: _downloader_user

    # Try to enable the duplicate entry.
    payload = {
        'entries': [
            {
                'sn': 70002,
                'enabled': True,  # ← attempt to enable a duplicate
                'tag': '',
                'season': 1,
                'comment': '',
                'duplicate_of_entry_id': source_id,
            }
        ]
    }
    r = client.put('/api/anime-list', json=payload)
    assert r.status_code == 400
    assert 'cannot_enable_duplicate' in r.json().get('detail', '')

"""Tests for ``/api/anime-list`` endpoints.

These tests use the DB-backed ``anime_list_entry_repo`` path (RBAC mode).
The ``client`` fixture provides a sentinel admin user so all entries are
visible and writable without a real Discord session.
"""

from __future__ import annotations

import fastapi.testclient

from .conftest import FakeContainer


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

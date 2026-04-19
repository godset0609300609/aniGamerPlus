"""Tests for ``/api/sn_list`` endpoints."""

from __future__ import annotations

import fastapi.testclient

from .conftest import FakeContainer


def test_get_empty_sn_list(client: fastapi.testclient.TestClient) -> None:
    r = client.get('/api/sn_list')
    assert r.status_code == 200
    assert r.text == ''


def test_put_sn_list_persists_raw_body(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    payload = '@分類\n12345 single <測試番劇>  # comment'
    r = client.put(
        '/api/sn_list',
        content=payload.encode('utf-8'),
        headers={'Content-Type': 'text/plain; charset=utf-8'},
    )
    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}
    assert fake_container.sn_list_repo.read_raw() == payload


def test_round_trip(client: fastapi.testclient.TestClient) -> None:
    body = '11111 latest\n22222 all'
    client.put('/api/sn_list', content=body)
    r = client.get('/api/sn_list')
    assert r.text == body

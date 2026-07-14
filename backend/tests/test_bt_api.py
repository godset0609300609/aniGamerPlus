"""Tests for ``/api/bt`` endpoints (feeds / filters / entries / probe)."""

from __future__ import annotations

import datetime
import typing as T

import fastapi
import fastapi.testclient
import pytest
import sqlalchemy

from app.api.bt_api import (
    get_bt_downloader_service,
    get_bt_feed_entry_repo,
    get_bt_feed_repo,
    get_bt_filter_repo,
    get_bt_manual_dispatch_service,
    get_bt_probe_service,
)
from app.api.deps import current_user_opt
from app.bt_downloader.feed_fetcher import FeedFetchError
from app.models import BtDownloaderSettings, BtProbeResult
from app.persistence.models import BtFeedEntryRow
from app.persistence.user_repo import UserRow
from app.services.bt_downloader_service import BtDownloaderService
from app.services.bt_manual_dispatch_service import (
    BtManualDispatchService,
    EntryNotFound,
    PutioTokenMissing,
)

from .conftest import FakeContainer


def _make_user(role: str, uid: str = 'test-user-1') -> UserRow:
    return UserRow(
        id=uid,
        username=f'test_{role}',
        avatar_url=None,
        role=role,
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )


def _make_downloader_client(base_client: fastapi.testclient.TestClient) -> fastapi.testclient.TestClient:
    app = base_client.app
    app.dependency_overrides[current_user_opt] = lambda: _make_user('downloader')
    return fastapi.testclient.TestClient(app, raise_server_exceptions=True)


def _bind_repos(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    client.app.dependency_overrides[get_bt_feed_repo] = lambda: fake_container.bt_feed_repo  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_bt_filter_repo] = lambda: fake_container.bt_filter_repo  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_bt_feed_entry_repo] = lambda: fake_container.bt_feed_entry_repo  # type: ignore[attr-defined]


class FakeProbeService:
    """Stand-in for BtProbeService — returns a canned result or raises FeedFetchError."""

    def __init__(self, result: BtProbeResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[str] = []

    def probe(self, url: str) -> BtProbeResult:
        self.calls.append(url)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


# ---------------------------------------------------------------------------
# Feeds — CRUD
# ---------------------------------------------------------------------------


def _feed_payload(
    url: str = 'https://share.dmhy.org/topics/rss/sort_id/2/rss.xml',
    name: str = 'dmhy',
) -> dict[str, T.Any]:
    return {
        'name': name,
        'url': url,
        'title_key': 'title',
        'link_key': 'link',
        'guid_key': None,
        'author_key': None,
        'enabled': True,
    }


def test_list_feeds_empty(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    _bind_repos(client, fake_container)
    r = client.get('/api/bt/feeds')
    assert r.status_code == 200
    assert r.json() == []


def test_create_feed_returns_201(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    _bind_repos(client, fake_container)
    r = client.post('/api/bt/feeds', json=_feed_payload())
    assert r.status_code == 201
    body = r.json()
    assert body['name'] == 'dmhy'
    assert body['url'] == 'https://share.dmhy.org/topics/rss/sort_id/2/rss.xml'
    assert body['id'] is not None


def test_create_duplicate_feed_returns_409(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    r1 = client.post('/api/bt/feeds', json=_feed_payload())
    assert r1.status_code == 201

    r2 = client.post('/api/bt/feeds', json=_feed_payload(name='dmhy-again'))
    assert r2.status_code == 409
    assert r2.json()['detail'] == 'URL 已存在'


def test_update_feed_applies_partial_fields(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    created = client.post('/api/bt/feeds', json=_feed_payload()).json()

    r = client.patch(f'/api/bt/feeds/{created["id"]}', json={'enabled': False, 'name': 'renamed'})
    assert r.status_code == 200
    body = r.json()
    assert body['enabled'] is False
    assert body['name'] == 'renamed'
    assert body['url'] == created['url']


def test_update_missing_feed_returns_404(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    _bind_repos(client, fake_container)
    r = client.patch('/api/bt/feeds/999999', json={'enabled': False})
    assert r.status_code == 404


def test_create_feed_ssrf_blocked_url_returns_400(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    r = client.post('/api/bt/feeds', json=_feed_payload(url='http://169.254.169.254/latest/meta-data/'))
    assert r.status_code == 400
    assert 'SSRF guard' in r.json()['detail']


def test_create_feed_container_hostname_url_returns_400(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    r = client.post('/api/bt/feeds', json=_feed_payload(url='http://redis:6379/'))
    assert r.status_code == 400


def test_update_feed_url_to_existing_returns_409(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    client.post('/api/bt/feeds', json=_feed_payload(url='https://a.example/rss.xml', name='a'))
    second = client.post('/api/bt/feeds', json=_feed_payload(url='https://b.example/rss.xml', name='b')).json()

    r = client.patch(f'/api/bt/feeds/{second["id"]}', json={'url': 'https://a.example/rss.xml'})
    assert r.status_code == 409


def test_delete_feed_returns_ok(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    _bind_repos(client, fake_container)
    created = client.post('/api/bt/feeds', json=_feed_payload()).json()

    r = client.delete(f'/api/bt/feeds/{created["id"]}')
    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}

    r2 = client.get('/api/bt/feeds')
    assert r2.json() == []


def test_delete_missing_feed_returns_404(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    _bind_repos(client, fake_container)
    r = client.delete('/api/bt/feeds/999999')
    assert r.status_code == 404


def test_list_feeds_returns_entry_count_per_feed(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    feed_a = client.post('/api/bt/feeds', json=_feed_payload(url='https://a.example/rss.xml', name='a')).json()
    feed_b = client.post('/api/bt/feeds', json=_feed_payload(url='https://b.example/rss.xml', name='b')).json()

    fake_container.bt_feed_entry_repo.insert_if_new(feed_a['id'], 'guid-1', 'A1', 'magnet:1')
    fake_container.bt_feed_entry_repo.insert_if_new(feed_a['id'], 'guid-2', 'A2', 'magnet:2')
    fake_container.bt_feed_entry_repo.insert_if_new(feed_b['id'], 'guid-3', 'B1', 'magnet:3')

    r = client.get('/api/bt/feeds')
    assert r.status_code == 200
    body = {row['id']: row['entry_count'] for row in r.json()}
    assert body[feed_a['id']] == 2
    assert body[feed_b['id']] == 1


def test_list_feeds_entry_count_zero_when_no_entries(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    created = client.post('/api/bt/feeds', json=_feed_payload()).json()

    r = client.get('/api/bt/feeds')
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]['id'] == created['id']
    assert body[0]['entry_count'] == 0


# ---------------------------------------------------------------------------
# Feeds — probe
# ---------------------------------------------------------------------------


def test_probe_feed_returns_available_keys_and_samples(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    fake_probe = FakeProbeService(
        result=BtProbeResult(available_keys=['title', 'link', 'guid'], sample_entries=[{'title': 'Ep 1'}])
    )
    client.app.dependency_overrides[get_bt_probe_service] = lambda: fake_probe

    r = client.post('/api/bt/feeds/probe', json={'url': 'https://example.com/rss.xml'})
    assert r.status_code == 200
    body = r.json()
    assert body['available_keys'] == ['title', 'link', 'guid']
    assert body['sample_entries'] == [{'title': 'Ep 1'}]
    assert fake_probe.calls == ['https://example.com/rss.xml']


def test_probe_feed_fetch_error_returns_502(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    fake_probe = FakeProbeService(error=FeedFetchError('https://dead.example/rss.xml', RuntimeError('timeout')))
    client.app.dependency_overrides[get_bt_probe_service] = lambda: fake_probe

    r = client.post('/api/bt/feeds/probe', json={'url': 'https://dead.example/rss.xml'})
    assert r.status_code == 502
    assert 'https://dead.example/rss.xml' in r.json()['detail']


def test_probe_feed_requires_non_empty_url(client: fastapi.testclient.TestClient) -> None:
    r = client.post('/api/bt/feeds/probe', json={'url': ''})
    assert r.status_code == 422


def test_probe_feed_burst_past_rate_limit_returns_429(
    client: fastapi.testclient.TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/bt/feeds/probe is rate-limited (fix #17) — burst past the env-configured cap."""
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_BT_PROBE', '2/minute')
    fake_probe = FakeProbeService(result=BtProbeResult(available_keys=[], sample_entries=[]))
    client.app.dependency_overrides[get_bt_probe_service] = lambda: fake_probe

    payload = {'url': 'https://example.com/rss.xml'}
    r1 = client.post('/api/bt/feeds/probe', json=payload)
    r2 = client.post('/api/bt/feeds/probe', json=payload)
    r3 = client.post('/api/bt/feeds/probe', json=payload)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_list_filters_empty(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    _bind_repos(client, fake_container)
    r = client.get('/api/bt/filters')
    assert r.status_code == 200
    assert r.json() == []


def test_put_filters_round_trips(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    _bind_repos(client, fake_container)
    payload = {
        'filters': [
            {
                'name': 'LoliHouse 1080p',
                'keywords': ['LoliHouse', '1080', '繁'],
                'enabled': True,
                'sort_order': 0,
            }
        ]
    }
    r = client.put('/api/bt/filters', json=payload)
    assert r.status_code == 200
    assert r.json() == {'status': 'ok'}

    r2 = client.get('/api/bt/filters')
    body = r2.json()
    assert len(body) == 1
    assert body[0]['name'] == 'LoliHouse 1080p'
    assert body[0]['keywords'] == ['LoliHouse', '1080', '繁']


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


def test_list_entries_empty(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    _bind_repos(client, fake_container)
    r = client.get('/api/bt/entries')
    assert r.status_code == 200
    assert r.json() == {'items': [], 'total': 0, 'page': 1, 'size': 50}


def test_list_entries_returns_recent_rows(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    _bind_repos(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-1', 'Some Episode Title', 'magnet:?xt=abc')

    r = client.get('/api/bt/entries?days=7')
    assert r.status_code == 200
    body = r.json()
    assert body['total'] == 1
    assert len(body['items']) == 1
    assert body['items'][0]['title'] == 'Some Episode Title'
    assert body['items'][0]['feed_id'] == feed['id']


@pytest.mark.parametrize('days', [0, 31])
def test_list_entries_rejects_out_of_range_days(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, days: int
) -> None:
    _bind_repos(client, fake_container)
    r = client.get(f'/api/bt/entries?days={days}')
    assert r.status_code == 422


def test_entries_filter_by_filter_id_returns_only_matched_entries(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    entry_a = fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-a', 'Entry A', 'magnet:a')
    entry_b = fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-b', 'Entry B', 'magnet:b')
    assert entry_a is not None
    assert entry_b is not None
    fake_container.bt_feed_entry_repo.mark_dispatched(entry_a.id, filter_id=1, transfer_id=100)
    fake_container.bt_feed_entry_repo.mark_dispatched(entry_b.id, filter_id=2, transfer_id=200)

    r = client.get('/api/bt/entries?filter_id=1')
    assert r.status_code == 200
    body = r.json()
    assert body['total'] == 1
    assert len(body['items']) == 1
    assert body['items'][0]['id'] == entry_a.id


def test_entries_filter_id_and_days_combined_scopes_correctly(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    entry = fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-a', 'Entry A', 'magnet:a')
    assert entry is not None
    fake_container.bt_feed_entry_repo.mark_dispatched(entry.id, filter_id=1, transfer_id=100)

    stale = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)).isoformat()
    with fake_container.database.session() as session:
        session.execute(sqlalchemy.update(BtFeedEntryRow).where(BtFeedEntryRow.id == entry.id).values(fetched_at=stale))

    r = client.get('/api/bt/entries?filter_id=1&days=7')
    assert r.status_code == 200
    assert r.json() == {'items': [], 'total': 0, 'page': 1, 'size': 50}


def test_list_entries_rejects_filter_id_below_one(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    r = client.get('/api/bt/entries?filter_id=0')
    assert r.status_code == 422


def test_entries_filter_by_putio_status_returns_only_matching_status(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    entry_a = fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-a', 'Entry A', 'magnet:a')
    entry_b = fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-b', 'Entry B', 'magnet:b')
    assert entry_a is not None
    assert entry_b is not None
    fake_container.bt_feed_entry_repo.mark_dispatched(entry_a.id, filter_id=1, transfer_id=100)
    fake_container.bt_feed_entry_repo.update_putio_status(entry_a.id, 'COMPLETED')
    fake_container.bt_feed_entry_repo.mark_dispatched(entry_b.id, filter_id=1, transfer_id=200)
    fake_container.bt_feed_entry_repo.update_putio_status(entry_b.id, 'DOWNLOADING')

    r = client.get('/api/bt/entries?putio_status=COMPLETED')
    assert r.status_code == 200
    body = r.json()
    assert body['total'] == 1
    assert body['items'][0]['id'] == entry_a.id


def test_entries_filter_by_putio_status_unassigned_sentinel_returns_null_status_rows(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    entry_a = fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-a', 'Entry A', 'magnet:a')
    entry_b = fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-b', 'Entry B', 'magnet:b')
    assert entry_a is not None
    assert entry_b is not None
    fake_container.bt_feed_entry_repo.mark_dispatched(entry_b.id, filter_id=1, transfer_id=200)

    r = client.get('/api/bt/entries?putio_status=__unassigned__')
    assert r.status_code == 200
    body = r.json()
    assert body['total'] == 1
    assert body['items'][0]['id'] == entry_a.id


def test_list_entries_api_returns_paged_shape(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    for i in range(3):
        fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], f'guid-{i}', f'Title {i}', f'magnet:{i}')

    r = client.get('/api/bt/entries?page=1&size=10')
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {'items', 'total', 'page', 'size'}
    assert body['total'] == 3
    assert body['page'] == 1
    assert body['size'] == 10
    assert len(body['items']) == 3


@pytest.mark.parametrize('params', ['page=0', 'size=5', 'size=201'])
def test_list_entries_api_validates_page_size_bounds(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, params: str
) -> None:
    _bind_repos(client, fake_container)
    r = client.get(f'/api/bt/entries?{params}')
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Entries — search
# ---------------------------------------------------------------------------


def test_entries_search_returns_matching_rows(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-1', 'Attack on Titan - 01', 'magnet:?xt=abc')
    fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-2', 'One Piece - 900', 'magnet:?xt=def')

    r = client.get('/api/bt/entries/search?q=attack')
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]['title'] == 'Attack on Titan - 01'


def test_entries_search_validates_q_length(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)

    assert client.get('/api/bt/entries/search?q=').status_code == 422
    assert client.get(f'/api/bt/entries/search?q={"x" * 201}').status_code == 422
    assert client.get('/api/bt/entries/search?q=ok&limit=0').status_code == 422
    assert client.get('/api/bt/entries/search?q=ok&limit=51').status_code == 422


def test_entries_search_admin_only(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    downloader_client = _make_downloader_client(client)
    _bind_repos(downloader_client, fake_container)

    r = downloader_client.get('/api/bt/entries/search?q=attack')
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Filters — match-count
# ---------------------------------------------------------------------------


def _bind_downloader_service(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, *, hanzi_convert: bool = True
) -> BtDownloaderService:
    service = BtDownloaderService(
        fake_container.bt_feed_repo,
        fake_container.bt_filter_repo,
        fake_container.bt_feed_entry_repo,
        lambda _token: None,  # type: ignore[arg-type,return-value]
        fake_container.putio_token_repo,
        BtDownloaderSettings(hanzi_convert=hanzi_convert),
    )
    client.app.dependency_overrides[get_bt_downloader_service] = lambda: service  # type: ignore[attr-defined]
    return service


def test_match_count_returns_count_for_matching_keywords(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    _bind_downloader_service(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-1', 'LoliHouse 1080p Ep01', 'magnet:1')
    fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-2', 'LoliHouse 720p Ep01', 'magnet:2')

    r = client.post('/api/bt/filters/match-count', json={'keywords': ['LoliHouse', '1080']})
    assert r.status_code == 200
    assert r.json() == {'count': 1, 'over_cap': False}


def test_match_count_empty_keywords_rejected_by_validation(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    _bind_downloader_service(client, fake_container)

    r = client.post('/api/bt/filters/match-count', json={'keywords': []})
    assert r.status_code == 422


def test_match_count_admin_only(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    downloader_client = _make_downloader_client(client)
    _bind_repos(downloader_client, fake_container)
    _bind_downloader_service(downloader_client, fake_container)

    r = downloader_client.post('/api/bt/filters/match-count', json={'keywords': ['x']})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Admin-only enforcement
# ---------------------------------------------------------------------------


def test_downloader_forbidden_on_every_route(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    downloader_client = _make_downloader_client(client)
    _bind_repos(downloader_client, fake_container)
    _bind_downloader_service(downloader_client, fake_container)
    fake_probe = FakeProbeService(result=BtProbeResult(available_keys=[], sample_entries=[]))
    downloader_client.app.dependency_overrides[get_bt_probe_service] = lambda: fake_probe

    assert downloader_client.get('/api/bt/feeds').status_code == 403
    assert downloader_client.post('/api/bt/feeds', json=_feed_payload()).status_code == 403
    assert downloader_client.patch('/api/bt/feeds/1', json={'enabled': False}).status_code == 403
    assert downloader_client.delete('/api/bt/feeds/1').status_code == 403
    assert downloader_client.post('/api/bt/feeds/probe', json={'url': 'https://x.example/rss'}).status_code == 403
    assert downloader_client.get('/api/bt/filters').status_code == 403
    assert downloader_client.put('/api/bt/filters', json={'filters': []}).status_code == 403
    assert downloader_client.post('/api/bt/filters/match-count', json={'keywords': ['x']}).status_code == 403
    assert downloader_client.get('/api/bt/entries').status_code == 403
    assert downloader_client.get('/api/bt/entries/search?q=x').status_code == 403


def test_anonymous_unauthenticated_returns_401(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """With auth enabled and no session, every route is 401 (not 403)."""
    _bind_repos(client, fake_container)
    _bind_downloader_service(client, fake_container)
    client.app.dependency_overrides[current_user_opt] = lambda: None

    r = client.get('/api/bt/feeds')
    assert r.status_code == 401
    assert client.get('/api/bt/entries/search?q=x').status_code == 401
    assert client.post('/api/bt/filters/match-count', json={'keywords': ['x']}).status_code == 401


# ---------------------------------------------------------------------------
# Entries — manual dispatch
# ---------------------------------------------------------------------------


class FakePutioClientForDispatch:
    """Minimal Put.io client stand-in — returns incrementing transfer ids."""

    def __init__(self, *, start_id: int = 100) -> None:
        self.add_transfer_calls: list[str] = []
        self._next_transfer_id = start_id

    def add_transfer(self, url: str) -> dict[str, T.Any]:
        self.add_transfer_calls.append(url)
        transfer_id = self._next_transfer_id
        self._next_transfer_id += 1
        return {'id': transfer_id, 'status': 'IN_QUEUE'}


class FakeManualDispatchService:
    """Stand-in for BtManualDispatchService — returns a canned result or raises."""

    def __init__(self, result: dict[str, T.Any] | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[int, str]] = []

    def dispatch(self, entry_id: int, user_id: str) -> dict[str, T.Any]:
        self.calls.append((entry_id, user_id))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _bind_manual_dispatch_service(
    client: fastapi.testclient.TestClient,
    fake_container: FakeContainer,
    *,
    putio_client: FakePutioClientForDispatch | None = None,
    notify_event_send: T.Callable[..., None] | None = None,
) -> tuple[BtManualDispatchService, FakePutioClientForDispatch]:
    fake_container.putio_token_repo.write('tok')
    fake_client = putio_client if putio_client is not None else FakePutioClientForDispatch()
    service = BtManualDispatchService(
        fake_container.bt_feed_entry_repo,
        lambda _token: fake_client,  # type: ignore[arg-type,return-value]
        fake_container.putio_token_repo,
        bt_feed_repo=fake_container.bt_feed_repo,
        bt_filter_repo=fake_container.bt_filter_repo,
        notify_event_send=notify_event_send,
    )
    client.app.dependency_overrides[get_bt_manual_dispatch_service] = lambda: service  # type: ignore[attr-defined]
    return service, fake_client


def test_dispatch_entry_success_returns_transfer_id(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    entry = fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-1', 'Some Show', 'magnet:1')
    assert entry is not None
    _bind_manual_dispatch_service(client, fake_container)

    r = client.post(f'/api/bt/entries/{entry.id}/dispatch')
    assert r.status_code == 200
    body = r.json()
    assert body == {'transfer_id': 100, 'status': 'IN_QUEUE'}

    r2 = client.get('/api/bt/entries')
    updated = next(item for item in r2.json()['items'] if item['id'] == entry.id)
    assert updated['putio_transfer_id'] == 100
    assert updated['putio_status'] == 'IN_QUEUE'
    assert updated['matched_filter_id'] is None
    assert updated['dispatched_at'] is not None


def test_dispatch_entry_re_dispatches_already_dispatched_row(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """A second dispatch of an already-dispatched entry creates a NEW transfer
    and overwrites putio_transfer_id, while preserving matched_filter_id."""
    _bind_repos(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    entry = fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-1', 'Some Show', 'magnet:1')
    assert entry is not None
    fake_container.bt_feed_entry_repo.mark_dispatched(entry.id, filter_id=1, transfer_id=999)
    _bind_manual_dispatch_service(client, fake_container, putio_client=FakePutioClientForDispatch(start_id=100))

    r = client.post(f'/api/bt/entries/{entry.id}/dispatch')
    assert r.status_code == 200
    body = r.json()
    assert body == {'transfer_id': 100, 'status': 'IN_QUEUE'}

    r2 = client.get('/api/bt/entries')
    updated = next(item for item in r2.json()['items'] if item['id'] == entry.id)
    assert updated['putio_transfer_id'] == 100  # overwritten, was 999
    assert updated['matched_filter_id'] == 1  # preserved, not cleared by manual dispatch


def test_dispatch_entry_returns_404_when_entry_not_found(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    fake_service = FakeManualDispatchService(error=EntryNotFound('entry_id=999999 not found'))
    client.app.dependency_overrides[get_bt_manual_dispatch_service] = lambda: fake_service

    r = client.post('/api/bt/entries/999999/dispatch')
    assert r.status_code == 404


def test_dispatch_entry_returns_400_when_putio_token_missing(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    fake_service = FakeManualDispatchService(error=PutioTokenMissing('Put.io token 未設定'))
    client.app.dependency_overrides[get_bt_manual_dispatch_service] = lambda: fake_service

    r = client.post('/api/bt/entries/1/dispatch')
    assert r.status_code == 400


def test_dispatch_already_remote_returns_200(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """The service's benign "already remote" outcome (a duplicate dispatch
    of a link already on Put.io) must surface as a 2xx with a friendly
    status, not a 502 — see BtManualDispatchService.dispatch's docstring."""
    _bind_repos(client, fake_container)
    fake_service = FakeManualDispatchService(result={'transfer_id': 999, 'status': 'ALREADY_ADDED'})
    client.app.dependency_overrides[get_bt_manual_dispatch_service] = lambda: fake_service

    r = client.post('/api/bt/entries/1/dispatch')
    assert r.status_code == 200
    assert r.json() == {'transfer_id': 999, 'status': 'ALREADY_ADDED'}


def test_dispatch_entry_returns_403_when_not_admin(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    downloader_client = _make_downloader_client(client)
    _bind_repos(downloader_client, fake_container)
    fake_service = FakeManualDispatchService(result={'transfer_id': 1, 'status': 'IN_QUEUE'})
    downloader_client.app.dependency_overrides[get_bt_manual_dispatch_service] = lambda: fake_service

    r = downloader_client.post('/api/bt/entries/1/dispatch')
    assert r.status_code == 403
    assert fake_service.calls == []


def test_dispatch_entry_fires_telegram_notification(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    _bind_repos(client, fake_container)
    feed = client.post('/api/bt/feeds', json=_feed_payload()).json()
    entry = fake_container.bt_feed_entry_repo.insert_if_new(feed['id'], 'guid-1', 'Some Show', 'magnet:1')
    assert entry is not None
    events: list[dict[str, object]] = []

    def notify_event_send(*, kwargs: dict[str, object]) -> None:
        events.append(kwargs)

    _bind_manual_dispatch_service(client, fake_container, notify_event_send=notify_event_send)

    r = client.post(f'/api/bt/entries/{entry.id}/dispatch')
    assert r.status_code == 200

    assert len(events) == 1
    event = events[0]
    assert event['event'] == 'bt_dispatched'
    assert event['title'] == 'Some Show'
    assert event['entry_id'] == entry.id
    assert event['putio_transfer_id'] == 100
    assert event['feed_name'] == 'dmhy'

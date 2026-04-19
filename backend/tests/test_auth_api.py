"""Tests for :mod:`app.api.auth_api` endpoints.

We mount a minimal FastAPI app that includes only the auth router and the
session middleware so these tests are self-contained (no real DB or Discord
API calls).  All database / OAuth collaborators are injected via
``dependency_overrides``.
"""

from __future__ import annotations

import dataclasses
import datetime

import fastapi
import fastapi.testclient
import pytest
import starlette.middleware.sessions

from app.api.auth_api import (
    get_oauth_client,
    get_settings,
    get_user_repo,
    router as auth_router,
)
from app.models import AppSettings, DiscordAuthSettings
from app.persistence.user_repo import UserRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(enabled: bool = True, bootstrap_ids: list[str] | None = None) -> AppSettings:
    auth = DiscordAuthSettings(
        enabled=enabled,
        client_id='CID',
        client_secret='CSECRET',
        redirect_uri='http://localhost/api/auth/callback',
        bootstrap_admin_ids=bootstrap_ids or [],
        session_secret='test-secret-key',
    )
    return AppSettings(auth=auth)


class FakeUserRepo:
    """In-memory UserRepository stand-in."""

    def __init__(self) -> None:
        self._store: dict[str, UserRow] = {}
        self.upsert_calls: list[dict] = []

    def upsert(
        self,
        *,
        id: str,  # noqa: A002
        username: str,
        avatar_url: str | None,
        role: str | None = None,
    ) -> UserRow:
        existing = self._store.get(id)
        if existing is None:
            new_role = role if role is not None else 'downloader'
            row = UserRow(
                id=id,
                username=username,
                avatar_url=avatar_url,
                role=new_role,
                created_at=datetime.datetime.now(datetime.UTC),
                last_login_at=datetime.datetime.now(datetime.UTC),
            )
        else:
            new_role = role if role is not None else existing.role
            row = dataclasses.replace(
                existing,
                username=username,
                avatar_url=avatar_url,
                role=new_role,
                last_login_at=datetime.datetime.now(datetime.UTC),
            )
        self._store[id] = row
        self.upsert_calls.append({'id': id, 'username': username, 'role': role})
        return row

    def get(self, id: str) -> UserRow | None:  # noqa: A002
        return self._store.get(id)


class FakeOAuthClient:
    """Fake DiscordOAuthClient for testing."""

    def __init__(
        self,
        token_response: dict | None = None,
        user_response: dict | None = None,
        exchange_error: Exception | None = None,
    ) -> None:
        self._token = token_response or {'access_token': 'fake_token'}
        self._user = user_response or {
            'id': '111',
            'username': 'alice',
            'avatar': None,
            'avatar_url': None,
        }
        self._exchange_error = exchange_error

    def build_authorize_url(self, state: str) -> str:
        return f'https://discord.com/oauth2/authorize?state={state}&client_id=CID'

    async def exchange_code(self, code: str, state: str) -> dict:  # noqa: ARG002
        if self._exchange_error is not None:
            raise self._exchange_error
        return self._token

    async def fetch_user_info(self, access_token: str) -> dict:  # noqa: ARG002
        return self._user


def _build_client(
    settings: AppSettings,
    user_repo: FakeUserRepo | None = None,
    oauth_client: FakeOAuthClient | None = None,
) -> fastapi.testclient.TestClient:
    """Build a TestClient for the auth router with overridden dependencies."""
    app = fastapi.FastAPI()
    app.add_middleware(
        starlette.middleware.sessions.SessionMiddleware,
        secret_key='test-secret-key',
    )
    app.include_router(auth_router)

    if user_repo is None:
        user_repo = FakeUserRepo()
    if oauth_client is None:
        oauth_client = FakeOAuthClient()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_repo] = lambda: user_repo
    app.dependency_overrides[get_oauth_client] = lambda: oauth_client

    return fastapi.testclient.TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# /login
# ---------------------------------------------------------------------------


def test_login_returns_404_when_auth_disabled() -> None:
    settings = _make_settings(enabled=False)
    client = _build_client(settings)
    resp = client.get('/api/auth/login')
    assert resp.status_code == 404


def test_login_redirects_to_discord() -> None:
    settings = _make_settings(enabled=True)
    client = _build_client(settings)
    resp = client.get('/api/auth/login')
    assert resp.status_code == 302
    assert 'discord.com' in resp.headers['location']


def test_login_stores_state_in_session() -> None:
    settings = _make_settings(enabled=True)
    client = _build_client(settings)
    resp = client.get('/api/auth/login')
    # TestClient stores session cookies automatically; subsequent requests
    # will carry them.  We just check we got redirected with a state param.
    assert resp.status_code == 302
    location = resp.headers['location']
    assert 'state=' in location


# ---------------------------------------------------------------------------
# /callback
# ---------------------------------------------------------------------------


def test_callback_returns_404_when_auth_disabled() -> None:
    settings = _make_settings(enabled=False)
    client = _build_client(settings)
    resp = client.get('/api/auth/callback?code=c&state=s')
    assert resp.status_code == 404


def test_callback_state_mismatch_returns_400() -> None:
    settings = _make_settings(enabled=True)
    client = _build_client(settings)

    # First hit /login to populate the session state.
    resp = client.get('/api/auth/login')
    assert resp.status_code == 302

    # Send a different state in the callback.
    resp2 = client.get('/api/auth/callback?code=mycode&state=WRONG_STATE')
    assert resp2.status_code == 400


def test_callback_happy_path_upserts_user_and_redirects() -> None:
    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(
        token_response={'access_token': 'tok'},
        user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None},
    )
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    # Hit /login to get the state stored in the session.
    resp = client.get('/api/auth/login')
    assert resp.status_code == 302
    location = resp.headers['location']
    state = location.split('state=')[1].split('&')[0]

    # Now hit /callback with the correct state.
    resp2 = client.get(f'/api/auth/callback?code=mycode&state={state}')
    assert resp2.status_code == 302
    assert resp2.headers['location'] == '/'
    # User should have been upserted.
    assert len(user_repo.upsert_calls) == 1
    assert user_repo.upsert_calls[0]['id'] == '111'


def test_callback_bootstrap_admin_ids_promotes_to_admin() -> None:
    settings = _make_settings(enabled=True, bootstrap_ids=['111'])
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(
        user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None},
    )
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    resp = client.get('/api/auth/login')
    state = resp.headers['location'].split('state=')[1].split('&')[0]

    client.get(f'/api/auth/callback?code=mycode&state={state}')

    user = user_repo.get('111')
    assert user is not None
    assert user.role == 'admin'


def test_callback_non_bootstrap_id_stays_downloader() -> None:
    settings = _make_settings(enabled=True, bootstrap_ids=['999'])
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(
        user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None},
    )
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    resp = client.get('/api/auth/login')
    state = resp.headers['location'].split('state=')[1].split('&')[0]
    client.get(f'/api/auth/callback?code=mycode&state={state}')

    user = user_repo.get('111')
    assert user is not None
    assert user.role == 'downloader'


# ---------------------------------------------------------------------------
# /logout
# ---------------------------------------------------------------------------


def test_logout_clears_session() -> None:
    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    # Pre-populate a user.
    user_repo.upsert(id='42', username='bob', avatar_url=None, role='downloader')

    oauth = FakeOAuthClient(
        user_response={'id': '42', 'username': 'bob', 'avatar': None, 'avatar_url': None},
    )
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    # Login → callback to get a session.
    resp = client.get('/api/auth/login')
    state = resp.headers['location'].split('state=')[1].split('&')[0]
    client.get(f'/api/auth/callback?code=code&state={state}')

    # Logout.
    resp = client.post('/api/auth/logout')
    assert resp.status_code == 200
    assert resp.json() == {'ok': True}

    # After logout, /me should return 401.
    resp = client.get('/api/auth/me')
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------


def test_me_returns_401_without_session_when_auth_enabled() -> None:
    settings = _make_settings(enabled=True)
    client = _build_client(settings)
    resp = client.get('/api/auth/me')
    assert resp.status_code == 401


def test_me_returns_sentinel_admin_when_auth_disabled() -> None:
    settings = _make_settings(enabled=False)
    client = _build_client(settings)
    resp = client.get('/api/auth/me')
    assert resp.status_code == 200
    data = resp.json()
    assert data['id'] == '__anonymous_admin__'
    assert data['role'] == 'admin'


def test_me_returns_user_info_with_session() -> None:
    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    user_repo.upsert(id='77', username='carol', avatar_url='http://img', role='admin')
    oauth = FakeOAuthClient(
        user_response={'id': '77', 'username': 'carol', 'avatar': None, 'avatar_url': 'http://img'},
    )
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    # Login → callback to set session.
    resp = client.get('/api/auth/login')
    state = resp.headers['location'].split('state=')[1].split('&')[0]
    client.get(f'/api/auth/callback?code=code&state={state}')

    resp = client.get('/api/auth/me')
    # user_repo has "77" as admin; session now has user_id="77".
    assert resp.status_code == 200
    data = resp.json()
    assert data['id'] == '77'
    assert data['username'] == 'carol'
    assert data['role'] == 'admin'

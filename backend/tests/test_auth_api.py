"""Tests for :mod:`app.api.auth_api` endpoints.

We mount a minimal FastAPI app that includes only the auth router and the
session middleware so these tests are self-contained (no real DB or Discord
API calls).  All database / OAuth collaborators are injected via
``dependency_overrides``.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import json
import urllib.parse

import fastapi
import fastapi.testclient
import pytest
import starlette.middleware.sessions

from app.api.auth_api import (
    get_oauth_client,
    get_settings,
    get_settings_repo,
    get_user_repo,
)
from app.api.auth_api import (
    router as auth_router,
)
from app.models import AppSettings, DiscordAuthSettings, TelegramSettings
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

    def find_by_telegram_chat_id(self, chat_id: int) -> UserRow | None:
        for row in self._store.values():
            if row.telegram_chat_id == chat_id:
                return row
        return None


class FakeSettingsRepo:
    """In-memory SettingsRepository stand-in that returns a fixed AppSettings."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def load(self) -> AppSettings:
        return self._settings


class FakeOAuthClient:
    """Fake DiscordOAuthClient for testing."""

    def __init__(
        self,
        token_response: dict | None = None,
        user_response: dict | None = None,
        exchange_error: Exception | None = None,
        guilds_response: list[dict] | None = None,
        guilds_error: Exception | None = None,
    ) -> None:
        self._token = token_response or {'access_token': 'fake_token'}
        self._user = user_response or {
            'id': '111',
            'username': 'alice',
            'avatar': None,
            'avatar_url': None,
        }
        self._exchange_error = exchange_error
        self._guilds = guilds_response or []
        self._guilds_error = guilds_error
        self.fetch_user_guilds_calls: list[str] = []

    def build_authorize_url(self, state: str) -> str:
        return f'https://discord.com/oauth2/authorize?state={state}&client_id=CID'

    async def exchange_code(self, code: str, state: str) -> dict:  # noqa: ARG002
        if self._exchange_error is not None:
            raise self._exchange_error
        return self._token

    async def fetch_user_info(self, access_token: str) -> dict:  # noqa: ARG002
        return self._user

    async def fetch_user_guilds(self, access_token: str) -> list[dict]:
        self.fetch_user_guilds_calls.append(access_token)
        if self._guilds_error is not None:
            raise self._guilds_error
        return self._guilds


def _build_client(
    settings: AppSettings,
    user_repo: FakeUserRepo | None = None,
    oauth_client: FakeOAuthClient | None = None,
    settings_repo: FakeSettingsRepo | None = None,
) -> fastapi.testclient.TestClient:
    """Build a TestClient for the auth router with overridden dependencies."""
    from app import rate_limit

    app = fastapi.FastAPI()
    app.add_middleware(
        starlette.middleware.sessions.SessionMiddleware,
        secret_key='test-secret-key',
    )
    rate_limit.install(app)
    app.include_router(auth_router)

    if user_repo is None:
        user_repo = FakeUserRepo()
    if oauth_client is None:
        oauth_client = FakeOAuthClient()
    if settings_repo is None:
        settings_repo = FakeSettingsRepo(settings)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_repo] = lambda: user_repo
    app.dependency_overrides[get_oauth_client] = lambda: oauth_client
    app.dependency_overrides[get_settings_repo] = lambda: settings_repo

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
# /callback — Discord allowlist gate (fix #16)
# ---------------------------------------------------------------------------


def _login_then_callback(
    client: fastapi.testclient.TestClient,
) -> fastapi.Response:
    resp = client.get('/api/auth/login')
    state = resp.headers['location'].split('state=')[1].split('&')[0]
    return client.get(f'/api/auth/callback?code=mycode&state={state}')


def test_callback_not_configured_warns_and_allows(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Neither allowlist env var set — permissive default, but a warning is logged."""
    import logging

    monkeypatch.delenv('ANIGAMERPLUS_DISCORD_ALLOWED_GUILDS', raising=False)
    monkeypatch.delenv('ANIGAMERPLUS_DISCORD_ALLOWED_USER_IDS', raising=False)

    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None})
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    with caplog.at_level(logging.WARNING):
        resp = _login_then_callback(client)

    assert resp.status_code == 302
    assert len(user_repo.upsert_calls) == 1
    assert any('allowlist not configured' in record.message for record in caplog.records)
    # Not configured — fetch_user_guilds must never be called.
    assert oauth.fetch_user_guilds_calls == []


def test_callback_user_id_allowlist_allows_listed_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_DISCORD_ALLOWED_USER_IDS', '111,222')
    monkeypatch.delenv('ANIGAMERPLUS_DISCORD_ALLOWED_GUILDS', raising=False)

    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None})
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    resp = _login_then_callback(client)

    assert resp.status_code == 302
    assert len(user_repo.upsert_calls) == 1
    # User-id allowlist short-circuits — guild fetch never happens.
    assert oauth.fetch_user_guilds_calls == []


def test_callback_user_id_allowlist_rejects_unlisted_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_DISCORD_ALLOWED_USER_IDS', '999')
    monkeypatch.delenv('ANIGAMERPLUS_DISCORD_ALLOWED_GUILDS', raising=False)

    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None})
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    resp = _login_then_callback(client)

    assert resp.status_code == 403
    # Rejected before the user row was ever touched.
    assert user_repo.upsert_calls == []


def test_callback_guild_allowlist_allows_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_DISCORD_ALLOWED_GUILDS', '555,666')
    monkeypatch.delenv('ANIGAMERPLUS_DISCORD_ALLOWED_USER_IDS', raising=False)

    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(
        user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None},
        guilds_response=[{'id': '666', 'name': 'My Server'}, {'id': '777', 'name': 'Other'}],
    )
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    resp = _login_then_callback(client)

    assert resp.status_code == 302
    assert len(user_repo.upsert_calls) == 1
    assert oauth.fetch_user_guilds_calls == ['fake_token']


def test_callback_guild_allowlist_rejects_non_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_DISCORD_ALLOWED_GUILDS', '555')
    monkeypatch.delenv('ANIGAMERPLUS_DISCORD_ALLOWED_USER_IDS', raising=False)

    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(
        user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None},
        guilds_response=[{'id': '777', 'name': 'Unrelated Server'}],
    )
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    resp = _login_then_callback(client)

    assert resp.status_code == 403
    assert user_repo.upsert_calls == []


def test_callback_both_allowlists_configured_user_id_fallback_still_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user not in any allowed guild but directly allowlisted by ID is still let in."""
    monkeypatch.setenv('ANIGAMERPLUS_DISCORD_ALLOWED_GUILDS', '555')
    monkeypatch.setenv('ANIGAMERPLUS_DISCORD_ALLOWED_USER_IDS', '111')

    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(
        user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None},
        guilds_response=[{'id': '777', 'name': 'Unrelated Server'}],
    )
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    resp = _login_then_callback(client)

    assert resp.status_code == 302
    assert len(user_repo.upsert_calls) == 1
    # Allowed via the user-id fallback without ever needing the guild fetch.
    assert oauth.fetch_user_guilds_calls == []


def test_callback_guild_fetch_error_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    import httpx

    monkeypatch.setenv('ANIGAMERPLUS_DISCORD_ALLOWED_GUILDS', '555')
    monkeypatch.delenv('ANIGAMERPLUS_DISCORD_ALLOWED_USER_IDS', raising=False)

    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(
        user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None},
        guilds_error=httpx.HTTPStatusError('bad', request=MagicMock(), response=MagicMock(status_code=503)),
    )
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    resp = _login_then_callback(client)

    assert resp.status_code == 502
    assert user_repo.upsert_calls == []


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


# ---------------------------------------------------------------------------
# /telegram-webapp
# ---------------------------------------------------------------------------

_TG_BOT_TOKEN = '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'


def _make_tg_initdata(*, bot_token: str, auth_date: int, user_id: int = 999) -> str:
    """Build a valid Telegram Mini App initData string signed with *bot_token*."""
    user_json = json.dumps({'id': user_id, 'first_name': 'Test'}, separators=(',', ':'))
    fields = {
        'auth_date': str(auth_date),
        'query_id': 'AAH...',
        'user': user_json,
    }
    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret_key = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    fields['hash'] = h
    return urllib.parse.urlencode(fields)


def _make_tg_settings(*, bot_token: str = _TG_BOT_TOKEN) -> AppSettings:
    tg = TelegramSettings(bot_token=bot_token, enabled=True)
    return AppSettings(telegram=tg)


def test_telegram_webapp_login_succeeds_for_bound_user() -> None:
    now = int(datetime.datetime.now(datetime.UTC).timestamp())
    settings = _make_tg_settings()
    user_repo = FakeUserRepo()
    # Insert a user already bound to chat_id 999.
    bound_row = dataclasses.replace(
        user_repo.upsert(id='discord-1', username='alice', avatar_url=None, role='downloader'),
        telegram_chat_id=999,
    )
    user_repo._store['discord-1'] = bound_row

    init_data = _make_tg_initdata(bot_token=_TG_BOT_TOKEN, auth_date=now, user_id=999)
    client = _build_client(settings, user_repo=user_repo)
    resp = client.post('/api/auth/telegram-webapp', json={'initData': init_data})

    assert resp.status_code == 200
    body = resp.json()
    assert body['user_id'] == 'discord-1'
    assert body['username'] == 'alice'
    assert body['role'] == 'downloader'


def test_telegram_webapp_login_401_when_not_bound() -> None:
    now = int(datetime.datetime.now(datetime.UTC).timestamp())
    settings = _make_tg_settings()
    user_repo = FakeUserRepo()
    # No user bound to chat_id 999.

    init_data = _make_tg_initdata(bot_token=_TG_BOT_TOKEN, auth_date=now, user_id=999)
    client = _build_client(settings, user_repo=user_repo)
    resp = client.post('/api/auth/telegram-webapp', json={'initData': init_data})

    assert resp.status_code == 401


def test_telegram_webapp_login_401_when_signature_invalid() -> None:
    now = int(datetime.datetime.now(datetime.UTC).timestamp())
    settings = _make_tg_settings()
    user_repo = FakeUserRepo()

    # Build initData with the correct token but then tamper with a field.
    init_data = _make_tg_initdata(bot_token=_TG_BOT_TOKEN, auth_date=now, user_id=999)
    tampered = init_data + '&extra=injected'
    client = _build_client(settings, user_repo=user_repo)
    resp = client.post('/api/auth/telegram-webapp', json={'initData': tampered})

    assert resp.status_code == 401


def test_telegram_webapp_login_503_when_bot_token_unset() -> None:
    settings = _make_tg_settings(bot_token='')
    user_repo = FakeUserRepo()

    client = _build_client(settings, user_repo=user_repo)
    resp = client.post('/api/auth/telegram-webapp', json={'initData': 'anything'})

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Rate limiting (fix #17)
# ---------------------------------------------------------------------------


def test_login_burst_past_limit_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_AUTH', '2/minute')
    settings = _make_settings(enabled=True)
    client = _build_client(settings)

    r1 = client.get('/api/auth/login')
    r2 = client.get('/api/auth/login')
    r3 = client.get('/api/auth/login')

    assert r1.status_code == 302
    assert r2.status_code == 302
    assert r3.status_code == 429
    assert 'error' in r3.json()


def test_callback_has_its_own_rate_limit_bucket_from_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exhausting /login's bucket must not affect /callback's separate bucket."""
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_AUTH', '1/minute')
    settings = _make_settings(enabled=True)
    user_repo = FakeUserRepo()
    oauth = FakeOAuthClient(user_response={'id': '111', 'username': 'alice', 'avatar': None, 'avatar_url': None})
    client = _build_client(settings, user_repo=user_repo, oauth_client=oauth)

    login_resp = client.get('/api/auth/login')
    assert login_resp.status_code == 302
    # /login's 1/minute bucket is now exhausted.
    assert client.get('/api/auth/login').status_code == 429

    # /callback is a distinct URL — its own bucket is still fresh.
    state = login_resp.headers['location'].split('state=')[1].split('&')[0]
    callback_resp = client.get(f'/api/auth/callback?code=c&state={state}')
    assert callback_resp.status_code == 302


def test_auth_rate_limit_env_var_default_allows_five_per_minute() -> None:
    """With the env var unset, the documented default of 5/minute applies."""
    from app.rate_limit import auth_rate_limit

    assert auth_rate_limit() == '5/minute'

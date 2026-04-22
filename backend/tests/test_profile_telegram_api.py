"""Tests for /api/profile/telegram endpoints.

All tests use a standalone FastAPI app (not the full conftest client fixture)
so they are fast and fully independent of the scheduler / downloader wiring.
"""

from __future__ import annotations

import datetime
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import fastapi
import fastapi.testclient
import pytest

from app.api.deps import get_settings, require_any_user
from app.api.profile_telegram_api import _get_telegram_client, _get_user_repo
from app.api.profile_telegram_api import router as profile_router
from app.logging_ import Logger
from app.models import AppSettings, TelegramSettings
from app.persistence.db import Database
from app.persistence.user_repo import UserRepository, UserRow

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_DOWNLOADER = UserRow(
    id='user-1',
    username='alice',
    avatar_url=None,
    role='downloader',
    created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
    last_login_at=None,
)


def _make_db(tmp_path: pathlib.Path) -> Database:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{(tmp_path / "test.db").as_posix()}', logger)
    db.run_baseline_migrations()
    return db


def _make_app(
    *,
    bot_token: str = 'BOT_TOKEN',
    public_url: str = 'https://example.com',
    user: UserRow = _DOWNLOADER,
    telegram_client: object | None = None,
    user_repo: UserRepository | None = None,
) -> tuple[fastapi.FastAPI, UserRepository | None]:
    """Build a minimal app with the profile telegram router and wired overrides."""
    app = fastapi.FastAPI()
    app.include_router(profile_router)

    tg = TelegramSettings(bot_token=bot_token, public_url=public_url)
    settings = AppSettings(telegram=tg)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_any_user] = lambda: user
    if telegram_client is not None:
        app.dependency_overrides[_get_telegram_client] = lambda: telegram_client
    else:
        app.dependency_overrides[_get_telegram_client] = lambda: None

    if user_repo is not None:
        app.dependency_overrides[_get_user_repo] = lambda: user_repo

    return app, user_repo


def _mock_telegram_client(*, bot_username: str = 'mybot') -> MagicMock:
    mock = MagicMock()
    mock.get_me = AsyncMock(return_value={'id': 123, 'is_bot': True, 'username': bot_username})
    mock.send_message = AsyncMock(return_value={})
    return mock


# ---------------------------------------------------------------------------
# POST /api/profile/telegram/start-link
# ---------------------------------------------------------------------------


def test_start_link_happy_path(tmp_path: pathlib.Path) -> None:
    """start-link returns link_url with correct structure and stores a token."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)

        mock_client = _mock_telegram_client()
        app, _ = _make_app(telegram_client=mock_client, user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.post('/api/profile/telegram/start-link')
        assert resp.status_code == 200
        data = resp.json()
        assert 'link_url' in data
        assert 'https://t.me/mybot?start=' in data['link_url']
        assert data['expires_in_seconds'] == 600

        mock_client.get_me.assert_awaited_once()

        # Token should be stored in the DB.
        row = repo.get(_DOWNLOADER.id)
        assert row is not None
        assert row.telegram_link_token is not None
        assert row.telegram_link_token_expires_at is not None
    finally:
        db.dispose()


def test_start_link_overwrites_existing_token(tmp_path: pathlib.Path) -> None:
    """Calling start-link twice overwrites the previous pending token."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)

        mock_client = _mock_telegram_client()
        app, _ = _make_app(telegram_client=mock_client, user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp1 = tc.post('/api/profile/telegram/start-link')
        token1 = resp1.json()['link_url'].split('?start=')[1]

        resp2 = tc.post('/api/profile/telegram/start-link')
        token2 = resp2.json()['link_url'].split('?start=')[1]

        # Tokens must be different.
        assert token1 != token2
        row = repo.get(_DOWNLOADER.id)
        assert row is not None
        assert row.telegram_link_token == token2
    finally:
        db.dispose()


def test_start_link_bot_token_empty_returns_400(tmp_path: pathlib.Path) -> None:
    """start-link with no bot_token configured returns 400 telegram_not_configured."""
    app, _ = _make_app(bot_token='', telegram_client=None)
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post('/api/profile/telegram/start-link')
    assert resp.status_code == 400
    assert resp.json()['detail'] == 'telegram_not_configured'


def test_start_link_public_url_empty_returns_400(tmp_path: pathlib.Path) -> None:
    """start-link with no public_url configured returns 400 telegram_not_configured."""
    mock_client = _mock_telegram_client()
    app, _ = _make_app(public_url='', telegram_client=mock_client)
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post('/api/profile/telegram/start-link')
    assert resp.status_code == 400
    assert resp.json()['detail'] == 'telegram_not_configured'


def test_start_link_no_client_returns_400() -> None:
    """start-link with no TelegramClient (None) returns 400."""
    app, _ = _make_app(bot_token='TOKEN', telegram_client=None)
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post('/api/profile/telegram/start-link')
    assert resp.status_code == 400
    assert resp.json()['detail'] == 'telegram_not_configured'


def test_start_link_resolves_after_token_saved(tmp_path: pathlib.Path) -> None:
    """Token saved between requests is picked up without a process restart.

    Simulates the bug: container was built with bot_token='' (cache returns
    None). Admin saves a token via settings. The very next POST succeeds
    because _get_telegram_client reads the CURRENT settings and resolves
    via the module-level cache rather than the stale container.
    """
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)

        mock_client = _mock_telegram_client()

        # Build app WITHOUT overriding _get_telegram_client so the real
        # resolve_telegram_client path is exercised.
        app = fastapi.FastAPI()
        app.include_router(profile_router)

        # Settings start with an empty token (simulates container built before
        # admin configured the bot).
        tg = TelegramSettings(bot_token='', public_url='https://example.com')
        settings = AppSettings(telegram=tg)

        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[require_any_user] = lambda: _DOWNLOADER
        app.dependency_overrides[_get_user_repo] = lambda: repo

        tc = fastapi.testclient.TestClient(app)

        # First call: token empty → 400 telegram_not_configured.
        resp = tc.post('/api/profile/telegram/start-link')
        assert resp.status_code == 400

        # Admin saves a valid token.  Update settings in-place so the
        # get_settings override returns the updated value.
        settings.telegram = TelegramSettings(bot_token='NEW_TOKEN', public_url='https://example.com')

        # Patch resolve_telegram_client to return our mock without a real HTTP call.
        with patch(
            'app.api.profile_telegram_api.resolve_telegram_client',
            return_value=mock_client,
        ):
            resp2 = tc.post('/api/profile/telegram/start-link')

        assert resp2.status_code == 200
        data = resp2.json()
        assert 'link_url' in data
        assert 'https://t.me/mybot?start=' in data['link_url']
    finally:
        db.dispose()


def test_start_link_get_me_raises_returns_502_with_exception_name(
    tmp_path: pathlib.Path,
) -> None:
    """502 detail contains the exception class name for diagnostics.

    Verifies that ConnectError raised by get_me() is surfaced in the
    response detail so the user can distinguish network vs auth failures.
    """
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)

        mock_client = MagicMock()
        mock_client.get_me = AsyncMock(side_effect=ConnectionError('network failure'))

        app, _ = _make_app(telegram_client=mock_client, user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.post('/api/profile/telegram/start-link')
        assert resp.status_code == 502
        detail = resp.json()['detail']
        assert detail.startswith('telegram_bot_unreachable:')
        assert 'ConnectionError' in detail
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# POST /api/profile/telegram/unlink
# ---------------------------------------------------------------------------


def test_unlink_clears_token_and_chat_id(tmp_path: pathlib.Path) -> None:
    """unlink clears both telegram_chat_id and telegram_link_token."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)
        repo.finalize_telegram_binding(_DOWNLOADER.id, 12345678)
        repo.set_telegram_link_token(
            _DOWNLOADER.id,
            'sometoken',
            datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5),
        )

        app, _ = _make_app(user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.post('/api/profile/telegram/unlink')
        assert resp.status_code == 200
        assert resp.json() == {'ok': True}

        row = repo.get(_DOWNLOADER.id)
        assert row is not None
        assert row.telegram_chat_id is None
        assert row.telegram_link_token is None
        assert row.telegram_link_token_expires_at is None
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# GET /api/profile/telegram/status
# ---------------------------------------------------------------------------


def test_status_bound(tmp_path: pathlib.Path) -> None:
    """status returns bound=True when chat_id is set."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)
        repo.finalize_telegram_binding(_DOWNLOADER.id, 99887766)

        app, _ = _make_app(user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.get('/api/profile/telegram/status')
        assert resp.status_code == 200
        data = resp.json()
        assert data['bound'] is True
        assert data['chat_id'] == 99887766
        assert data['link_pending'] is False
    finally:
        db.dispose()


def test_status_link_pending(tmp_path: pathlib.Path) -> None:
    """status returns link_pending=True when token is set and not expired."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)
        future_expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=9)
        repo.set_telegram_link_token(_DOWNLOADER.id, 'pendingtoken', future_expiry)

        app, _ = _make_app(user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.get('/api/profile/telegram/status')
        assert resp.status_code == 200
        data = resp.json()
        assert data['bound'] is False
        assert data['link_pending'] is True
    finally:
        db.dispose()


def test_status_neither_bound_nor_pending(tmp_path: pathlib.Path) -> None:
    """status returns bound=False, link_pending=False for a fresh user."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)

        app, _ = _make_app(user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.get('/api/profile/telegram/status')
        assert resp.status_code == 200
        data = resp.json()
        assert data['bound'] is False
        assert data['link_pending'] is False
    finally:
        db.dispose()


def test_status_expired_token_not_pending(tmp_path: pathlib.Path) -> None:
    """status with an expired token returns link_pending=False."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)
        past_expiry = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
        repo.set_telegram_link_token(_DOWNLOADER.id, 'expiredtoken', past_expiry)

        app, _ = _make_app(user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.get('/api/profile/telegram/status')
        assert resp.status_code == 200
        data = resp.json()
        assert data['link_pending'] is False
    finally:
        db.dispose()


def test_status_no_token_link_expires_in_seconds_is_none(tmp_path: pathlib.Path) -> None:
    """GET /status returns link_expires_in_seconds=None when no token is pending."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)

        app, _ = _make_app(user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.get('/api/profile/telegram/status')
        assert resp.status_code == 200
        data = resp.json()
        assert data['link_pending'] is False
        assert data['link_expires_in_seconds'] is None
    finally:
        db.dispose()


def test_status_fresh_token_link_expires_in_seconds(tmp_path: pathlib.Path) -> None:
    """GET /status right after token mint returns link_expires_in_seconds close to TTL."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)
        # Mint a token with full TTL (600 s).
        expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=600)
        repo.set_telegram_link_token(_DOWNLOADER.id, 'freshtoken', expires_at)

        app, _ = _make_app(user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.get('/api/profile/telegram/status')
        assert resp.status_code == 200
        data = resp.json()
        assert data['link_pending'] is True
        remaining = data['link_expires_in_seconds']
        assert remaining is not None
        assert 595 <= remaining <= 600
    finally:
        db.dispose()


def test_status_expired_token_link_expires_in_seconds_is_none(tmp_path: pathlib.Path) -> None:
    """GET /status with expired token: link_pending=False, link_expires_in_seconds=None."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)
        past_expiry = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=5)
        repo.set_telegram_link_token(_DOWNLOADER.id, 'expiredtoken2', past_expiry)

        app, _ = _make_app(user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.get('/api/profile/telegram/status')
        assert resp.status_code == 200
        data = resp.json()
        assert data['link_pending'] is False
        assert data['link_expires_in_seconds'] is None
    finally:
        db.dispose()


def test_status_token_expiring_in_one_second(tmp_path: pathlib.Path) -> None:
    """GET /status with token expiring in 1 s: link_pending=True, remaining >= 0."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)
        # Use a near-future expiry — gives at least 0 s by the time the response arrives.
        expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=1)
        repo.set_telegram_link_token(_DOWNLOADER.id, 'almostexpiredtoken', expires_at)

        app, _ = _make_app(user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.get('/api/profile/telegram/status')
        assert resp.status_code == 200
        data = resp.json()
        # The token is technically still valid (expires_at is in the future when stored).
        # Depending on execution speed link_pending may be True or False;
        # just assert that if pending, remaining >= 0, and if not pending, remaining is None.
        if data['link_pending']:
            assert data['link_expires_in_seconds'] is not None
            assert data['link_expires_in_seconds'] >= 0
        else:
            assert data['link_expires_in_seconds'] is None
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# PATCH /api/profile/telegram/notify-enabled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('enabled', [True, False])
def test_notify_enabled_persists(tmp_path: pathlib.Path, enabled: bool) -> None:
    """PATCH notify-enabled updates the flag for the current user."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id=_DOWNLOADER.id, username=_DOWNLOADER.username, avatar_url=None)
        # Set initial state to the opposite of what we're testing.
        repo.set_telegram_notify_enabled(_DOWNLOADER.id, not enabled)

        app, _ = _make_app(user_repo=repo)
        tc = fastapi.testclient.TestClient(app)

        resp = tc.patch(
            '/api/profile/telegram/notify-enabled',
            json={'enabled': enabled},
        )
        assert resp.status_code == 200
        assert resp.json() == {'ok': True}

        row = repo.get(_DOWNLOADER.id)
        assert row is not None
        assert row.telegram_notify_enabled is enabled
    finally:
        db.dispose()

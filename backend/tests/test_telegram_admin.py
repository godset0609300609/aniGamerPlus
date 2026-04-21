"""Tests for Telegram admin endpoints.

Covers each endpoint's happy path with an admin role and the
"bot_token empty" error path. TelegramClient is mocked so no real
Telegram requests are made.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import fastapi
import fastapi.testclient  # noqa: F401  # needed for TestClient type

from app.api.deps import get_settings, require_admin_user
from app.api.telegram_admin import _get_telegram_client
from app.api.telegram_admin import router as admin_router
from app.models import AppSettings, TelegramSettings
from app.persistence.user_repo import UserRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADMIN_USER = UserRow(
    id='admin-1',
    username='admin',
    avatar_url=None,
    role='admin',
    created_at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC),
    last_login_at=None,
)


def _make_app(
    *,
    bot_token: str = 'BOT_TOKEN',
    public_url: str = 'https://example.com',
    webhook_secret: str = 'my-secret',
    telegram_client: object | None = None,
) -> fastapi.FastAPI:
    app = fastapi.FastAPI()
    app.include_router(admin_router)

    tg = TelegramSettings(
        bot_token=bot_token,
        public_url=public_url,
        webhook_secret=webhook_secret,
    )
    settings = AppSettings(telegram=tg)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin_user] = lambda: _ADMIN_USER
    if telegram_client is not None:
        app.dependency_overrides[_get_telegram_client] = lambda: telegram_client
    else:
        # No client → bot_token effectively empty for _require_client
        app.dependency_overrides[_get_telegram_client] = lambda: None

    return app


def _client_with_mock() -> tuple[fastapi.testclient.TestClient, MagicMock]:
    """Return (TestClient, mock TelegramClient) with all methods pre-wired."""
    mock = MagicMock()
    mock.set_webhook = AsyncMock(return_value=None)
    mock.delete_webhook = AsyncMock(return_value=None)
    mock.get_webhook_info = AsyncMock(
        return_value={'url': 'https://example.com/api/webhooks/telegram/my-secret', 'pending_update_count': 0}
    )
    mock.get_me = AsyncMock(return_value={'id': 123, 'is_bot': True, 'username': 'mybot'})
    app = _make_app(telegram_client=mock)
    return fastapi.testclient.TestClient(app), mock


# ---------------------------------------------------------------------------
# register webhook — happy path
# ---------------------------------------------------------------------------


def test_register_webhook_posts_to_telegram() -> None:
    tc, mock = _client_with_mock()
    resp = tc.post('/api/admin/telegram/webhook/register')
    assert resp.status_code == 200
    data = resp.json()
    assert data['ok'] is True
    assert 'api/webhooks/telegram/my-secret' in data['url']
    mock.set_webhook.assert_awaited_once()
    call_args = mock.set_webhook.call_args
    assert call_args.kwargs['secret_token'] == 'my-secret'
    assert 'message' in call_args.kwargs['allowed_updates']


def test_register_webhook_bot_token_empty_returns_400() -> None:
    app = _make_app(bot_token='', telegram_client=None)
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post('/api/admin/telegram/webhook/register')
    assert resp.status_code == 400
    assert 'bot_token' in resp.json()['detail']


def test_register_webhook_missing_public_url_returns_400() -> None:
    mock = MagicMock()
    mock.set_webhook = AsyncMock(return_value=None)
    app = _make_app(public_url='', telegram_client=mock)
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post('/api/admin/telegram/webhook/register')
    assert resp.status_code == 400
    assert 'public_url' in resp.json()['detail']


def test_register_webhook_missing_webhook_secret_returns_400() -> None:
    mock = MagicMock()
    mock.set_webhook = AsyncMock(return_value=None)
    app = _make_app(webhook_secret='', telegram_client=mock)
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post('/api/admin/telegram/webhook/register')
    assert resp.status_code == 400
    assert 'webhook_secret' in resp.json()['detail']


# ---------------------------------------------------------------------------
# delete webhook
# ---------------------------------------------------------------------------


def test_delete_webhook_happy_path() -> None:
    tc, mock = _client_with_mock()
    resp = tc.post('/api/admin/telegram/webhook/delete')
    assert resp.status_code == 200
    assert resp.json()['ok'] is True
    mock.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)


def test_delete_webhook_bot_token_empty_returns_400() -> None:
    app = _make_app(bot_token='', telegram_client=None)
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post('/api/admin/telegram/webhook/delete')
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# webhook info
# ---------------------------------------------------------------------------


def test_webhook_info_returns_raw_result() -> None:
    tc, mock = _client_with_mock()
    resp = tc.get('/api/admin/telegram/webhook/info')
    assert resp.status_code == 200
    data = resp.json()
    assert 'pending_update_count' in data
    mock.get_webhook_info.assert_awaited_once()


def test_webhook_info_bot_token_empty_returns_400() -> None:
    app = _make_app(bot_token='', telegram_client=None)
    tc = fastapi.testclient.TestClient(app)
    resp = tc.get('/api/admin/telegram/webhook/info')
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# bot/me
# ---------------------------------------------------------------------------


def test_bot_me_returns_bot_info() -> None:
    tc, mock = _client_with_mock()
    resp = tc.get('/api/admin/telegram/bot/me')
    assert resp.status_code == 200
    data = resp.json()
    assert data['is_bot'] is True
    assert data['username'] == 'mybot'
    mock.get_me.assert_awaited_once()


def test_bot_me_bot_token_empty_returns_400() -> None:
    app = _make_app(bot_token='', telegram_client=None)
    tc = fastapi.testclient.TestClient(app)
    resp = tc.get('/api/admin/telegram/bot/me')
    assert resp.status_code == 400

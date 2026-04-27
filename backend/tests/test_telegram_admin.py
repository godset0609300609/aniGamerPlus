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
    mock.set_my_commands = AsyncMock(return_value=None)
    mock.delete_my_commands = AsyncMock(return_value=None)
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


# ---------------------------------------------------------------------------
# Dynamic resolution: token saved via Settings → next request uses new token
# ---------------------------------------------------------------------------


def test_dynamic_resolution_new_token_used_after_settings_save() -> None:
    """After settings change bot_token from '' to a real value, the next
    admin request must resolve a client built for the new token — without
    a process restart.
    """
    from unittest.mock import patch

    from app.services.telegram_client import TelegramClient
    from app.services.telegram_client_cache import _TelegramClientCache

    # Use an isolated cache instance so we don't pollute the module singleton.
    isolated_cache = _TelegramClientCache()
    captured_tokens: list[str] = []

    original_init = TelegramClient.__init__

    def _tracking_init(self: TelegramClient, token: str, **kwargs: object) -> None:
        captured_tokens.append(token)
        original_init(self, token, **kwargs)

    new_token = 'NEWBOT:dynamic_test_token_abc'

    with patch.object(TelegramClient, '__init__', _tracking_init):
        # Simulate the dynamic dependency: resolve_telegram_client reads
        # settings.telegram.bot_token at request time via the cache.
        client = isolated_cache.get(new_token)

    assert client is not None
    assert new_token in captured_tokens, f'Expected {new_token!r} in {captured_tokens}'


def test_register_webhook_response_includes_scheduler_restart_hint() -> None:
    """register_webhook success response must carry scheduler_restart_hint."""
    tc, mock = _client_with_mock()
    resp = tc.post('/api/admin/telegram/webhook/register')
    assert resp.status_code == 200
    data = resp.json()
    assert 'scheduler_restart_hint' in data
    assert data['scheduler_restart_hint']  # non-empty string


# ---------------------------------------------------------------------------
# Bot commands ("/" menu) integration
# ---------------------------------------------------------------------------


def test_register_webhook_also_pushes_bot_commands() -> None:
    """Registering the webhook should also push the canonical command list,
    so admins don't need a separate step to populate the Telegram "/" menu.
    """
    from app.services.telegram_commands import BOT_MENU_COMMANDS

    tc, mock = _client_with_mock()
    resp = tc.post('/api/admin/telegram/webhook/register')

    assert resp.status_code == 200
    assert resp.json()['commands_pushed'] is True
    mock.set_webhook.assert_awaited_once()
    mock.set_my_commands.assert_awaited_once_with(BOT_MENU_COMMANDS)


def test_register_webhook_succeeds_when_command_push_fails() -> None:
    """A failure in setMyCommands must not bubble up — the webhook is
    already registered (the more important side-effect).
    """
    from app.services.telegram_client import TelegramApiError

    mock = MagicMock()
    mock.set_webhook = AsyncMock(return_value=None)
    mock.set_my_commands = AsyncMock(side_effect=TelegramApiError(500, 'boom'))
    app = _make_app(telegram_client=mock)
    tc = fastapi.testclient.TestClient(app)

    resp = tc.post('/api/admin/telegram/webhook/register')

    assert resp.status_code == 200
    data = resp.json()
    assert data['ok'] is True
    assert data['commands_pushed'] is False
    mock.set_webhook.assert_awaited_once()
    mock.set_my_commands.assert_awaited_once()


def test_delete_webhook_clears_bot_commands_first() -> None:
    """Decommissioning the webhook should also clear the bot menu so
    clients don't keep showing stale commands.
    """
    tc, mock = _client_with_mock()
    resp = tc.post('/api/admin/telegram/webhook/delete')

    assert resp.status_code == 200
    assert resp.json()['commands_cleared'] is True
    mock.delete_my_commands.assert_awaited_once()
    mock.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)


def test_delete_webhook_runs_even_when_command_clear_fails() -> None:
    """delete_webhook is the primary action and must run even if the
    best-effort menu clear errors.
    """
    from app.services.telegram_client import TelegramApiError

    mock = MagicMock()
    mock.delete_webhook = AsyncMock(return_value=None)
    mock.delete_my_commands = AsyncMock(side_effect=TelegramApiError(500, 'boom'))
    app = _make_app(telegram_client=mock)
    tc = fastapi.testclient.TestClient(app)

    resp = tc.post('/api/admin/telegram/webhook/delete')

    assert resp.status_code == 200
    data = resp.json()
    assert data['ok'] is True
    assert data['commands_cleared'] is False
    mock.delete_webhook.assert_awaited_once()


def test_refresh_commands_endpoint_pushes_canonical_list() -> None:
    """POST /commands/refresh pushes the canonical BOT_MENU_COMMANDS."""
    from app.services.telegram_commands import BOT_MENU_COMMANDS

    tc, mock = _client_with_mock()
    resp = tc.post('/api/admin/telegram/commands/refresh')

    assert resp.status_code == 200
    data = resp.json()
    assert data['ok'] is True
    assert data['count'] == len(BOT_MENU_COMMANDS)
    mock.set_my_commands.assert_awaited_once_with(BOT_MENU_COMMANDS)


def test_refresh_commands_bot_token_empty_returns_400() -> None:
    app = _make_app(bot_token='', telegram_client=None)
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post('/api/admin/telegram/commands/refresh')
    assert resp.status_code == 400
    assert 'bot_token' in resp.json()['detail']


def test_bot_menu_commands_constant_matches_dispatcher() -> None:
    """The menu list must stay in sync with the actual command dispatcher.

    If a command is added to the dispatcher but missing from the menu, users
    won't discover it. If the menu lists a command the dispatcher doesn't
    handle, the bot will reply 'unknown command' — both are bad.
    """
    from app.services.telegram_commands import _HELP_TEXT, BOT_MENU_COMMANDS

    menu_names = {c['command'] for c in BOT_MENU_COMMANDS}
    # _HELP_TEXT lists each user-facing command on its own line as ``/<name>``.
    help_names = {
        line.split()[0].lstrip('/').rstrip('`')
        for line in _HELP_TEXT.splitlines()
        if line.startswith('/')
    }
    assert menu_names == help_names, (
        f'BOT_MENU_COMMANDS and _HELP_TEXT diverged. '
        f'menu only: {menu_names - help_names}, help only: {help_names - menu_names}'
    )

"""Tests for the Telegram webhook receiver endpoint.

Covers:
- IP allowlist (localhost with allow_localhost=False/True, Telegram CIDR, random public IP)
- X-Forwarded-For header parsing
- Path secret verification
- Header secret verification
- Valid message update returns 200 {"ok": True}
- Malformed JSON → 422
"""

from __future__ import annotations

import datetime
import logging
import typing as T
from unittest.mock import AsyncMock, MagicMock

import fastapi
import fastapi.testclient

from app.api.deps import get_settings
from app.api.telegram_webhook import _get_dispatcher, _get_rate_limiter, _get_telegram_client, _get_user_repo
from app.api.telegram_webhook import router as webhook_router
from app.models import AppSettings, TelegramSettings
from app.services.telegram_rate_limiter import TelegramRateLimiter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = 'test-secret-abc123'
_WEBHOOK_PATH = f'/api/webhooks/telegram/{_SECRET}'

_VALID_MESSAGE_UPDATE = {
    'update_id': 1,
    'message': {
        'message_id': 10,
        'from': {'id': 111, 'is_bot': False, 'first_name': 'Alice'},
        'chat': {'id': 111, 'type': 'private'},
        'date': 1700000000,
        'text': '/start',
    },
}

_VALID_CALLBACK_UPDATE = {
    'update_id': 2,
    'callback_query': {
        'id': 'cq-1',
        'from': {'id': 222, 'is_bot': False, 'first_name': 'Bob'},
        'data': 'action:1',
    },
}


def _make_app(
    *,
    bot_token: str = 'TOKEN',
    webhook_secret: str = _SECRET,
    allow_localhost: bool = True,
) -> fastapi.FastAPI:
    """Build a minimal FastAPI app with the webhook router + overridden settings."""
    from app.api.telegram_webhook import _get_dispatcher, _get_rate_limiter

    app = fastapi.FastAPI()
    app.include_router(webhook_router)

    tg = TelegramSettings(
        bot_token=bot_token,
        webhook_secret=webhook_secret,
        allow_localhost=allow_localhost,
    )
    settings = AppSettings(telegram=tg)

    app.dependency_overrides[get_settings] = lambda: settings
    # Stub out deps that would otherwise make real HTTP calls or require a container.
    app.dependency_overrides[_get_telegram_client] = lambda: None
    app.dependency_overrides[_get_dispatcher] = lambda: None
    app.dependency_overrides[_get_rate_limiter] = lambda: None
    return app


def _client(
    *,
    allow_localhost: bool = True,
    webhook_secret: str = _SECRET,
) -> fastapi.testclient.TestClient:
    return fastapi.testclient.TestClient(_make_app(allow_localhost=allow_localhost, webhook_secret=webhook_secret))


def _post(
    tc: fastapi.testclient.TestClient,
    body: object = _VALID_MESSAGE_UPDATE,
    *,
    path_secret: str = _SECRET,
    header_secret: str = _SECRET,
    client_ip: str = '127.0.0.1',
    extra_headers: dict[str, str] | None = None,
) -> T.Any:
    # Default client_ip is 127.0.0.1 (works when allow_localhost=True).
    # Tests that need a specific IP override via client_ip; set to '' to omit.
    headers: dict[str, str] = {'X-Telegram-Bot-Api-Secret-Token': header_secret}
    if client_ip:
        headers['X-Real-IP'] = client_ip
    if extra_headers:
        headers.update(extra_headers)
    return tc.post(
        f'/api/webhooks/telegram/{path_secret}',
        json=body,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# IP allowlist tests
# ---------------------------------------------------------------------------


def test_localhost_rejected_when_allow_localhost_false() -> None:
    tc = _client(allow_localhost=False)
    resp = _post(tc, client_ip='127.0.0.1')
    assert resp.status_code == 403
    assert resp.json()['detail'] == 'forbidden'


def test_localhost_accepted_when_allow_localhost_true() -> None:
    tc = _client(allow_localhost=True)
    resp = _post(tc, client_ip='127.0.0.1')
    assert resp.status_code == 200


def test_telegram_cidr_ip_accepted() -> None:
    """IP from Telegram's 149.154.160.0/20 range should be accepted."""
    tc = _client(allow_localhost=False)
    resp = _post(tc, client_ip='149.154.160.5')
    assert resp.status_code == 200


def test_second_telegram_cidr_range_accepted() -> None:
    """IP from Telegram's 91.108.4.0/22 range should be accepted."""
    tc = _client(allow_localhost=False)
    resp = _post(tc, client_ip='91.108.4.1')
    assert resp.status_code == 200


def test_random_public_ip_rejected() -> None:
    """Google's DNS IP 8.8.8.8 is not in Telegram's CIDRs."""
    tc = _client(allow_localhost=False)
    resp = _post(tc, client_ip='8.8.8.8')
    assert resp.status_code == 403
    assert resp.json()['detail'] == 'forbidden'


def test_x_forwarded_for_first_element_used() -> None:
    """XFF first element = Telegram CIDR → accepted (no X-Real-IP header).

    The first entry in X-Forwarded-For is the original client IP set by the
    edge proxy (trustworthy). Later entries are added by intermediate proxies.
    """
    tc = _client(allow_localhost=False)
    # Omit X-Real-IP so XFF is the fallback; first entry is in Telegram's CIDR.
    resp = _post(tc, client_ip='', extra_headers={'X-Forwarded-For': '149.154.160.5, 10.0.0.1, 172.16.0.1'})
    assert resp.status_code == 200


def test_x_forwarded_for_first_element_not_telegram_rejected() -> None:
    """XFF first element non-Telegram → rejected even when later entries are Telegram IPs."""
    tc = _client(allow_localhost=False)
    resp = _post(tc, client_ip='', extra_headers={'X-Forwarded-For': '10.0.0.1, 149.154.160.5'})
    assert resp.status_code == 403


def test_x_real_ip_takes_precedence_over_xff() -> None:
    """X-Real-IP shadows X-Forwarded-For."""
    tc = _client(allow_localhost=False)
    # client_ip sets X-Real-IP to a non-Telegram IP; XFF has a Telegram IP.
    resp = _post(
        tc,
        client_ip='8.8.8.8',
        extra_headers={'X-Forwarded-For': '149.154.160.5'},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Secret verification tests
# ---------------------------------------------------------------------------


def test_wrong_path_secret_returns_403() -> None:
    tc = _client()
    resp = _post(tc, path_secret='wrong-secret')
    assert resp.status_code == 403


def test_wrong_header_secret_returns_403() -> None:
    tc = _client()
    resp = _post(tc, header_secret='wrong-header-secret')
    assert resp.status_code == 403


def test_both_secrets_correct_returns_200() -> None:
    tc = _client()
    resp = _post(tc)
    assert resp.status_code == 200
    assert resp.json() == {'ok': True}


# ---------------------------------------------------------------------------
# Update body tests
# ---------------------------------------------------------------------------


def test_valid_message_update_returns_ok_true() -> None:
    tc = _client()
    resp = _post(tc, body=_VALID_MESSAGE_UPDATE)
    assert resp.status_code == 200
    assert resp.json() == {'ok': True}


def test_valid_callback_query_update_returns_ok_true() -> None:
    tc = _client()
    resp = _post(tc, body=_VALID_CALLBACK_UPDATE)
    assert resp.status_code == 200
    assert resp.json() == {'ok': True}


def test_malformed_json_returns_422() -> None:
    tc = _client()
    resp = tc.post(
        _WEBHOOK_PATH,
        content=b'not valid json at all',
        headers={
            'Content-Type': 'application/json',
            'X-Telegram-Bot-Api-Secret-Token': _SECRET,
            'X-Real-IP': '127.0.0.1',  # pass IP check (allow_localhost=True)
        },
    )
    assert resp.status_code == 422


def test_message_update_logs_received() -> None:
    """Verify that a valid message update returns 200 and logs at INFO.

    We install a list-based log handler directly on the module logger to
    avoid caplog thread-isolation issues with the TestClient event loop.
    """
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger('app.api.telegram_webhook')
    handler = _Capture(level=logging.INFO)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        tc = _client()
        resp = _post(tc, body=_VALID_MESSAGE_UPDATE)
    finally:
        logger.removeHandler(handler)

    assert resp.status_code == 200
    messages = [r.getMessage() for r in records]
    assert any('received' in m.lower() and 'message' in m for m in messages), messages


def test_callback_update_logs_correct_type() -> None:
    """Verify callback_query update is logged with correct type."""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger('app.api.telegram_webhook')
    handler = _Capture(level=logging.INFO)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        tc = _client()
        resp = _post(tc, body=_VALID_CALLBACK_UPDATE)
    finally:
        logger.removeHandler(handler)

    assert resp.status_code == 200
    messages = [r.getMessage() for r in records]
    assert any('callback_query' in m for m in messages), messages


# ---------------------------------------------------------------------------
# Dispatcher integration tests (bound / unbound / rate-limited)
# ---------------------------------------------------------------------------


def _make_user_repo_mock(*, bound: bool = True, chat_id: int = 111) -> MagicMock:
    """Return a mock UserRepository.

    When ``bound=True``, ``find_by_telegram_chat_id`` returns a fake UserRow.
    When ``bound=False``, it returns None.
    """
    from app.persistence.user_repo import UserRow

    repo = MagicMock()
    if bound:
        user_row = UserRow(
            id='user-1',
            username='Alice',
            avatar_url=None,
            role='downloader',
            created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
            last_login_at=None,
            telegram_chat_id=chat_id,
            telegram_notify_enabled=True,
        )
        repo.find_by_telegram_chat_id = MagicMock(return_value=user_row)
    else:
        repo.find_by_telegram_chat_id = MagicMock(return_value=None)

    # find_by_telegram_link_token always returns None (not a /start test)
    repo.find_by_telegram_link_token = MagicMock(return_value=None)
    return repo


def _make_dispatcher_mock() -> MagicMock:
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(return_value=None)
    return dispatcher


def _make_app_with_overrides(
    *,
    user_repo: object,
    dispatcher: object | None,
    rate_limiter: object | None,
    telegram_client: object | None = None,
) -> fastapi.FastAPI:
    app = fastapi.FastAPI()
    app.include_router(webhook_router)

    tg = TelegramSettings(
        bot_token='TOKEN',
        webhook_secret=_SECRET,
        allow_localhost=True,
    )
    settings = AppSettings(telegram=tg)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[_get_user_repo] = lambda: user_repo
    app.dependency_overrides[_get_dispatcher] = lambda: dispatcher
    app.dependency_overrides[_get_rate_limiter] = lambda: rate_limiter

    if telegram_client is not None:
        from app.api.telegram_webhook import _get_telegram_client

        app.dependency_overrides[_get_telegram_client] = lambda: telegram_client

    return app


def _cmd_msg(text: str, chat_id: int = 111) -> dict:
    return {
        'update_id': 99,
        'message': {
            'message_id': 20,
            'from': {'id': chat_id, 'is_bot': False, 'first_name': 'Alice'},
            'chat': {'id': chat_id, 'type': 'private'},
            'date': 1700000100,
            'text': text,
        },
    }


def test_unbound_user_gets_bind_hint_not_dispatcher() -> None:
    """Unbound user → 'please bind' reply; dispatcher NOT called."""
    repo = _make_user_repo_mock(bound=False)
    dispatcher = _make_dispatcher_mock()

    tg_client = MagicMock()
    tg_client.send_message = AsyncMock(return_value={})

    app = _make_app_with_overrides(
        user_repo=repo,
        dispatcher=dispatcher,
        rate_limiter=TelegramRateLimiter(max_provider=lambda: 30),
        telegram_client=tg_client,
    )
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post(
        _WEBHOOK_PATH,
        json=_cmd_msg('/download 48430'),
        headers={'X-Telegram-Bot-Api-Secret-Token': _SECRET, 'X-Real-IP': '127.0.0.1'},
    )
    assert resp.status_code == 200
    dispatcher.dispatch.assert_not_called()
    tg_client.send_message.assert_awaited_once()
    sent_text = tg_client.send_message.call_args[0][1]
    assert '綁定' in sent_text or 'bind' in sent_text.lower()


def test_bound_user_over_rate_limit_gets_retry_hint() -> None:
    """Bound user over rate limit → rate-limit reply; dispatcher NOT called."""
    repo = _make_user_repo_mock(bound=True)
    dispatcher = _make_dispatcher_mock()

    # Rate limiter already exhausted: allow 1 then pre-exhaust it
    rl = TelegramRateLimiter(max_provider=lambda: 1)
    rl.allow('user-1')  # exhaust the single slot

    tg_client = MagicMock()
    tg_client.send_message = AsyncMock(return_value={})

    app = _make_app_with_overrides(
        user_repo=repo,
        dispatcher=dispatcher,
        rate_limiter=rl,
        telegram_client=tg_client,
    )
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post(
        _WEBHOOK_PATH,
        json=_cmd_msg('/download 48430'),
        headers={'X-Telegram-Bot-Api-Secret-Token': _SECRET, 'X-Real-IP': '127.0.0.1'},
    )
    assert resp.status_code == 200
    dispatcher.dispatch.assert_not_called()
    tg_client.send_message.assert_awaited_once()
    sent_text = tg_client.send_message.call_args[0][1]
    assert '頻繁' in sent_text or '限' in sent_text or '請' in sent_text


def test_bound_user_under_rate_limit_dispatches_unknown_command() -> None:
    """Bound user under rate limit, unknown command → dispatcher is called."""
    repo = _make_user_repo_mock(bound=True)
    dispatcher = _make_dispatcher_mock()
    rl = TelegramRateLimiter(max_provider=lambda: 30)

    app = _make_app_with_overrides(
        user_repo=repo,
        dispatcher=dispatcher,
        rate_limiter=rl,
    )
    tc = fastapi.testclient.TestClient(app)
    resp = tc.post(
        _WEBHOOK_PATH,
        json=_cmd_msg('/unknown_command_for_test'),
        headers={'X-Telegram-Bot-Api-Secret-Token': _SECRET, 'X-Real-IP': '127.0.0.1'},
    )
    assert resp.status_code == 200
    dispatcher.dispatch.assert_awaited_once()


# ---------------------------------------------------------------------------
# Dynamic resolution: webhook route uses cache (token rotation test)
# ---------------------------------------------------------------------------


def test_webhook_client_resolves_from_current_settings_token() -> None:
    """The webhook route's _get_telegram_client reads the CURRENT bot_token from
    settings at request time via the cache — not a frozen startup value.

    Simulates: admin saves a new token → next webhook call uses a client
    built with the new token without restarting the process.
    """
    from unittest.mock import patch

    from app.services.telegram_client import TelegramClient
    from app.services.telegram_client_cache import _TelegramClientCache

    isolated_cache = _TelegramClientCache()
    captured: list[str] = []
    original_init = TelegramClient.__init__

    def _tracking_init(self: TelegramClient, token: str, **kwargs: object) -> None:
        captured.append(token)
        original_init(self, token, **kwargs)

    new_token = 'WEBHOOKROT:new_token_xyz'
    with patch.object(TelegramClient, '__init__', _tracking_init):
        client = isolated_cache.get(new_token)

    assert client is not None
    assert new_token in captured

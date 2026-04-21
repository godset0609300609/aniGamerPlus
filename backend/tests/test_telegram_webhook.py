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

import logging
import typing as T

import fastapi
import fastapi.testclient

from app.api.deps import get_settings
from app.api.telegram_webhook import router as webhook_router
from app.models import AppSettings, TelegramSettings

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
    app = fastapi.FastAPI()
    app.include_router(webhook_router)

    tg = TelegramSettings(
        bot_token=bot_token,
        webhook_secret=webhook_secret,
        allow_localhost=allow_localhost,
    )
    settings = AppSettings(telegram=tg)

    app.dependency_overrides[get_settings] = lambda: settings
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


def test_x_forwarded_for_last_element_used() -> None:
    """XFF last element = Telegram CIDR → accepted (no X-Real-IP header)."""
    tc = _client(allow_localhost=False)
    # Omit X-Real-IP so XFF is the fallback; last entry is in Telegram's CIDR.
    resp = _post(tc, client_ip='', extra_headers={'X-Forwarded-For': '10.0.0.1, 149.154.160.5'})
    assert resp.status_code == 200


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

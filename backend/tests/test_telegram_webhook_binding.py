"""Tests for the Telegram webhook /start binding flow.

A separate file so the existing test_telegram_webhook.py is untouched.
Covers the new _handle_message logic added in PR #3.
"""

from __future__ import annotations

import datetime
import pathlib
from unittest.mock import AsyncMock, MagicMock

import fastapi
import fastapi.testclient

from app.api.deps import get_settings
from app.api.telegram_webhook import _get_dispatcher, _get_rate_limiter, _get_telegram_client, _get_user_repo
from app.api.telegram_webhook import router as webhook_router
from app.logging_ import Logger
from app.models import AppSettings, TelegramSettings
from app.persistence.db import Database
from app.persistence.user_repo import UserRepository

_SECRET = 'test-secret-abc'
_WEBHOOK_PATH = f'/api/webhooks/telegram/{_SECRET}'


def _make_db(tmp_path: pathlib.Path) -> Database:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{(tmp_path / "test.db").as_posix()}', logger)
    db.run_baseline_migrations()
    return db


def _mock_client() -> MagicMock:
    mock = MagicMock()
    mock.send_message = AsyncMock(return_value={})
    return mock


def _make_app(
    *,
    user_repo: UserRepository,
    telegram_client: object | None = None,
) -> fastapi.FastAPI:
    app = fastapi.FastAPI()
    app.include_router(webhook_router)

    tg = TelegramSettings(
        bot_token='BOT_TOKEN',
        webhook_secret=_SECRET,
    )
    settings = AppSettings(telegram=tg)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[_get_user_repo] = lambda: user_repo
    if telegram_client is not None:
        app.dependency_overrides[_get_telegram_client] = lambda: telegram_client
    else:
        app.dependency_overrides[_get_telegram_client] = lambda: None

    # Stub out container-bound deps so tests don't try to call build_container().
    app.dependency_overrides[_get_dispatcher] = lambda: None
    app.dependency_overrides[_get_rate_limiter] = lambda: None

    return app


def _post(
    app: fastapi.FastAPI,
    body: object,
) -> fastapi.testclient.TestClient:
    tc = fastapi.testclient.TestClient(app)
    return tc.post(
        _WEBHOOK_PATH,
        json=body,
        headers={
            'X-Telegram-Bot-Api-Secret-Token': _SECRET,
        },
    )


def _start_msg(token: str, chat_id: int = 999) -> dict:
    return {
        'update_id': 1,
        'message': {
            'message_id': 10,
            'from': {'id': chat_id, 'is_bot': False, 'first_name': 'Alice'},
            'chat': {'id': chat_id, 'type': 'private'},
            'date': 1700000000,
            'text': f'/start {token}',
        },
    }


def _plain_start_msg(chat_id: int = 999) -> dict:
    return {
        'update_id': 2,
        'message': {
            'message_id': 11,
            'from': {'id': chat_id, 'is_bot': False, 'first_name': 'Bob'},
            'chat': {'id': chat_id, 'type': 'private'},
            'date': 1700000001,
            'text': '/start',
        },
    }


def _other_msg(chat_id: int = 999) -> dict:
    return {
        'update_id': 3,
        'message': {
            'message_id': 12,
            'from': {'id': chat_id, 'is_bot': False, 'first_name': 'Carol'},
            'chat': {'id': chat_id, 'type': 'private'},
            'date': 1700000002,
            'text': 'hello there',
        },
    }


# ---------------------------------------------------------------------------
# /start <valid_token> — happy path
# ---------------------------------------------------------------------------


def test_start_valid_token_finalises_binding(tmp_path: pathlib.Path) -> None:
    """Valid /start token writes chat_id and clears the token."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='user-1', username='Alice', avatar_url=None)
        future_expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5)
        repo.set_telegram_link_token('user-1', 'validtoken', future_expiry)

        mock_client = _mock_client()
        app = _make_app(user_repo=repo, telegram_client=mock_client)
        resp = _post(app, _start_msg('validtoken', chat_id=11111))
        assert resp.status_code == 200

        row = repo.get('user-1')
        assert row is not None
        assert row.telegram_chat_id == 11111
        assert row.telegram_link_token is None
        mock_client.send_message.assert_awaited_once()
        call_text = mock_client.send_message.call_args[0][1]
        assert '綁定成功' in call_text
    finally:
        db.dispose()


def test_start_valid_token_sends_success_reply(tmp_path: pathlib.Path) -> None:
    """send_message is called with the success text after a valid /start."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='user-2', username='Bob', avatar_url=None)
        future_expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5)
        repo.set_telegram_link_token('user-2', 'tok2', future_expiry)

        mock_client = _mock_client()
        app = _make_app(user_repo=repo, telegram_client=mock_client)
        _post(app, _start_msg('tok2', chat_id=22222))

        mock_client.send_message.assert_awaited_once()
        chat_id_sent = mock_client.send_message.call_args[0][0]
        assert chat_id_sent == 22222
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# /start <expired_token>
# ---------------------------------------------------------------------------


def test_start_expired_token_sends_error_no_binding(tmp_path: pathlib.Path) -> None:
    """Expired token → error reply, chat_id stays None."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='user-3', username='Carol', avatar_url=None)
        past_expiry = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
        repo.set_telegram_link_token('user-3', 'expiredtok', past_expiry)

        mock_client = _mock_client()
        app = _make_app(user_repo=repo, telegram_client=mock_client)
        resp = _post(app, _start_msg('expiredtok', chat_id=33333))
        assert resp.status_code == 200

        row = repo.get('user-3')
        assert row is not None
        assert row.telegram_chat_id is None  # not bound

        mock_client.send_message.assert_awaited_once()
        call_text = mock_client.send_message.call_args[0][1]
        assert '無效或已過期' in call_text
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# /start <unknown_token>
# ---------------------------------------------------------------------------


def test_start_unknown_token_sends_error(tmp_path: pathlib.Path) -> None:
    """Unknown token → error reply."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        # No token stored for any user.

        mock_client = _mock_client()
        app = _make_app(user_repo=repo, telegram_client=mock_client)
        resp = _post(app, _start_msg('unknowntok', chat_id=44444))
        assert resp.status_code == 200

        mock_client.send_message.assert_awaited_once()
        call_text = mock_client.send_message.call_args[0][1]
        assert '無效或已過期' in call_text
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# /start (no args)
# ---------------------------------------------------------------------------


def test_start_no_args_sends_hint(tmp_path: pathlib.Path) -> None:
    """/start with no token sends a hint message."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)

        mock_client = _mock_client()
        app = _make_app(user_repo=repo, telegram_client=mock_client)
        resp = _post(app, _plain_start_msg(chat_id=55555))
        assert resp.status_code == 200

        mock_client.send_message.assert_awaited_once()
        call_text = mock_client.send_message.call_args[0][1]
        assert '網站' in call_text or '綁定' in call_text
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# Token race: second webhook with same token → error (token cleared)
# ---------------------------------------------------------------------------


def test_token_race_second_webhook_rejected(tmp_path: pathlib.Path) -> None:
    """First /start wins; second with same token gets error (token cleared)."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='user-5', username='Erin', avatar_url=None)
        future_expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5)
        repo.set_telegram_link_token('user-5', 'racetoken', future_expiry)

        mock_client = _mock_client()
        app = _make_app(user_repo=repo, telegram_client=mock_client)

        # First call — success
        _post(app, _start_msg('racetoken', chat_id=66666))
        row = repo.get('user-5')
        assert row is not None
        assert row.telegram_chat_id == 66666
        assert row.telegram_link_token is None

        mock_client.send_message.reset_mock()

        # Second call with same token — should get error reply
        resp2 = _post(app, _start_msg('racetoken', chat_id=77777))
        assert resp2.status_code == 200
        mock_client.send_message.assert_awaited_once()
        call_text = mock_client.send_message.call_args[0][1]
        assert '無效或已過期' in call_text

        # chat_id must still be the winner (66666)
        row2 = repo.get('user-5')
        assert row2 is not None
        assert row2.telegram_chat_id == 66666
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# Other text message
# ---------------------------------------------------------------------------


def test_other_message_sends_help_hint(tmp_path: pathlib.Path) -> None:
    """Any other message text gets the /help hint reply."""
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)

        mock_client = _mock_client()
        app = _make_app(user_repo=repo, telegram_client=mock_client)
        resp = _post(app, _other_msg(chat_id=88888))
        assert resp.status_code == 200

        mock_client.send_message.assert_awaited_once()
        call_text = mock_client.send_message.call_args[0][1]
        assert '/help' in call_text
    finally:
        db.dispose()

"""Tests for ``PhoneLoginService`` — phone + code (+ optional 2FA) login.

Same "genuine hydrogram.Client, stub only the network boundary" approach as
``test_tg_qr_login.py``. Unlike QR login, phone login uses hydrogram's
ordinary high-level API (``send_code`` / ``sign_in`` / ``check_password``)
so there's no raw MTProto TL object construction to verify here — the
integration point being exercised for real is hydrogram's own
``types.SentCode`` / ``types.User`` objects flowing through our state
machine unmocked.
"""

from __future__ import annotations

import pathlib
import unittest.mock

import hydrogram
import hydrogram.enums
import hydrogram.errors
import hydrogram.types
import pytest

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths
from app.persistence.tg_session_repo import TgSessionRepository
from app.tg_downloader.notification_binder import NotificationBinder
from app.tg_downloader.phone_login import PhoneLoginService

API_ID = 123456
API_HASH = 'abcabcabcabcabcabcabcabcabcabcab'


@pytest.fixture
def database(tmp_path: pathlib.Path) -> Database:
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    db.run_baseline_migrations()
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def session_repo(database: Database) -> TgSessionRepository:
    return TgSessionRepository(database)


def _real_user(user_id: int = 777, username: str = 'phoneuser', phone: str = '886912345678') -> hydrogram.types.User:
    # usernames=[] (not the field default of None) matches what a real MTProto
    # response carries for a user with no *additional* numbered usernames —
    # hydrogram.types.User._parse's active_usernames computation iterates
    # raw_user.usernames unconditionally (unlike its `username=` fallback a
    # few lines above, which does guard on truthiness), so leaving this at
    # the raw.types.User field default of None raises TypeError.
    raw_user = hydrogram.raw.types.User(
        id=user_id,
        first_name='Phone',
        username=username,
        phone=phone,
        access_hash=1,
        restriction_reason=[],
        usernames=[],
    )
    return hydrogram.types.User._parse(None, raw_user)


def _stub_client(*, sent_code_hash: str = 'code-hash-abc', export_session_string: str = 'PHONE_SESSION'):
    client = hydrogram.Client('test-phone-client', api_id=API_ID, api_hash=API_HASH, in_memory=True)
    client.connect = unittest.mock.AsyncMock(return_value=True)
    client.disconnect = unittest.mock.AsyncMock()
    client.send_code = unittest.mock.AsyncMock(
        return_value=hydrogram.types.SentCode(type=hydrogram.enums.SentCodeType.APP, phone_code_hash=sent_code_hash)
    )
    client.sign_in = unittest.mock.AsyncMock()
    client.check_password = unittest.mock.AsyncMock()
    client.export_session_string = unittest.mock.AsyncMock(return_value=export_session_string)
    client.send_message = unittest.mock.AsyncMock()
    return client


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_send_code_invokes_real_hydrogram_send_code(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    client = _stub_client()
    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)

    login_token = await service.send_code('user-1', '+886912345678')

    client.connect.assert_awaited_once()
    client.send_code.assert_awaited_once_with('+886912345678')
    assert login_token


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_code_success_persists_session_and_binds_notification(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    client = _stub_client(sent_code_hash='hash-xyz')
    client.sign_in.return_value = _real_user(user_id=777, username='phoneuser')
    binder = NotificationBinder(lambda: 'aniGamerPlusBot')

    service = PhoneLoginService(
        API_ID, API_HASH, session_repo, client_factory=lambda **kw: client, notification_binder=binder
    )
    login_token = await service.send_code('user-1', '+886912345678')

    result = await service.submit_code(login_token, '12345', 'user-1')

    client.sign_in.assert_awaited_once_with('+886912345678', 'hash-xyz', '12345')
    assert result['status'] == 'success'
    assert result['user_id'] == 'user-1'
    assert session_repo.get_decrypted_session_string('user-1') == 'PHONE_SESSION'
    entry = session_repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.telegram_user_id == 777
    client.send_message.assert_awaited_once_with('@aniGamerPlusBot', '/start')
    assert entry.notification_bind_status == 'success'
    assert entry.notification_bind_error is None


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_code_invalid_stays_awaiting_code(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    client = _stub_client()
    client.sign_in.side_effect = hydrogram.errors.PhoneCodeInvalid()

    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token = await service.send_code('user-1', '+886912345678')

    result = await service.submit_code(login_token, '00000', 'user-1')

    assert result['status'] == 'awaiting_code'
    assert result['error']
    assert session_repo.get_by_user_id('user-1') is None


# ---------------------------------------------------------------------------
# TgLoginStatus 'awaiting_code' — wrong/expired phone code and wrong 2FA
# password must be recoverable retries, not the pending-purge/failed path.
# See app.models.TgLoginStatus.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_wrong_phone_code_sets_status_awaiting_code_not_failed(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    """A wrong code must be recoverable: the same login_token stays usable
    for a follow-up correct submission, rather than being silently switched
    to 'failed' (which would 500 once ``TgLoginStatusResponse`` validates a
    status Pydantic doesn't recognize, or strand the user with no way to
    retry)."""
    client = _stub_client()
    client.sign_in.side_effect = [hydrogram.errors.PhoneCodeInvalid(), _real_user(user_id=42, username='retryuser')]

    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token = await service.send_code('user-1', '+886912345678')

    wrong_result = await service.submit_code(login_token, '00000', 'user-1')
    assert wrong_result['status'] == 'awaiting_code'
    assert wrong_result['error'] == '驗證碼錯誤，請重新輸入'

    retry_result = await service.submit_code(login_token, '11111', 'user-1')
    assert retry_result['status'] == 'success'
    # A stale error from the earlier wrong attempt must not leak into the
    # eventual success payload.
    assert 'error' not in retry_result


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_expired_phone_code_sets_status_awaiting_code_with_appropriate_message(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    """``PhoneCodeExpired`` gets its own distinct, friendlier message from
    ``PhoneCodeInvalid`` — both land on 'awaiting_code', but "expired" and
    "wrong" call for different user-facing guidance."""
    client = _stub_client()
    client.sign_in.side_effect = hydrogram.errors.PhoneCodeExpired()

    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token = await service.send_code('user-1', '+886912345678')

    result = await service.submit_code(login_token, '00000', 'user-1')

    assert result['status'] == 'awaiting_code'
    assert result['error'] == '驗證碼已過期，請重新取得驗證碼'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_code_2fa_switches_to_awaiting_password(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    client = _stub_client()
    client.sign_in.side_effect = hydrogram.errors.SessionPasswordNeeded()

    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token = await service.send_code('user-1', '+886912345678')

    result = await service.submit_code(login_token, '12345', 'user-1')

    assert result == {'status': 'awaiting_password'}


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_password_after_2fa_success(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    client = _stub_client()
    client.sign_in.side_effect = hydrogram.errors.SessionPasswordNeeded()
    client.check_password.return_value = _real_user(user_id=888, username='twofactorphone')

    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token = await service.send_code('user-1', '+886912345678')
    await service.submit_code(login_token, '12345', 'user-1')

    result = await service.submit_password(login_token, 'my-2fa-password', 'user-1')

    assert result['status'] == 'success'
    client.check_password.assert_awaited_once_with('my-2fa-password')
    entry = session_repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.telegram_user_id == 888


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_password_wrong_password_stays_awaiting_password(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    client = _stub_client()
    client.sign_in.side_effect = hydrogram.errors.SessionPasswordNeeded()
    client.check_password.side_effect = hydrogram.errors.PasswordHashInvalid()

    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token = await service.send_code('user-1', '+886912345678')
    await service.submit_code(login_token, '12345', 'user-1')

    result = await service.submit_password(login_token, 'wrong', 'user-1')

    assert result['status'] == 'awaiting_password'
    assert result['error']


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_wrong_2fa_password_sets_status_awaiting_password_not_failed(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    """A wrong 2FA password must be recoverable: the same login_token stays
    usable for a follow-up correct submission, rather than being silently
    switched to 'failed'."""
    client = _stub_client()
    client.sign_in.side_effect = hydrogram.errors.SessionPasswordNeeded()
    client.check_password.side_effect = [
        hydrogram.errors.PasswordHashInvalid(),
        _real_user(user_id=99, username='pwuser'),
    ]

    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token = await service.send_code('user-1', '+886912345678')
    await service.submit_code(login_token, '12345', 'user-1')

    wrong_result = await service.submit_password(login_token, 'wrong', 'user-1')
    assert wrong_result['status'] == 'awaiting_password'
    assert wrong_result['error'] == '密碼錯誤，請重新輸入'

    retry_result = await service.submit_password(login_token, 'correct', 'user-1')
    assert retry_result['status'] == 'success'
    assert 'error' not in retry_result


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_code_unknown_login_token_returns_failed(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    service = PhoneLoginService(API_ID, API_HASH, session_repo)

    result = await service.submit_code('nonexistent', '12345', 'user-1')

    assert result['status'] == 'failed'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_code_bool_result_treated_as_signup_needed(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    """``sign_in`` returning a bare ``bool`` means the account needs sign-up (ToS) — out of scope, fails cleanly."""
    client = _stub_client()
    client.sign_in.return_value = False

    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token = await service.send_code('user-1', '+886912345678')

    result = await service.submit_code(login_token, '12345', 'user-1')

    assert result['status'] == 'failed'
    assert session_repo.get_by_user_id('user-1') is None


# ---------------------------------------------------------------------------
# MEDIUM-1 security fix — login_token caller-ownership check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_code_by_wrong_user_returns_not_found(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    """A login_token belonging to user-1 must not be usable by user-2 — the
    response must be indistinguishable from an unknown/expired token, so a
    non-owner can't even confirm the token exists."""
    client = _stub_client()
    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token = await service.send_code('user-1', '+886912345678')

    result = await service.submit_code(login_token, '12345', 'user-2')

    assert result == {'status': 'failed', 'error': 'login_token 不存在或已過期'}
    client.sign_in.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_password_by_wrong_user_returns_not_found(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    client = _stub_client()
    client.sign_in.side_effect = hydrogram.errors.SessionPasswordNeeded()
    service = PhoneLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token = await service.send_code('user-1', '+886912345678')
    await service.submit_code(login_token, '12345', 'user-1')

    result = await service.submit_password(login_token, 'hunter2', 'user-2')

    assert result == {'status': 'failed', 'error': 'login_token 不存在或已過期'}
    client.check_password.assert_not_awaited()

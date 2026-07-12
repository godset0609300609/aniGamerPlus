"""Tests for ``QrLoginService`` — the raw MTProto ``auth.exportLoginToken`` /
``auth.importLoginToken`` QR-login state machine.

These construct a genuine ``hydrogram.Client`` (``in_memory=True``, dummy
credentials) and only stub the boundary methods that would otherwise touch
real MTProto servers (``connect`` / ``invoke`` / ``disconnect`` /
``export_session_string`` / ``check_password``). Everything else — in
particular the real ``hydrogram.raw.functions.auth.ExportLoginToken``
construction inside ``QrLoginService`` and the real
``hydrogram.types.User._parse`` static method that turns a raw MTProto user
into hydrogram's high-level ``User`` — runs unmocked, so these tests actually
exercise hydrogram's own object model rather than asserting shape on our own
stand-ins.
"""

from __future__ import annotations

import contextlib
import pathlib
import unittest.mock

import hydrogram
import hydrogram.errors
import hydrogram.raw as raw
import pytest

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths
from app.persistence.tg_session_repo import TgSessionRepository
from app.tg_downloader.notification_binder import NotificationBinder
from app.tg_downloader.qr_login import QrLoginService

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


def _real_raw_user(user_id: int = 999, username: str = 'realuser', phone: str = '886900000000') -> raw.types.User:
    """A genuine ``hydrogram.raw.types.User`` — enough fields set that
    ``hydrogram.types.User._parse`` (real, unmocked) can build a real
    high-level ``User`` from it.

    ``usernames=[]`` (not the field default of ``None``) matches what a real
    MTProto response carries for a user with no *additional* numbered
    usernames — ``hydrogram.types.User._parse``'s ``active_usernames``
    computation iterates ``raw_user.usernames`` unconditionally (unlike its
    ``username=`` fallback a few lines above, which does guard on
    truthiness), so leaving this at the field default raises ``TypeError``.
    """
    return raw.types.User(
        id=user_id,
        first_name='Real',
        username=username,
        phone=phone,
        access_hash=42,
        restriction_reason=[],
        usernames=[],
    )


def _stub_client(*, invoke_results: list[object], export_session_string: str = 'EXPORTED_SESSION_STRING'):
    """Build a real ``hydrogram.Client`` with only its network-touching methods stubbed.

    ``storage`` stays a genuine ``hydrogram.storage.MemoryStorage`` instance
    (from ``Client.__init__``), but its ``dc_id``/``auth_key``/``test_mode``
    accessors are stubbed too: the real ones need an already-``open()``ed
    sqlite handle, which never happens here because ``connect`` is mocked
    out rather than actually run. ``session`` stands in for the live
    ``hydrogram.session.Session`` a real ``connect()`` would have created —
    exercised by the DC-migration path's ``session.stop()`` /
    ``client.session = <new Session>`` / ``session.start()`` dance.
    """
    client = hydrogram.Client('test-qr-client', api_id=API_ID, api_hash=API_HASH, in_memory=True)
    client.connect = unittest.mock.AsyncMock(return_value=True)
    client.disconnect = unittest.mock.AsyncMock()
    client.invoke = unittest.mock.AsyncMock(side_effect=invoke_results)
    client.export_session_string = unittest.mock.AsyncMock(return_value=export_session_string)
    client.check_password = unittest.mock.AsyncMock()
    client.send_message = unittest.mock.AsyncMock()

    client.storage.dc_id = unittest.mock.AsyncMock(return_value=2)
    client.storage.auth_key = unittest.mock.AsyncMock(return_value=b'\x00' * 256)
    client.storage.test_mode = unittest.mock.AsyncMock(return_value=False)
    client.storage.api_id = unittest.mock.AsyncMock(return_value=API_ID)

    client.session = unittest.mock.MagicMock()
    client.session.stop = unittest.mock.AsyncMock()

    return client


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_start_invokes_real_export_login_token(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'\x01\x02\x03qr-token-bytes')
    client = _stub_client(invoke_results=[login_token_result])

    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)

    login_token, qr_url, qr_png = await service.start('user-1')

    # Assert the ACTUAL object passed to Client.invoke is a genuine hydrogram
    # raw TL function instance with our api_id/api_hash — not a shape check
    # on our own dict.
    called_with = client.invoke.call_args.args[0]
    assert isinstance(called_with, raw.functions.auth.ExportLoginToken)
    assert called_with.api_id == API_ID
    assert called_with.api_hash == API_HASH
    assert called_with.except_ids == []

    client.connect.assert_awaited_once()
    assert login_token  # opaque server-side token, non-empty
    assert qr_url.startswith('tg://login?token=')
    assert qr_png.startswith('data:image/png;base64,')


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_poll_success_persists_encrypted_session_and_binds_notification(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    raw_user = _real_raw_user()
    success_result = raw.types.auth.LoginTokenSuccess(authorization=raw.types.auth.Authorization(user=raw_user))
    client = _stub_client(invoke_results=[login_token_result, success_result])
    binder = NotificationBinder(lambda: 'aniGamerPlusBot')

    service = QrLoginService(
        API_ID, API_HASH, session_repo, client_factory=lambda **kw: client, notification_binder=binder
    )

    login_token, _url, _png = await service.start('user-1')
    result = await service.poll(login_token, 'user-1')

    assert result['status'] == 'success'
    assert result['user_id'] == 'user-1'
    assert result['telegram_handle'] == 'realuser'

    # Session persisted, Fernet-encrypted at rest, decryptable back to the
    # exact string hydrogram's (mocked) export_session_string returned.
    assert session_repo.get_decrypted_session_string('user-1') == 'EXPORTED_SESSION_STRING'
    entry = session_repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.telegram_user_id == 999
    assert entry.phone_tail4 == '0000'

    # Notification-bind /start fired via the user's own session (not the Bot API),
    # and the outcome ('success') was persisted alongside the session row.
    client.send_message.assert_awaited_once_with('@aniGamerPlusBot', '/start')
    assert entry.notification_bind_status == 'success'
    assert entry.notification_bind_error is None
    client.disconnect.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_poll_success_session_persist_failure_returns_sanitized_error(
    anyio_backend: str, session_repo: TgSessionRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-11 (security audit): a failure persisting the session (e.g. Fernet
    misconfiguration) must not leak internal detail via ``result['error']``
    — it gets the same bounded, generic treatment as any other login
    failure, just mapped to the more specific 'session 儲存失敗' bucket."""
    from app.security import crypto

    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    raw_user = _real_raw_user()
    success_result = raw.types.auth.LoginTokenSuccess(authorization=raw.types.auth.Authorization(user=raw_user))
    client = _stub_client(invoke_results=[login_token_result, success_result])

    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')

    # Break the Fernet key so persist_login_success's encrypt_str() call
    # (inside session_repo.upsert) raises FernetKeyMissingError.
    monkeypatch.delenv(crypto.FERNET_KEY_ENV_VAR, raising=False)
    crypto.reset_fernet_cache()
    try:
        result = await service.poll(login_token, 'user-1')
    finally:
        crypto.reset_fernet_cache()

    assert result['status'] == 'failed'
    assert result['error'] == 'session 儲存失敗'
    assert crypto.FERNET_KEY_ENV_VAR not in result['error']
    assert session_repo.get_by_user_id('user-1') is None
    client.disconnect.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_poll_success_persists_notification_bind_failure_reason(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    """A failed notification bind must not un-succeed the session bind, but its
    reason is persisted so the Settings UI can surface *why* it failed."""
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    raw_user = _real_raw_user()
    success_result = raw.types.auth.LoginTokenSuccess(authorization=raw.types.auth.Authorization(user=raw_user))
    client = _stub_client(invoke_results=[login_token_result, success_result])
    # No bot_username configured -> NotificationBindResult.BOT_USERNAME_NOT_CONFIGURED.
    binder = NotificationBinder(lambda: '')

    service = QrLoginService(
        API_ID, API_HASH, session_repo, client_factory=lambda **kw: client, notification_binder=binder
    )

    login_token, _url, _png = await service.start('user-1')
    result = await service.poll(login_token, 'user-1')

    assert result['status'] == 'success'  # session bind still succeeds
    client.send_message.assert_not_awaited()
    entry = session_repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.notification_bind_status == 'bot_username_not_configured'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_poll_repeated_before_scan_stays_pending(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    # Every poll still returns the same (unscanned) LoginToken.
    client = _stub_client(invoke_results=[login_token_result, login_token_result, login_token_result])

    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')

    result = await service.poll(login_token, 'user-1')

    assert result == {'status': 'pending'}


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_poll_session_password_needed_switches_to_awaiting_password(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    client = _stub_client(invoke_results=[login_token_result, hydrogram.errors.SessionPasswordNeeded()])

    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')

    result = await service.poll(login_token, 'user-1')

    assert result == {'status': 'awaiting_password'}
    # Polling again while awaiting_password must not re-invoke ExportLoginToken.
    result2 = await service.poll(login_token, 'user-1')
    assert result2 == {'status': 'awaiting_password'}
    assert client.invoke.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_password_success_persists_session(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    client = _stub_client(invoke_results=[login_token_result, hydrogram.errors.SessionPasswordNeeded()])
    real_user = hydrogram.types.User._parse(client, _real_raw_user(user_id=555, username='twofactoruser'))
    client.check_password.return_value = real_user

    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')
    await service.poll(login_token, 'user-1')  # -> awaiting_password

    result = await service.submit_password(login_token, 'correct horse battery staple', 'user-1')

    assert result['status'] == 'success'
    client.check_password.assert_awaited_once_with('correct horse battery staple')
    assert session_repo.get_decrypted_session_string('user-1') == 'EXPORTED_SESSION_STRING'
    entry = session_repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.telegram_user_id == 555


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_password_invalid_stays_awaiting_password(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    client = _stub_client(invoke_results=[login_token_result, hydrogram.errors.SessionPasswordNeeded()])
    client.check_password.side_effect = hydrogram.errors.PasswordHashInvalid()

    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')
    await service.poll(login_token, 'user-1')

    result = await service.submit_password(login_token, 'wrong-password', 'user-1')

    assert result['status'] == 'awaiting_password'
    assert result['error']
    assert session_repo.get_by_user_id('user-1') is None  # never persisted


@contextlib.contextmanager
def _mocked_dc_migration_session(auth_key: bytes = b'\xaa' * 256):
    """Patch ``hydrogram.session.Auth``/``Session`` so DC-migration tests exercise
    ``QrLoginService._complete_migration``'s reconnect dance without ever touching
    the network (a real ``Auth.create()``/``Session.start()`` performs a live
    MTProto handshake). Yields ``(mock_auth_cls, mock_session_cls, new_session)``.
    """
    auth_instance = unittest.mock.MagicMock()
    auth_instance.create = unittest.mock.AsyncMock(return_value=auth_key)
    new_session = unittest.mock.MagicMock()
    new_session.start = unittest.mock.AsyncMock()

    with (
        unittest.mock.patch('hydrogram.session.Auth', return_value=auth_instance) as mock_auth_cls,
        unittest.mock.patch('hydrogram.session.Session', return_value=new_session) as mock_session_cls,
    ):
        yield mock_auth_cls, mock_session_cls, new_session


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_dc_migration_reconnects_and_completes_login(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    migrate_result = raw.types.auth.LoginTokenMigrateTo(dc_id=2, token=b'other-dc-token')
    raw_user = _real_raw_user()
    success_result = raw.types.auth.LoginTokenSuccess(authorization=raw.types.auth.Authorization(user=raw_user))

    client = _stub_client(invoke_results=[login_token_result])
    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')

    # Isolate the poll-triggered invokes from start()'s ExportLoginToken call.
    client.invoke.reset_mock()
    client.invoke.side_effect = [migrate_result, success_result]
    original_session = client.session  # the pre-migration Session, captured before it's replaced

    with _mocked_dc_migration_session() as (mock_auth_cls, mock_session_cls, new_session):
        result = await service.poll(login_token, 'user-1')

    assert result['status'] == 'success'
    assert result['telegram_handle'] == 'realuser'

    # The DC actually switched on the client's existing (never-reopened)
    # storage, and a fresh auth key was created against the target DC (auth
    # keys are per-DC and not reusable across a migration).
    client.storage.dc_id.assert_any_call(2)
    mock_auth_cls.assert_called_once_with(client, 2, False)
    mock_session_cls.assert_called_once_with(client, 2, b'\xaa' * 256, False)
    new_session.start.assert_awaited_once()
    original_session.stop.assert_awaited_once()
    assert client.session is new_session  # the live Session was swapped in place

    # No Client.disconnect()/connect() cycle mid-migration — see module
    # docstring for why that would silently wipe in-memory storage. connect()
    # was only ever awaited once, from start(); disconnect() only fires once,
    # as the normal post-success cleanup (same as a non-migrated success).
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()

    assert client.invoke.await_count == 2
    export_call, import_call = client.invoke.call_args_list
    assert isinstance(export_call.args[0], raw.functions.auth.ExportLoginToken)
    assert isinstance(import_call.args[0], raw.functions.auth.ImportLoginToken)
    assert import_call.args[0].token == b'other-dc-token'

    # Same persistence path as a non-migrated success.
    assert session_repo.get_decrypted_session_string('user-1') == 'EXPORTED_SESSION_STRING'
    entry = session_repo.get_by_user_id('user-1')
    assert entry is not None
    assert entry.telegram_user_id == 999


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_dc_migration_preserves_notification_bind(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    migrate_result = raw.types.auth.LoginTokenMigrateTo(dc_id=2, token=b'other-dc-token')
    raw_user = _real_raw_user()
    success_result = raw.types.auth.LoginTokenSuccess(authorization=raw.types.auth.Authorization(user=raw_user))

    client = _stub_client(invoke_results=[login_token_result])
    binder = NotificationBinder(lambda: 'aniGamerPlusBot')
    service = QrLoginService(
        API_ID, API_HASH, session_repo, client_factory=lambda **kw: client, notification_binder=binder
    )
    login_token, _url, _png = await service.start('user-1')

    client.invoke.reset_mock()
    client.invoke.side_effect = [migrate_result, success_result]

    with _mocked_dc_migration_session():
        result = await service.poll(login_token, 'user-1')

    assert result['status'] == 'success'
    # Notification-bind /start still fires via the user's own session after
    # a migrated login, exactly as it does for a non-migrated one.
    client.send_message.assert_awaited_once_with('@aniGamerPlusBot', '/start')


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_migration_failure_at_import_raises_clean_error(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    """B-11 (security audit): the API-facing ``error`` must be one of the
    small set of sanitized, generic strings — never the raw exception text
    (which could leak internal type names / protocol detail). The full
    detail (still exercised here indirectly — this is the exact failure
    that produces it) only reaches the log, not the response."""
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    migrate_result = raw.types.auth.LoginTokenMigrateTo(dc_id=2, token=b'other-dc-token')
    # After a migration, ImportLoginToken is expected to resolve to
    # LoginTokenSuccess; a bare LoginToken here is a protocol-legal but
    # unhandled shape and must fail clearly rather than being misread as a
    # still-pending scan.
    unexpected_result = raw.types.auth.LoginToken(expires=999999, token=b'unexpected-again')

    client = _stub_client(invoke_results=[login_token_result])
    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')

    client.invoke.reset_mock()
    client.invoke.side_effect = [migrate_result, unexpected_result]

    with _mocked_dc_migration_session():
        result = await service.poll(login_token, 'user-1')

    assert result['status'] == 'failed'
    assert result['error'] == '認證失敗，請重新綁定'
    assert 'LoginToken' not in result['error']  # never leak the raw type name
    assert '尚未支援' not in result['error']  # old hard-failure copy must be gone
    assert 'unexpected-again' not in result['error']  # never log the (migrated) token
    client.disconnect.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_dc_migration_repeated_after_import_fails_clean_error(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    """A second LoginTokenMigrateTo out of ImportLoginToken is protocol-legal
    (same auth.LoginToken union as the initial export) but not something this
    flow chains through — it must fail clearly rather than loop or stall."""
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    migrate_result = raw.types.auth.LoginTokenMigrateTo(dc_id=2, token=b'first-hop')
    second_migrate_result = raw.types.auth.LoginTokenMigrateTo(dc_id=3, token=b'second-hop')

    client = _stub_client(invoke_results=[login_token_result])
    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')

    client.invoke.reset_mock()
    client.invoke.side_effect = [migrate_result, second_migrate_result]

    with _mocked_dc_migration_session():
        result = await service.poll(login_token, 'user-1')

    assert result['status'] == 'failed'
    # B-11 (security audit): sanitized to the generic message — see
    # test_migration_failure_at_import_raises_clean_error above.
    assert result['error'] == '認證失敗，請重新綁定'
    assert 'LoginTokenMigrateTo' not in result['error']
    assert client.invoke.await_count == 2  # no automatic re-chained migration attempt


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_poll_while_migrating_returns_pending_without_reinvoking(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    """A poll landing while another one is mid-DC-switch for the same
    login_token must not race it by re-invoking ExportLoginToken on a client
    whose Session is being torn down and rebuilt underneath it."""
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    client = _stub_client(invoke_results=[login_token_result])
    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')

    service._pending[login_token].migrating = True
    client.invoke.reset_mock()

    result = await service.poll(login_token, 'user-1')

    assert result == {'status': 'pending'}
    client.invoke.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_poll_unknown_login_token_returns_failed(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    service = QrLoginService(API_ID, API_HASH, session_repo)

    result = await service.poll('nonexistent-token', 'user-1')

    assert result['status'] == 'failed'


# ---------------------------------------------------------------------------
# MEDIUM-1 security fix — login_token caller-ownership check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_poll_qr_by_wrong_user_returns_not_found(anyio_backend: str, session_repo: TgSessionRepository) -> None:
    """A login_token started by user-1 must not be pollable by user-2 — the
    response must be indistinguishable from an unknown/expired token, so a
    non-owner can't even confirm the token exists."""
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    client = _stub_client(invoke_results=[login_token_result])
    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')
    client.invoke.reset_mock()

    result = await service.poll(login_token, 'user-2')

    assert result == {'status': 'failed', 'error': 'login_token 不存在或已過期'}
    client.invoke.assert_not_called()  # never even touches the pending client


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_submit_qr_password_by_wrong_user_returns_not_found(
    anyio_backend: str, session_repo: TgSessionRepository
) -> None:
    login_token_result = raw.types.auth.LoginToken(expires=999999, token=b'token-bytes')
    client = _stub_client(invoke_results=[login_token_result, hydrogram.errors.SessionPasswordNeeded()])
    service = QrLoginService(API_ID, API_HASH, session_repo, client_factory=lambda **kw: client)
    login_token, _url, _png = await service.start('user-1')
    await service.poll(login_token, 'user-1')  # -> awaiting_password

    result = await service.submit_password(login_token, 'correct horse battery staple', 'user-2')

    assert result == {'status': 'failed', 'error': 'login_token 不存在或已過期'}
    client.check_password.assert_not_awaited()

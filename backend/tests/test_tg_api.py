"""Tests for ``/api/tg`` endpoints — session bind (QR/phone), watched-chat
CRUD, downloads, admin ``?user_id=`` scoping, and rate limiting.

``TgService`` is constructed for real, wired to ``fake_container``'s real
(sqlite-backed) tg repos — only the hydrogram-touching innards (client pool /
qr login / phone login / notification binder / download watcher) are fakes,
since their hydrogram-boundary behavior is already covered by
``test_tg_qr_login.py`` / ``test_tg_phone_login.py`` / ``test_tg_downloader.py``.
This exercises the real routing/scoping/serialization logic in ``tg_api.py``.
"""

from __future__ import annotations

import datetime
import types
import typing as T
import unittest.mock

import fastapi
import fastapi.testclient
import pytest

from app.api.deps import current_user_opt
from app.api.tg_api import get_tg_service, get_user_repo
from app.models import TgWatchedChatCreate
from app.persistence.user_repo import UserRow
from app.services.tg_service import TgService
from app.tg_downloader.notification_binder import NotificationBindOutcome, NotificationBindResult

from .conftest import FakeContainer


def _make_user(role: str, uid: str = 'test-user-1') -> UserRow:
    return UserRow(
        id=uid,
        username=f'test_{role}',
        avatar_url=None,
        role=role,
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )


def _as_user(base_client: fastapi.testclient.TestClient, user: UserRow) -> fastapi.testclient.TestClient:
    app = base_client.app
    app.dependency_overrides[current_user_opt] = lambda: user
    return fastapi.testclient.TestClient(app, raise_server_exceptions=True)


class _FakeQrLogin:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.poll_result: dict[str, T.Any] = {'status': 'pending'}
        self.password_result: dict[str, T.Any] = {'status': 'awaiting_password'}
        self.poll_calls: list[tuple[str, str]] = []
        self.submit_password_calls: list[tuple[str, str]] = []

    async def start(self, user_id: str) -> tuple[str, str, str]:
        self.started.append(user_id)
        return f'qr-token-for-{user_id}', 'tg://login?token=abc', 'data:image/png;base64,ZmFrZQ=='

    async def poll(self, login_token: str, user_id: str) -> dict[str, T.Any]:
        self.poll_calls.append((login_token, user_id))
        return self.poll_result

    async def submit_password(self, login_token: str, password: str, user_id: str) -> dict[str, T.Any]:
        self.submit_password_calls.append((login_token, user_id))
        return self.password_result


class _FakePhoneLogin:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.code_result: dict[str, T.Any] = {'status': 'awaiting_code'}
        self.password_result: dict[str, T.Any] = {'status': 'awaiting_password'}
        self.submit_code_calls: list[tuple[str, str]] = []
        self.submit_password_calls: list[tuple[str, str]] = []

    async def send_code(self, user_id: str, phone: str) -> str:
        self.sent.append((user_id, phone))
        return f'phone-token-for-{user_id}'

    async def submit_code(self, login_token: str, code: str, user_id: str) -> dict[str, T.Any]:
        self.submit_code_calls.append((login_token, user_id))
        return self.code_result

    async def submit_password(self, login_token: str, password: str, user_id: str) -> dict[str, T.Any]:
        self.submit_password_calls.append((login_token, user_id))
        return self.password_result


class _FakeClient:
    def __init__(self, dialogs: list[types.SimpleNamespace]) -> None:
        self._dialogs = dialogs

    async def get_dialogs(self, limit: int = 0):  # noqa: ANN201 — async generator, matches hydrogram's shape
        for d in self._dialogs:
            yield d


class _FakeClientPool:
    def __init__(
        self,
        connected_user_ids: set[str] | None = None,
        dialogs: list[types.SimpleNamespace] | None = None,
    ) -> None:
        self._connected = connected_user_ids or set()
        self._dialogs = dialogs or []

    async def get(self, user_id: str):  # noqa: ANN201
        if user_id not in self._connected:
            return None
        return _FakeClient(self._dialogs)

    async def disconnect(self, user_id: str) -> None:
        self._connected.discard(user_id)

    async def disconnect_all(self) -> None:
        self._connected.clear()

    def is_connected(self, user_id: str) -> bool:
        return user_id in self._connected


class _FakeNotificationBinder:
    def __init__(self, outcome: NotificationBindOutcome | None = None) -> None:
        self.outcome = outcome or NotificationBindOutcome(NotificationBindResult.SUCCESS)
        self.bind_calls = 0

    async def bind(self, client: object) -> NotificationBindOutcome:
        self.bind_calls += 1
        return self.outcome


class _FakeWatcher:
    def __init__(self) -> None:
        self.registered: list[str] = []
        self.unregistered: list[str] = []

    def register(self, user_id: str, client: object) -> None:
        self.registered.append(user_id)

    def unregister(self, user_id: str, client: object) -> None:
        self.unregistered.append(user_id)


def _make_tg_service(fake_container: FakeContainer, **overrides: object) -> TgService:
    return TgService(
        fake_container.tg_session_repo,
        fake_container.tg_watched_chat_repo,
        fake_container.tg_downloaded_media_repo,
        overrides.get('client_pool', _FakeClientPool()),  # type: ignore[arg-type]
        overrides.get('qr_login', _FakeQrLogin()),  # type: ignore[arg-type]
        overrides.get('phone_login', _FakePhoneLogin()),  # type: ignore[arg-type]
        overrides.get('notification_binder', _FakeNotificationBinder()),  # type: ignore[arg-type]
        overrides.get('watcher', _FakeWatcher()),  # type: ignore[arg-type]
    )


def _bind_service(client: fastapi.testclient.TestClient, service: TgService, fake_container: FakeContainer) -> None:
    client.app.dependency_overrides[get_tg_service] = lambda: service
    client.app.dependency_overrides[get_user_repo] = lambda: fake_container.user_repo


# ---------------------------------------------------------------------------
# 503 when the feature is unconfigured
# ---------------------------------------------------------------------------


def test_service_unavailable_returns_503(client: fastapi.testclient.TestClient) -> None:
    client.app.dependency_overrides[get_tg_service] = lambda: None
    r = client.post('/api/tg/session/qr-login')
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# QR login
# ---------------------------------------------------------------------------


def test_start_qr_login_returns_token_and_qr(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    qr = _FakeQrLogin()
    service = _make_tg_service(fake_container, qr_login=qr)
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/qr-login')

    assert r.status_code == 200
    body = r.json()
    assert body['login_token'] == 'qr-token-for-__anonymous_admin__'
    # B-10 (security audit): the raw tg://login?token=... deep link is the
    # login credential itself — it must never round-trip through the API
    # response, only the rendered PNG.
    assert 'qr_code_url' not in body
    assert body['qr_code_png_base64'].startswith('data:image/png;base64,')
    assert qr.started == ['__anonymous_admin__']


def test_poll_qr_login_returns_status(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    qr = _FakeQrLogin()
    qr.poll_result = {'status': 'awaiting_password'}
    service = _make_tg_service(fake_container, qr_login=qr)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/session/qr-login/some-token')

    assert r.status_code == 200
    assert r.json()['status'] == 'awaiting_password'


def test_poll_qr_login_success_does_not_leak_user_id(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """``TgService`` attaches ``user_id`` internally for client-pool warmup — must never reach the HTTP response."""
    qr = _FakeQrLogin()
    qr.poll_result = {'status': 'success', 'user_id': '__anonymous_admin__', 'telegram_handle': 'realhandle'}
    watcher = _FakeWatcher()
    service = _make_tg_service(
        fake_container, qr_login=qr, client_pool=_FakeClientPool({'__anonymous_admin__'}), watcher=watcher
    )
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/session/qr-login/some-token')

    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'success'
    assert body['telegram_handle'] == 'realhandle'
    assert 'user_id' not in body
    # Side-effect: TgService should have registered the watcher for the newly-bound user.
    assert watcher.registered == ['__anonymous_admin__']


def test_submit_qr_password(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    qr = _FakeQrLogin()
    service = _make_tg_service(fake_container, qr_login=qr)
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/qr-login/some-token/password', json={'password': 'hunter2'})

    assert r.status_code == 200
    assert r.json()['status'] == 'awaiting_password'


def test_poll_qr_login_passes_caller_user_id_to_service(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """MEDIUM-1 wiring check: the API layer must thread the *caller's own*
    user id through to QrLoginService.poll (not trust a client-supplied
    value) so the service's ownership check has something real to compare
    against."""
    qr = _FakeQrLogin()
    service = _make_tg_service(fake_container, qr_login=qr)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/session/qr-login/some-token')

    assert r.status_code == 200
    assert qr.poll_calls == [('some-token', '__anonymous_admin__')]


def test_submit_qr_password_passes_caller_user_id_to_service(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    qr = _FakeQrLogin()
    service = _make_tg_service(fake_container, qr_login=qr)
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/qr-login/some-token/password', json={'password': 'hunter2'})

    assert r.status_code == 200
    assert qr.submit_password_calls == [('some-token', '__anonymous_admin__')]


def test_qr_login_poll_rate_limited(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN_POLL', '2/minute')
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r1 = client.get('/api/tg/session/qr-login/some-token')
    r2 = client.get('/api/tg/session/qr-login/some-token')
    r3 = client.get('/api/tg/session/qr-login/some-token')

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


def test_qr_login_submit_password_rate_limited(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN_SUBMIT', '2/minute')
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r1 = client.post('/api/tg/session/qr-login/some-token/password', json={'password': 'hunter2'})
    r2 = client.post('/api/tg/session/qr-login/some-token/password', json={'password': 'hunter2'})
    r3 = client.post('/api/tg/session/qr-login/some-token/password', json={'password': 'hunter2'})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


def test_qr_login_rate_limited(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN', '2/minute')
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r1 = client.post('/api/tg/session/qr-login')
    r2 = client.post('/api/tg/session/qr-login')
    r3 = client.post('/api/tg/session/qr-login')

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


# ---------------------------------------------------------------------------
# Phone login
# ---------------------------------------------------------------------------


def test_start_phone_login(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    phone = _FakePhoneLogin()
    service = _make_tg_service(fake_container, phone_login=phone)
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/phone-login', json={'phone': '+886912345678'})

    assert r.status_code == 200
    body = r.json()
    assert body['phone'] == '+886912345678'
    assert body['login_token'] == 'phone-token-for-__anonymous_admin__'
    assert phone.sent == [('__anonymous_admin__', '+886912345678')]


def test_submit_phone_code(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    phone = _FakePhoneLogin()
    phone.code_result = {'status': 'awaiting_password'}
    service = _make_tg_service(fake_container, phone_login=phone)
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/phone-login/tok/code', json={'code': '12345'})

    assert r.status_code == 200
    assert r.json()['status'] == 'awaiting_password'


def test_submit_phone_password(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    phone = _FakePhoneLogin()
    phone.password_result = {'status': 'success', 'user_id': '__anonymous_admin__'}
    service = _make_tg_service(fake_container, phone_login=phone, client_pool=_FakeClientPool({'__anonymous_admin__'}))
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/phone-login/tok/password', json={'password': 'hunter2'})

    assert r.status_code == 200
    assert r.json()['status'] == 'success'


def test_submit_phone_code_passes_caller_user_id_to_service(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """MEDIUM-1 wiring check — see test_poll_qr_login_passes_caller_user_id_to_service."""
    phone = _FakePhoneLogin()
    phone.code_result = {'status': 'awaiting_password'}
    service = _make_tg_service(fake_container, phone_login=phone)
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/phone-login/tok/code', json={'code': '12345'})

    assert r.status_code == 200
    assert phone.submit_code_calls == [('tok', '__anonymous_admin__')]


def test_submit_phone_password_passes_caller_user_id_to_service(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    phone = _FakePhoneLogin()
    service = _make_tg_service(fake_container, phone_login=phone)
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/phone-login/tok/password', json={'password': 'hunter2'})

    assert r.status_code == 200
    assert phone.submit_password_calls == [('tok', '__anonymous_admin__')]


def test_phone_login_submit_rate_limited(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN_SUBMIT', '2/minute')
    phone = _FakePhoneLogin()
    phone.code_result = {'status': 'awaiting_password'}
    service = _make_tg_service(fake_container, phone_login=phone)
    _bind_service(client, service, fake_container)

    r1 = client.post('/api/tg/session/phone-login/tok/code', json={'code': '12345'})
    r2 = client.post('/api/tg/session/phone-login/tok/code', json={'code': '12345'})
    r3 = client.post('/api/tg/session/phone-login/tok/code', json={'code': '12345'})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


# ---------------------------------------------------------------------------
# Session status / revoke
# ---------------------------------------------------------------------------


def test_get_session_status_no_session(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/session')

    assert r.status_code == 200
    assert r.json() == {
        'status': 'no_session',
        'phone_tail4': None,
        'telegram_user_id': None,
        'telegram_handle': None,
        'last_active_at': None,
        'notification_bound': False,
        'notification_bind_status': None,
        'notification_bind_error': None,
    }


def test_get_session_status_active_never_includes_session_string(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    fake_container.tg_session_repo.upsert(
        '__anonymous_admin__',
        session_string='super-secret',
        phone_tail4='1234',
        telegram_user_id=555,
        notification_bind_status='bot_username_not_configured',
    )
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/session')

    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'active'
    assert body['phone_tail4'] == '1234'
    assert body['telegram_user_id'] == 555
    assert body['notification_bind_status'] == 'bot_username_not_configured'
    assert body['notification_bind_error'] is None
    assert 'session_string' not in body
    assert 'super-secret' not in r.text


def test_delete_session_revokes(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    fake_container.tg_session_repo.upsert(
        '__anonymous_admin__', session_string='s', phone_tail4=None, telegram_user_id=None
    )
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r = client.delete('/api/tg/session')

    assert r.status_code == 200
    entry = fake_container.tg_session_repo.get_by_user_id('__anonymous_admin__')
    assert entry is not None
    assert entry.status == 'revoked'


# ---------------------------------------------------------------------------
# Session — rebind notification (retry)
# ---------------------------------------------------------------------------


def test_rebind_notification_success_persists_outcome(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    fake_container.tg_session_repo.upsert(
        '__anonymous_admin__',
        session_string='s',
        phone_tail4=None,
        telegram_user_id=None,
        notification_bind_status='bot_username_not_configured',
    )
    binder = _FakeNotificationBinder(NotificationBindOutcome(NotificationBindResult.SUCCESS))
    service = _make_tg_service(
        fake_container,
        client_pool=_FakeClientPool({'__anonymous_admin__'}),
        notification_binder=binder,
    )
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/rebind-notification')

    assert r.status_code == 200
    body = r.json()
    assert body == {'notification_bind_status': 'success', 'notification_bind_error': None}
    assert binder.bind_calls == 1
    entry = fake_container.tg_session_repo.get_by_user_id('__anonymous_admin__')
    assert entry is not None
    assert entry.notification_bind_status == 'success'
    # The session string itself must be untouched by a rebind.
    assert fake_container.tg_session_repo.get_decrypted_session_string('__anonymous_admin__') == 's'


def test_rebind_notification_failure_persists_reason(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    fake_container.tg_session_repo.upsert(
        '__anonymous_admin__', session_string='s', phone_tail4=None, telegram_user_id=None
    )
    binder = _FakeNotificationBinder(
        NotificationBindOutcome(NotificationBindResult.BOT_NOT_FOUND, detail='USERNAME_INVALID')
    )
    service = _make_tg_service(
        fake_container,
        client_pool=_FakeClientPool({'__anonymous_admin__'}),
        notification_binder=binder,
    )
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/rebind-notification')

    assert r.status_code == 200
    body = r.json()
    assert body == {'notification_bind_status': 'bot_not_found', 'notification_bind_error': 'USERNAME_INVALID'}
    entry = fake_container.tg_session_repo.get_by_user_id('__anonymous_admin__')
    assert entry is not None
    assert entry.notification_bind_status == 'bot_not_found'
    assert entry.notification_bind_error == 'USERNAME_INVALID'


def test_rebind_notification_no_active_session_returns_404(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    service = _make_tg_service(fake_container, client_pool=_FakeClientPool())
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/session/rebind-notification')

    assert r.status_code == 404


def test_rebind_notification_requires_auth(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """With auth enabled and no session, the retry endpoint is 401 (not 403) — same
    convention as every other require_any_user route (see test_bt_api.py)."""
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    client.app.dependency_overrides[current_user_opt] = lambda: None

    r = client.post('/api/tg/session/rebind-notification')

    assert r.status_code == 401


def test_rebind_notification_rate_limited(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN', '2/minute')
    service = _make_tg_service(fake_container, client_pool=_FakeClientPool({'__anonymous_admin__'}))
    _bind_service(client, service, fake_container)

    r1 = client.post('/api/tg/session/rebind-notification')
    r2 = client.post('/api/tg/session/rebind-notification')
    r3 = client.post('/api/tg/session/rebind-notification')

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


# ---------------------------------------------------------------------------
# Admin ?user_id= scoping
# ---------------------------------------------------------------------------


def test_downloader_cannot_use_admin_user_id_override(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    downloader_client = _as_user(client, _make_user('downloader', uid='downloader-1'))
    service = _make_tg_service(fake_container)
    _bind_service(downloader_client, service, fake_container)

    r = downloader_client.get('/api/tg/session?user_id=someone-else')

    assert r.status_code == 403


def test_admin_user_id_override_unknown_user_returns_404(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    admin_client = _as_user(client, _make_user('admin', uid='admin-1'))
    service = _make_tg_service(fake_container)
    _bind_service(admin_client, service, fake_container)

    r = admin_client.get('/api/tg/session?user_id=does-not-exist')

    assert r.status_code == 404


def test_admin_user_id_override_views_other_users_session(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    fake_container.user_repo.upsert(id='other-user', username='other', avatar_url=None, role='downloader')
    fake_container.tg_session_repo.upsert('other-user', session_string='s', phone_tail4='9999', telegram_user_id=1)
    admin_client = _as_user(client, _make_user('admin', uid='admin-1'))
    service = _make_tg_service(fake_container)
    _bind_service(admin_client, service, fake_container)

    r = admin_client.get('/api/tg/session?user_id=other-user')

    assert r.status_code == 200
    assert r.json()['phone_tail4'] == '9999'


# ---------------------------------------------------------------------------
# Watched chats
# ---------------------------------------------------------------------------


def _chat_payload(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        'chat_id': -100123456,
        'chat_title': '測試頻道',
        'media_types': ['video'],
        'size_min_mb': None,
        'size_max_mb': None,
        'format_whitelist': None,
        'save_path': None,
        'enabled': True,
    }
    defaults.update(overrides)
    return defaults


def test_list_watched_chats_empty(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/chats')

    assert r.status_code == 200
    assert r.json() == []


def test_create_watched_chat(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    watcher = _FakeWatcher()
    service = _make_tg_service(fake_container, watcher=watcher, client_pool=_FakeClientPool({'__anonymous_admin__'}))
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/chats', json=_chat_payload())

    assert r.status_code == 201
    body = r.json()
    assert body['chat_title'] == '測試頻道'
    assert body['media_types'] == ['video']
    # Adding a chat for an already-connected user should refresh the watcher.
    assert watcher.registered == ['__anonymous_admin__']


def test_create_duplicate_watched_chat_returns_409(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    client.post('/api/tg/chats', json=_chat_payload())

    r = client.post('/api/tg/chats', json=_chat_payload())

    assert r.status_code == 409


def test_update_watched_chat(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    created = client.post('/api/tg/chats', json=_chat_payload()).json()

    r = client.patch(f'/api/tg/chats/{created["id"]}', json={'enabled': False})

    assert r.status_code == 200
    assert r.json()['enabled'] is False


def test_update_missing_watched_chat_returns_404(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r = client.patch('/api/tg/chats/999999', json={'enabled': False})

    assert r.status_code == 404


def test_delete_watched_chat(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    created = client.post('/api/tg/chats', json=_chat_payload()).json()

    r = client.delete(f'/api/tg/chats/{created["id"]}')

    assert r.status_code == 200
    assert client.get('/api/tg/chats').json() == []


def test_create_watched_chat_rate_limited(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN', '2/minute')
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r1 = client.post('/api/tg/chats', json=_chat_payload(chat_id=1))
    r2 = client.post('/api/tg/chats', json=_chat_payload(chat_id=2))
    r3 = client.post('/api/tg/chats', json=_chat_payload(chat_id=3))

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r3.status_code == 429


def test_update_watched_chat_rate_limited(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    created = client.post('/api/tg/chats', json=_chat_payload()).json()
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN', '2/minute')

    r1 = client.patch(f'/api/tg/chats/{created["id"]}', json={'enabled': False})
    r2 = client.patch(f'/api/tg/chats/{created["id"]}', json={'enabled': True})
    r3 = client.patch(f'/api/tg/chats/{created["id"]}', json={'enabled': False})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


def test_create_watched_chat_over_cap_returns_409(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """HIGH-6 defense-in-depth: the per-user watched-chat cap (independent of
    the rate limit above) rejects a create once the ceiling is hit."""
    from app.persistence.tg_watched_chat_repo import _MAX_WATCHED_CHATS_PER_USER

    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    for chat_id in range(_MAX_WATCHED_CHATS_PER_USER):
        fake_container.tg_watched_chat_repo.insert(
            '__anonymous_admin__',
            TgWatchedChatCreate(chat_id=chat_id, chat_title=f'頻道{chat_id}', media_types=['video']),
        )

    r = client.post('/api/tg/chats', json=_chat_payload(chat_id=999999))

    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Watched chats — historical backfill
# ---------------------------------------------------------------------------


def test_create_watched_chat_defaults_backfill_disabled(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/chats', json=_chat_payload())

    assert r.status_code == 201
    body = r.json()
    assert body['backfill_enabled'] is False
    assert body['backfill_days'] == 7
    assert body['backfill_status'] is None
    assert body['backfill_scanned_count'] == 0
    assert body['backfill_matched_count'] == 0


def test_create_watched_chat_with_backfill_enabled_dispatches_actor(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    with unittest.mock.patch('app.tasks.tg_backfill_tick.tg_backfill_actor.send') as fake_send:
        r = client.post('/api/tg/chats', json=_chat_payload(backfill_enabled=True, backfill_days=30))

    assert r.status_code == 201
    body = r.json()
    assert body['backfill_enabled'] is True
    assert body['backfill_days'] == 30
    assert body['backfill_status'] == 'pending'
    fake_send.assert_called_once_with('__anonymous_admin__', body['chat_id'], 30)


def test_create_watched_chat_backfill_days_out_of_range_rejected(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/chats', json=_chat_payload(backfill_enabled=True, backfill_days=91))

    assert r.status_code == 422


def test_update_backfill_enabled_false_to_true_dispatches_actor(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    created = client.post('/api/tg/chats', json=_chat_payload()).json()
    assert created['backfill_enabled'] is False

    with unittest.mock.patch('app.tasks.tg_backfill_tick.tg_backfill_actor.send') as fake_send:
        r = client.patch(f'/api/tg/chats/{created["id"]}', json={'backfill_enabled': True, 'backfill_days': 14})

    assert r.status_code == 200
    body = r.json()
    assert body['backfill_enabled'] is True
    assert body['backfill_status'] == 'pending'
    fake_send.assert_called_once_with('__anonymous_admin__', created['chat_id'], 14)


def test_update_unrelated_field_does_not_retrigger_backfill(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """backfill_enabled already True and untouched by this PATCH -> no actor dispatch."""
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    with unittest.mock.patch('app.tasks.tg_backfill_tick.tg_backfill_actor.send'):
        created = client.post('/api/tg/chats', json=_chat_payload(backfill_enabled=True)).json()

    with unittest.mock.patch('app.tasks.tg_backfill_tick.tg_backfill_actor.send') as fake_send:
        r = client.patch(f'/api/tg/chats/{created["id"]}', json={'enabled': False})

    assert r.status_code == 200
    fake_send.assert_not_called()


def test_retry_backfill_dispatches_actor(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    created = client.post('/api/tg/chats', json=_chat_payload()).json()

    with unittest.mock.patch('app.tasks.tg_backfill_tick.tg_backfill_actor.send') as fake_send:
        r = client.post(f'/api/tg/chats/{created["id"]}/backfill/retry')

    assert r.status_code == 200
    body = r.json()
    assert body['backfill_status'] == 'pending'
    fake_send.assert_called_once_with('__anonymous_admin__', created['chat_id'], 7)


def test_retry_backfill_missing_chat_returns_404(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r = client.post('/api/tg/chats/999999/backfill/retry')

    assert r.status_code == 404


def test_retry_backfill_requires_auth(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    client.app.dependency_overrides[current_user_opt] = lambda: None

    r = client.post('/api/tg/chats/1/backfill/retry')

    assert r.status_code == 401


def test_retry_backfill_rate_limited(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN', '2/minute')
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    created = client.post('/api/tg/chats', json=_chat_payload()).json()

    with unittest.mock.patch('app.tasks.tg_backfill_tick.tg_backfill_actor.send'):
        r1 = client.post(f'/api/tg/chats/{created["id"]}/backfill/retry')
        r2 = client.post(f'/api/tg/chats/{created["id"]}/backfill/retry')
        r3 = client.post(f'/api/tg/chats/{created["id"]}/backfill/retry')

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


def _fake_dialog(chat_id: int = -100999, title: str = '新的頻道', chat_type: str = 'channel') -> types.SimpleNamespace:
    chat = types.SimpleNamespace(id=chat_id, title=title, first_name=None, type=types.SimpleNamespace(value=chat_type))
    return types.SimpleNamespace(chat=chat)


def test_list_available_chats(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    pool = _FakeClientPool({'__anonymous_admin__'}, dialogs=[_fake_dialog()])
    service = _make_tg_service(fake_container, client_pool=pool)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/chats/available')

    assert r.status_code == 200
    body = r.json()
    assert body['truncated'] is False
    assert body['total_seen'] == 1
    items = body['items']
    assert len(items) == 1
    assert items[0]['chat_id'] == -100999
    assert items[0]['title'] == '新的頻道'
    assert items[0]['type'] == 'channel'
    assert items[0]['already_watched'] is False


def test_list_available_chats_marks_already_watched(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    fake_container.tg_watched_chat_repo.insert(
        '__anonymous_admin__',
        TgWatchedChatCreate(chat_id=-100999, chat_title='新的頻道', media_types=['video']),
    )
    pool = _FakeClientPool({'__anonymous_admin__'}, dialogs=[_fake_dialog()])
    service = _make_tg_service(fake_container, client_pool=pool)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/chats/available')

    assert r.json()['items'][0]['already_watched'] is True


def test_list_available_chats_truncates_at_default_cap(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """B-09/G-07: an account with more dialogs than the cap gets a truncated,
    not unbounded, response."""
    dialogs = [_fake_dialog(chat_id=-100000 - i, title=f'頻道{i}') for i in range(501)]
    pool = _FakeClientPool({'__anonymous_admin__'}, dialogs=dialogs)
    service = _make_tg_service(fake_container, client_pool=pool)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/chats/available')

    assert r.status_code == 200
    body = r.json()
    assert body['truncated'] is True
    assert len(body['items']) == 500
    assert body['total_seen'] == 500


def test_list_available_chats_limit_query_param_is_capped(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """The ``limit`` query param itself is bounded server-side — a caller can't
    request an unbounded fetch just by passing a huge ``limit``."""
    pool = _FakeClientPool({'__anonymous_admin__'}, dialogs=[_fake_dialog()])
    service = _make_tg_service(fake_container, client_pool=pool)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/chats/available?limit=100000')

    assert r.status_code == 422


def test_chats_available_rate_limited(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TG_CHATS_AVAILABLE', '2/minute')
    service = _make_tg_service(fake_container, client_pool=_FakeClientPool({'__anonymous_admin__'}))
    _bind_service(client, service, fake_container)

    r1 = client.get('/api/tg/chats/available')
    r2 = client.get('/api/tg/chats/available')
    r3 = client.get('/api/tg/chats/available')

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def test_list_downloads_paginated(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    for i in range(15):
        fake_container.tg_downloaded_media_repo.insert_if_new(
            '__anonymous_admin__',
            chat_id=1,
            message_id=i,
            file_id=f'f{i}',
            file_name=f'ep{i}.mp4',
            file_size=100,
            local_path=f'/x/ep{i}.mp4',
        )
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/downloads?page=1&size=10')

    assert r.status_code == 200
    body = r.json()
    assert body['total'] == 15
    assert len(body['items']) == 10
    assert body['page'] == 1
    assert body['size'] == 10


def test_list_downloads_local_path_is_basename_only(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """HIGH-2 security fix: the API must never leak the full server-side path
    (e.g. /app/bangumi/...) — only the filename is meaningful to the client."""
    fake_container.tg_downloaded_media_repo.insert_if_new(
        '__anonymous_admin__',
        chat_id=1,
        message_id=1,
        file_id='f1',
        file_name='episode01.mp4',
        file_size=100,
        local_path='/app/bangumi/tg/__anonymous_admin__/測試頻道/episode01.mp4',
    )
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    r = client.get('/api/tg/downloads')

    assert r.status_code == 200
    item = r.json()['items'][0]
    assert item['local_path'] == 'episode01.mp4'
    assert '/app/bangumi' not in item['local_path']
    assert '/app/bangumi' not in r.text


# ---------------------------------------------------------------------------
# Force re-download
# ---------------------------------------------------------------------------


def test_force_redownload_dispatches_actor_and_returns_queued(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    entry = fake_container.tg_downloaded_media_repo.insert_if_new(
        '__anonymous_admin__',
        chat_id=1,
        message_id=1,
        file_id='f1',
        file_name='episode01.mp4',
        file_size=100,
        local_path='/app/bangumi/tg/__anonymous_admin__/測試頻道/episode01.mp4',
    )
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    with unittest.mock.patch('app.tasks.tg_redownload_tick.tg_redownload_actor.send') as fake_send:
        r = client.post(f'/api/tg/downloads/{entry.id}/redownload')

    assert r.status_code == 200
    body = r.json()
    assert body == {'entry_id': entry.id, 'status': 'queued'}
    fake_send.assert_called_once_with('__anonymous_admin__', entry.id)


def test_force_redownload_missing_entry_returns_404(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    with unittest.mock.patch('app.tasks.tg_redownload_tick.tg_redownload_actor.send') as fake_send:
        r = client.post('/api/tg/downloads/999999/redownload')

    assert r.status_code == 404
    fake_send.assert_not_called()


def test_force_redownload_another_users_entry_returns_404_not_403(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer
) -> None:
    """Cross-user access must never leak whether the id exists — 404, the
    same response as a genuinely missing id, never a distinguishing 403."""
    entry = fake_container.tg_downloaded_media_repo.insert_if_new(
        'owner-user',
        chat_id=1,
        message_id=1,
        file_id='f1',
        file_name='episode01.mp4',
        file_size=100,
        local_path='/app/bangumi/tg/owner-user/測試頻道/episode01.mp4',
    )
    service = _make_tg_service(fake_container)
    attacker_client = _as_user(client, _make_user('user', uid='attacker-user'))
    _bind_service(attacker_client, service, fake_container)

    with unittest.mock.patch('app.tasks.tg_redownload_tick.tg_redownload_actor.send') as fake_send:
        r = attacker_client.post(f'/api/tg/downloads/{entry.id}/redownload')

    assert r.status_code == 404
    fake_send.assert_not_called()


def test_force_redownload_requires_auth(client: fastapi.testclient.TestClient, fake_container: FakeContainer) -> None:
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)
    client.app.dependency_overrides[current_user_opt] = lambda: None

    r = client.post('/api/tg/downloads/1/redownload')

    assert r.status_code == 401


def test_force_redownload_rate_limited(
    client: fastapi.testclient.TestClient, fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN', '2/minute')
    entry = fake_container.tg_downloaded_media_repo.insert_if_new(
        '__anonymous_admin__',
        chat_id=1,
        message_id=1,
        file_id='f1',
        file_name='episode01.mp4',
        file_size=100,
        local_path='/app/bangumi/tg/__anonymous_admin__/測試頻道/episode01.mp4',
    )
    service = _make_tg_service(fake_container)
    _bind_service(client, service, fake_container)

    with unittest.mock.patch('app.tasks.tg_redownload_tick.tg_redownload_actor.send'):
        r1 = client.post(f'/api/tg/downloads/{entry.id}/redownload')
        r2 = client.post(f'/api/tg/downloads/{entry.id}/redownload')
        r3 = client.post(f'/api/tg/downloads/{entry.id}/redownload')

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429

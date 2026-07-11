"""Server-side QR login flow.

hydrogram (a pyrogram-API-compatible fork; this flow was originally verified
against pyrogram 2.0.106's public API surface, which hydrogram matches) has
**no** high-level ``Client.qr_login()`` wrapper — QR login is driven
directly via the raw MTProto ``auth.exportLoginToken`` /
``auth.importLoginToken`` dance:

1. An unauthorized, in-memory ``hydrogram.Client`` calls
   ``auth.ExportLoginToken`` and gets back a ``LoginToken`` (token bytes +
   expiry). ``tg://login?token=<base64url(token)>`` is the deep link/QR
   payload — scanning it in an *already logged-in* Telegram app on another
   device calls the mirror-image ``auth.importLoginToken`` there.
2. This service polls by re-invoking ``ExportLoginToken`` on the same
   client. While unscanned it keeps returning ``LoginToken``. Once scanned
   + confirmed it returns ``LoginTokenSuccess`` (contains the
   authorization + user) — or raises ``SessionPasswordNeeded`` if the
   account has 2FA enabled, in which case :meth:`submit_password` finishes
   the flow via the normal ``Client.check_password``.
3. ``LoginTokenMigrateTo`` (the export/import needs to happen against a
   different data-center than the one this client initially connected to)
   is handled in-flow, mirroring hydrogram's own internal DC-migration dance
   (see ``hydrogram.methods.auth.send_code.SendCode.send_code``'s
   ``PhoneMigrate``/``NetworkMigrate`` handling): the client's live MTProto
   ``Session`` is stopped, a fresh auth key is created against the target
   DC via ``hydrogram.session.Auth``, the storage's ``dc_id``/``auth_key``
   are updated in place, and a new ``Session`` bound to that DC is started
   — all without ever calling ``Client.disconnect()``/``Client.connect()``.
   That matters because this service always builds its clients with
   ``in_memory=True``: hydrogram's ``MemoryStorage.open()`` unconditionally
   opens a brand-new ``sqlite3.connect(":memory:")`` handle on every call,
   and ``Client.load_session()`` then treats that fresh, empty storage as
   unauthorized and re-seeds it with ``dc_id=2`` plus a *newly generated*
   auth key on DC 2 — silently undoing any migration a naive
   disconnect/reconnect cycle tried to perform. Once reconnected, this
   client (the one that originally called ``ExportLoginToken`` and got back
   ``LoginTokenMigrateTo``) calls ``auth.ImportLoginToken`` on the new DC
   with the token bytes from the migration response, which resolves to the
   same ``LoginTokenSuccess`` shape as an in-place (non-migrated) success.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import io
import secrets
import time
import typing as T

import hydrogram
import hydrogram.errors
import hydrogram.raw
import hydrogram.session
import hydrogram.types
import qrcode

from ..security.log_scrub import scrub_exception_for_log
from ._login_common import _sanitize_login_error, persist_login_success

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..persistence.tg_session_repo import TgSessionRepository
    from .notification_binder import NotificationBinder

ClientFactory = T.Callable[..., 'hydrogram.Client']

#: How long a server-side pending QR/password login is kept around before
#: garbage-collection, regardless of poll activity.
_PENDING_LOGIN_TTL_SECONDS = 300.0

_LOG_TAG = 'TG QR登入'


@dataclasses.dataclass
class _PendingQrLogin:
    user_id: str
    client: hydrogram.Client
    status: T.Literal['pending', 'awaiting_password', 'success', 'failed'] = 'pending'
    error: str | None = None
    telegram_handle: str | None = None
    created_at: float = dataclasses.field(default_factory=time.monotonic)
    #: Set for the duration of an in-flight DC migration (``session.stop()``
    #: through ``session.start()`` + ``ImportLoginToken``). A concurrent
    #: :meth:`QrLoginService.poll` call landing while this is ``True`` must
    #: not re-invoke ``ExportLoginToken`` on the same client — its
    #: ``Session`` is being torn down and rebuilt underneath it — so it
    #: reports ``'pending'`` and returns immediately instead.
    migrating: bool = False


def build_qr_login_url(token: bytes) -> str:
    """Build the ``tg://login?token=...`` deep link for *token* (raw ``LoginToken.token`` bytes)."""
    encoded = base64.urlsafe_b64encode(token).rstrip(b'=').decode('ascii')
    return f'tg://login?token={encoded}'


def render_qr_png_data_uri(url: str) -> str:
    """Render *url* as a PNG QR code, returned as a ``data:image/png;base64,...`` URI."""
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    encoded = base64.b64encode(buf.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


class QrLoginService:
    """Drives the raw MTProto QR-login state machine, one pending attempt per ``login_token``."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_repo: TgSessionRepository,
        *,
        client_factory: ClientFactory | None = None,
        notification_binder: NotificationBinder | None = None,
        logger: Logger | None = None,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_repo = session_repo
        self._client_factory = client_factory or self._default_factory
        self._notification_binder = notification_binder
        self._logger = logger
        self._pending: dict[str, _PendingQrLogin] = {}

    def _default_factory(self, *, name: str) -> hydrogram.Client:
        return hydrogram.Client(name, api_id=self._api_id, api_hash=self._api_hash, in_memory=True)

    # ------------------------------------------------------------------ public

    async def start(self, user_id: str) -> tuple[str, str, str]:
        """Begin a QR login attempt. Returns ``(login_token, qr_code_url, qr_png_data_uri)``."""
        self._gc_expired()
        client = self._client_factory(name=f'tg-qr-{user_id}-{secrets.token_hex(4)}')
        await client.connect()
        try:
            exported = await self._export_login_token(client)
        except Exception:
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise

        if not isinstance(exported, hydrogram.raw.types.auth.LoginToken):
            # Already-authorized / migrate-to on the very first export is
            # exceedingly unlikely (a fresh in-memory client has no prior
            # auth), but handle it defensively rather than assume the type.
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise RuntimeError(f'unexpected auth.ExportLoginToken result on first export: {exported!r}')

        qr_url = build_qr_login_url(exported.token)
        qr_png = render_qr_png_data_uri(qr_url)

        login_token = secrets.token_urlsafe(24)
        self._pending[login_token] = _PendingQrLogin(user_id=user_id, client=client)
        return login_token, qr_url, qr_png

    async def poll(self, login_token: str, user_id: str) -> dict[str, T.Any]:
        """Advance and return the current status of a pending QR login.

        MEDIUM-1 (security audit): *user_id* must match the caller who
        started this login attempt (:meth:`start`). A mismatch returns the
        exact same "not found" response as an unknown/expired token, rather
        than a distinct "forbidden" — this avoids confirming to a
        non-owner that a given ``login_token`` even exists.
        """
        pending = self._pending.get(login_token)
        if pending is None or pending.user_id != user_id:
            return {'status': 'failed', 'error': 'login_token 不存在或已過期'}
        if pending.status in ('success', 'failed', 'awaiting_password'):
            return self._status_payload(pending)
        if pending.migrating:
            # Another poll for this same login_token is already mid-DC-switch
            # (see _PendingQrLogin.migrating) — don't race it by re-invoking
            # ExportLoginToken on a client whose Session is being replaced.
            return {'status': 'pending'}

        try:
            result = await self._export_login_token(pending.client)
        except hydrogram.errors.SessionPasswordNeeded:
            pending.status = 'awaiting_password'
            return self._status_payload(pending)
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller as 'failed'
            await self._fail(pending, exc)
            return self._status_payload(pending)

        if isinstance(result, hydrogram.raw.types.auth.LoginTokenMigrateTo):
            pending.migrating = True
            try:
                await self._complete_migration(pending, result)
            except Exception as exc:  # noqa: BLE001 — surfaced to the caller as 'failed'
                await self._fail(pending, exc)
            finally:
                pending.migrating = False
            return self._status_payload(pending)

        if isinstance(result, hydrogram.raw.types.auth.LoginTokenSuccess):
            user = hydrogram.types.User._parse(pending.client, result.authorization.user)
            try:
                await self._succeed(pending, user)
            except Exception as exc:  # noqa: BLE001 — e.g. session-persistence failure
                await self._fail(pending, exc)
            return self._status_payload(pending)

        # Still a plain LoginToken — unscanned/unconfirmed. Unchanged state.
        return self._status_payload(pending)

    async def submit_password(self, login_token: str, password: str, user_id: str) -> dict[str, T.Any]:
        """Complete a 2FA-gated QR login. Only valid once :meth:`poll` reports ``'awaiting_password'``.

        MEDIUM-1: same owner check as :meth:`poll` — see its docstring.
        """
        pending = self._pending.get(login_token)
        if pending is None or pending.user_id != user_id:
            return {'status': 'failed', 'error': 'login_token 不存在或已過期'}
        if pending.status != 'awaiting_password':
            return self._status_payload(pending)

        try:
            user = await pending.client.check_password(password)
        except hydrogram.errors.PasswordHashInvalid:
            return {'status': 'awaiting_password', 'error': '密碼錯誤，請再試一次'}
        except Exception as exc:  # noqa: BLE001
            await self._fail(pending, exc)
            return self._status_payload(pending)

        try:
            await self._succeed(pending, user)
        except Exception as exc:  # noqa: BLE001 — e.g. session-persistence failure
            await self._fail(pending, exc)
        return self._status_payload(pending)

    # ------------------------------------------------------------------ internal

    async def _export_login_token(self, client: hydrogram.Client) -> hydrogram.raw.base.auth.LoginToken:
        return await client.invoke(
            hydrogram.raw.functions.auth.ExportLoginToken(api_id=self._api_id, api_hash=self._api_hash, except_ids=[])
        )

    async def _complete_migration(
        self, pending: _PendingQrLogin, migrate: hydrogram.raw.types.auth.LoginTokenMigrateTo
    ) -> None:
        """Reconnect ``pending.client`` to ``migrate.dc_id`` and import the login token there.

        Deliberately does *not* call ``client.disconnect()``/``client.connect()``
        — see the module docstring for why that silently loses the migration
        on an ``in_memory=True`` client. Instead this mirrors hydrogram's own
        ``send_code()`` DC-migration handling: stop the live ``Session``,
        point storage at the new DC with a freshly created auth key (auth
        keys are per-DC and cannot be carried over), start a new ``Session``
        bound to it, then import. Never logs ``migrate.token`` itself.
        """
        client = pending.client

        await client.session.stop()
        await client.storage.dc_id(migrate.dc_id)
        new_dc_id = await client.storage.dc_id()
        test_mode = await client.storage.test_mode()
        new_auth_key = await hydrogram.session.Auth(client, new_dc_id, test_mode).create()
        await client.storage.auth_key(new_auth_key)

        client.session = hydrogram.session.Session(client, new_dc_id, new_auth_key, test_mode)
        await client.session.start()

        imported = await client.invoke(hydrogram.raw.functions.auth.ImportLoginToken(token=migrate.token))

        if not isinstance(imported, hydrogram.raw.types.auth.LoginTokenSuccess):
            # ImportLoginToken's declared return type is the same auth.LoginToken
            # union as ExportLoginToken, so protocol-legal-but-unhandled shapes
            # here include a bare LoginToken or — theoretically, though it
            # shouldn't happen — a second LoginTokenMigrateTo. Rather than loop
            # or guess, fail clearly with the type name so it's diagnosable.
            raise RuntimeError(
                '登入回應異常，請重試或改用手機驗證碼登入'
                f'（auth.ImportLoginToken 回傳非預期型別：{type(imported).__name__}）'
            )

        user = hydrogram.types.User._parse(client, imported.authorization.user)
        await self._succeed(pending, user)

    async def _succeed(self, pending: _PendingQrLogin, user: hydrogram.types.User) -> None:
        await persist_login_success(
            client=pending.client,
            user=user,
            user_id=pending.user_id,
            session_repo=self._session_repo,
            notification_binder=self._notification_binder,
        )
        pending.status = 'success'
        pending.telegram_handle = user.username
        with contextlib.suppress(Exception):
            await pending.client.disconnect()

    async def _fail(self, pending: _PendingQrLogin, error: Exception | str) -> None:
        """Mark *pending* failed.

        B-11 (security audit): *error* is either the raw exception that
        caused the failure — logged in full (token-scrubbed, length-capped
        via :func:`scrub_exception_for_log`) but exposed to the API caller
        only as one of :func:`_sanitize_login_error`'s bounded, generic
        strings — or an already-safe, hand-authored message (e.g. the
        "needs sign-up" case) that's used verbatim either way.
        """
        pending.status = 'failed'
        if isinstance(error, Exception):
            self._log_error(f'user_id={pending.user_id}: {scrub_exception_for_log(error)}')
            pending.error = _sanitize_login_error(error)
        else:
            self._log_error(f'user_id={pending.user_id}: {error}')
            pending.error = error
        with contextlib.suppress(Exception):
            await pending.client.disconnect()

    def _status_payload(self, pending: _PendingQrLogin) -> dict[str, T.Any]:
        payload: dict[str, T.Any] = {'status': pending.status}
        if pending.error:
            payload['error'] = pending.error
        if pending.telegram_handle:
            payload['telegram_handle'] = pending.telegram_handle
        if pending.status == 'success':
            # Exposed so TgService can warm the client pool / register the
            # download watcher for the right user without reaching into
            # this service's private ``_pending`` map.
            payload['user_id'] = pending.user_id
        return payload

    def _gc_expired(self) -> None:
        now = time.monotonic()
        expired = [tok for tok, p in self._pending.items() if now - p.created_at > _PENDING_LOGIN_TTL_SECONDS]
        for tok in expired:
            self._pending.pop(tok, None)

    def _log_error(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)

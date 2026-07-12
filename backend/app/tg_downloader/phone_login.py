"""Phone number + SMS/app code login flow — the fallback path when QR login
isn't convenient.

Unlike QR login, this uses hydrogram's ordinary high-level ``Client`` API
(``send_code`` / ``sign_in`` / ``check_password``) end to end, since
hydrogram handles any DC migration required for a given phone number
internally (:mod:`qr_login` handles its own ``auth.exportLoginToken``/
``importLoginToken`` DC migration directly, since that raw MTProto flow has
no high-level wrapper to lean on).
"""

from __future__ import annotations

import contextlib
import dataclasses
import secrets
import time
import typing as T

import hydrogram
import hydrogram.errors
import hydrogram.types

from ..security.log_scrub import scrub_exception_for_log
from ._login_common import _sanitize_login_error, persist_login_success

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..persistence.tg_session_repo import TgSessionRepository
    from .notification_binder import NotificationBinder

ClientFactory = T.Callable[..., 'hydrogram.Client']

_PENDING_LOGIN_TTL_SECONDS = 300.0
_LOG_TAG = 'TG手機登入'


@dataclasses.dataclass
class _PendingPhoneLogin:
    user_id: str
    client: hydrogram.Client
    phone_number: str
    phone_code_hash: str
    status: T.Literal['awaiting_code', 'awaiting_password', 'success', 'failed'] = 'awaiting_code'
    error: str | None = None
    telegram_handle: str | None = None
    created_at: float = dataclasses.field(default_factory=time.monotonic)


class PhoneLoginService:
    """Drives the phone-code (+ optional 2FA) login state machine."""

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
        self._pending: dict[str, _PendingPhoneLogin] = {}

    def _default_factory(self, *, name: str) -> hydrogram.Client:
        return hydrogram.Client(name, api_id=self._api_id, api_hash=self._api_hash, in_memory=True)

    # ------------------------------------------------------------------ public

    async def send_code(self, user_id: str, phone_number: str) -> str:
        """Start a phone-code login. Returns a ``login_token`` to poll/submit against."""
        self._gc_expired()
        client = self._client_factory(name=f'tg-phone-{user_id}-{secrets.token_hex(4)}')
        await client.connect()
        try:
            sent = await client.send_code(phone_number)
        except Exception:
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise

        login_token = secrets.token_urlsafe(24)
        self._pending[login_token] = _PendingPhoneLogin(
            user_id=user_id,
            client=client,
            phone_number=phone_number,
            phone_code_hash=sent.phone_code_hash,
        )
        return login_token

    async def submit_code(self, login_token: str, code: str, user_id: str) -> dict[str, T.Any]:
        """Submit the SMS/app code sent by :meth:`send_code`.

        MEDIUM-1 (security audit): *user_id* must match the caller who
        started this login attempt. A mismatch returns the exact same
        "not found" response as an unknown/expired token, rather than a
        distinct "forbidden" — this avoids confirming to a non-owner that a
        given ``login_token`` even exists.
        """
        pending = self._pending.get(login_token)
        if pending is None or pending.user_id != user_id:
            return {'status': 'failed', 'error': 'login_token 不存在或已過期'}
        if pending.status != 'awaiting_code':
            return self._status_payload(pending)

        try:
            result = await pending.client.sign_in(pending.phone_number, pending.phone_code_hash, code)
        except hydrogram.errors.SessionPasswordNeeded:
            # Clear any stale error from an earlier wrong-code retry on this
            # same pending login — 'awaiting_password' is a fresh step, not
            # a retry of 'awaiting_code'.
            pending.status = 'awaiting_password'
            pending.error = None
            return self._status_payload(pending)
        except hydrogram.errors.PhoneCodeInvalid:
            return self._retry(pending, 'awaiting_code', '驗證碼錯誤，請重新輸入')
        except hydrogram.errors.PhoneCodeExpired:
            return self._retry(pending, 'awaiting_code', '驗證碼已過期，請重新取得驗證碼')
        except Exception as exc:  # noqa: BLE001
            await self._fail(pending, exc)
            return self._status_payload(pending)

        if not isinstance(result, hydrogram.types.User):
            # types.TermsOfService (new account, must accept ToS) or a bare
            # bool — both are new-account-signup edge cases hydrogram's
            # `sign_up` handles; out of scope here since every real user
            # already has a Telegram account they're binding.
            await self._fail(pending, '此帳號需要先在官方 App 完成註冊/接受服務條款')
            return self._status_payload(pending)

        try:
            await self._succeed(pending, result)
        except Exception as exc:  # noqa: BLE001 — e.g. session-persistence failure
            await self._fail(pending, exc)
        return self._status_payload(pending)

    async def submit_password(self, login_token: str, password: str, user_id: str) -> dict[str, T.Any]:
        """MEDIUM-1: same owner check as :meth:`submit_code` — see its docstring."""
        pending = self._pending.get(login_token)
        if pending is None or pending.user_id != user_id:
            return {'status': 'failed', 'error': 'login_token 不存在或已過期'}
        if pending.status != 'awaiting_password':
            return self._status_payload(pending)

        try:
            user = await pending.client.check_password(password)
        except hydrogram.errors.PasswordHashInvalid:
            return self._retry(pending, 'awaiting_password', '密碼錯誤，請重新輸入')
        except Exception as exc:  # noqa: BLE001
            await self._fail(pending, exc)
            return self._status_payload(pending)

        try:
            await self._succeed(pending, user)
        except Exception as exc:  # noqa: BLE001 — e.g. session-persistence failure
            await self._fail(pending, exc)
        return self._status_payload(pending)

    # ------------------------------------------------------------------ internal

    async def _succeed(self, pending: _PendingPhoneLogin, user: hydrogram.types.User) -> None:
        await persist_login_success(
            client=pending.client,
            user=user,
            user_id=pending.user_id,
            session_repo=self._session_repo,
            notification_binder=self._notification_binder,
        )
        pending.status = 'success'
        # Clear any stale error from an earlier wrong-code/wrong-password
        # retry on this same pending login — success shouldn't surface it.
        pending.error = None
        pending.telegram_handle = user.username
        with contextlib.suppress(Exception):
            await pending.client.disconnect()

    async def _fail(self, pending: _PendingPhoneLogin, error: Exception | str) -> None:
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

    def _retry(
        self, pending: _PendingPhoneLogin, status: T.Literal['awaiting_code', 'awaiting_password'], error: str
    ) -> dict[str, T.Any]:
        """A wrong/expired code or wrong 2FA password — recoverable, not a
        :meth:`_fail`. The client stays connected and *pending* stays keyed
        under its ``login_token`` so the caller can retry the same flow."""
        pending.status = status
        pending.error = error
        return self._status_payload(pending)

    def _status_payload(self, pending: _PendingPhoneLogin) -> dict[str, T.Any]:
        payload: dict[str, T.Any] = {'status': pending.status}
        if pending.error:
            payload['error'] = pending.error
        if pending.telegram_handle:
            payload['telegram_handle'] = pending.telegram_handle
        if pending.status == 'success':
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

"""Shared "persist a successful login" helper for :mod:`qr_login` and
:mod:`phone_login`.

Both flows end the same way once hydrogram hands back an authorized
``types.User``: best-effort fire the notification-bind ``/start``, then
export the session string and encrypt-and-upsert it (recording the
notification-bind outcome alongside it). Factored out here instead of
duplicated in both modules.
"""

from __future__ import annotations

import contextlib
import typing as T

import sqlalchemy.exc

from ..security.crypto import FernetKeyMissingError

if T.TYPE_CHECKING:
    import hydrogram

    from ..persistence.tg_session_repo import TgSessionRepository
    from .notification_binder import NotificationBinder

#: B-11 (security audit): the small, bounded set of strings a login-failure
#: response's ``TgLoginStatusResponse.error`` may ever contain. Never the
#: raw ``str(exc)`` — hydrogram/MTProto exceptions (and, in principle, a
#: sqlite/OS error surfacing from the session-persistence step) can include
#: internal file paths or transport-layer detail that has no business
#: leaving the server. The raw, unsanitized detail still reaches the log
#: file (scrubbed for token-shaped substrings — see
#: ``app.security.log_scrub.scrub_exception_for_log``), just not the API.
_GENERIC_LOGIN_ERROR = '認證失敗，請重新綁定'
_SESSION_PERSIST_ERROR = 'session 儲存失敗'


def _sanitize_login_error(exc: Exception) -> str:
    """Map *exc* to one of a small set of safe, user-facing error strings.

    ``sqlalchemy.exc.SQLAlchemyError`` / ``FernetKeyMissingError`` / plain
    ``OSError`` cover the ways :func:`persist_login_success` below (session
    upsert + Fernet-encrypt) can fail — everything else (hydrogram RPC
    errors, the flow's own protocol-shape ``RuntimeError``s, ...) falls
    back to the generic auth-failure message.
    """
    if isinstance(exc, (sqlalchemy.exc.SQLAlchemyError, FernetKeyMissingError, OSError)):
        return _SESSION_PERSIST_ERROR
    return _GENERIC_LOGIN_ERROR


async def persist_login_success(
    *,
    client: hydrogram.Client,
    user: hydrogram.types.User,
    user_id: str,
    session_repo: TgSessionRepository,
    notification_binder: NotificationBinder | None,
) -> None:
    """Best-effort bind notifications, then export + encrypt + upsert the session.

    Never raises on the notification-bind step — a failed ``/start`` fire
    must not un-succeed an otherwise-successful session bind (the frontend
    surfaces a "重試通知綁定" retry button for that case instead, driven by
    the outcome persisted alongside the session below).
    """
    notification_bind_status: str | None = None
    notification_bind_error: str | None = None
    if notification_binder is not None:
        with contextlib.suppress(Exception):
            outcome = await notification_binder.bind(client)
            notification_bind_status = outcome.result.value
            notification_bind_error = outcome.detail

    session_string = await client.export_session_string()
    phone_tail4 = user.phone_number[-4:] if user.phone_number else None
    session_repo.upsert(
        user_id,
        session_string=session_string,
        phone_tail4=phone_tail4,
        telegram_user_id=user.id,
        notification_bind_status=notification_bind_status,
        notification_bind_error=notification_bind_error,
    )

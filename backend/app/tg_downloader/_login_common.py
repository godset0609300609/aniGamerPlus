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

if T.TYPE_CHECKING:
    import hydrogram

    from ..persistence.tg_session_repo import TgSessionRepository
    from .notification_binder import NotificationBinder


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

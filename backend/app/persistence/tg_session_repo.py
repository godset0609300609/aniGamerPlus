"""Repository for the ``tg_session`` table — one hydrogram (MTProto) session
per Discord user.

The session string is Fernet-encrypted at rest (see ``app.security.crypto``)
and is deliberately **not** part of :class:`TgSessionEntry` — the plain
dataclass returned by every read method here. Only
:meth:`TgSessionRepository.get_decrypted_session_string` ever decrypts it,
and that method exists solely for internal callers
(``app.tg_downloader.client_pool`` / the login flows) to hand the plaintext
to hydrogram. The API layer must never import or call it.
"""

from __future__ import annotations

import dataclasses
import datetime
import typing as T

import sqlalchemy
import sqlalchemy.dialects.sqlite

from ..security import crypto
from .models import TgSessionRow

if T.TYPE_CHECKING:
    from .db import Database


@dataclasses.dataclass(slots=True)
class TgSessionEntry:
    """API/service-safe snapshot of a ``tg_session`` row — no session string."""

    id: int
    user_id: str
    phone_tail4: str | None
    telegram_user_id: int | None
    status: str
    added_at: str
    last_active_at: str | None
    #: Outcome of the most recent notification-bind attempt — see
    #: ``app.tg_downloader.notification_binder.NotificationBindResult``.
    notification_bind_status: str | None = None
    notification_bind_error: str | None = None


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _to_entry(row: TgSessionRow) -> TgSessionEntry:
    return TgSessionEntry(
        id=row.id,
        user_id=row.user_id,
        phone_tail4=row.phone_tail4,
        telegram_user_id=row.telegram_user_id,
        status=row.status,
        added_at=row.added_at,
        last_active_at=row.last_active_at,
        notification_bind_status=row.notification_bind_status,
        notification_bind_error=row.notification_bind_error,
    )


class TgSessionRepository:
    """CRUD surface for the ``tg_session`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ reads

    def get_by_user_id(self, user_id: str) -> TgSessionEntry | None:
        with self._db.session() as session:
            stmt = sqlalchemy.select(TgSessionRow).where(TgSessionRow.user_id == user_id)
            row = session.scalars(stmt).first()
            return _to_entry(row) if row is not None else None

    def get_decrypted_session_string(self, user_id: str) -> str | None:
        """Return the plaintext session string for an *active* session, or ``None``.

        Internal-only — never call this from the API layer. Returns ``None``
        both when there is no row and when the row's ``status`` is not
        ``'active'`` (revoked/expired sessions must not be reconnected).
        """
        with self._db.session() as session:
            stmt = sqlalchemy.select(TgSessionRow).where(TgSessionRow.user_id == user_id)
            row = session.scalars(stmt).first()
            if row is None or row.status != 'active':
                return None
            encrypted = row.session_string_encrypted
        return crypto.decrypt_str(encrypted)

    def list_active(self) -> list[TgSessionEntry]:
        """Every session with ``status == 'active'`` — used at startup to warm the client pool."""
        with self._db.session() as session:
            stmt = sqlalchemy.select(TgSessionRow).where(TgSessionRow.status == 'active')
            return [_to_entry(r) for r in session.scalars(stmt).all()]

    # ------------------------------------------------------------------ writes

    def upsert(
        self,
        user_id: str,
        *,
        session_string: str,
        phone_tail4: str | None = None,
        telegram_user_id: int | None = None,
        notification_bind_status: str | None = None,
        notification_bind_error: str | None = None,
    ) -> TgSessionEntry:
        """Create or replace the session for *user_id* (``UNIQUE(user_id)``).

        A re-bind (QR/phone login run again for a user who already has a
        row) overwrites the encrypted session string and resets
        ``status='active'`` — this is the "upgrade legacy notify-only bind"
        and "re-bind after revoke" path.

        *notification_bind_status* / *notification_bind_error* record the
        outcome of the ``NotificationBinder.bind()`` call that
        ``_login_common.persist_login_success`` fires alongside this upsert —
        see ``app.tg_downloader.notification_binder.NotificationBindResult``.
        Left ``None`` when the caller didn't attempt a bind (e.g. direct
        repo tests).
        """
        now_iso = _now_iso()
        encrypted = crypto.encrypt_str(session_string)
        stmt = (
            sqlalchemy.dialects.sqlite.insert(TgSessionRow)
            .values(
                user_id=user_id,
                session_string_encrypted=encrypted,
                phone_tail4=phone_tail4,
                telegram_user_id=telegram_user_id,
                status='active',
                added_at=now_iso,
                last_active_at=now_iso,
                notification_bind_status=notification_bind_status,
                notification_bind_error=notification_bind_error,
            )
            .on_conflict_do_update(
                index_elements=['user_id'],
                set_={
                    'session_string_encrypted': encrypted,
                    'phone_tail4': phone_tail4,
                    'telegram_user_id': telegram_user_id,
                    'status': 'active',
                    'last_active_at': now_iso,
                    'notification_bind_status': notification_bind_status,
                    'notification_bind_error': notification_bind_error,
                },
            )
        )
        with self._db.session() as session:
            session.execute(stmt)
        entry = self.get_by_user_id(user_id)
        assert entry is not None  # noqa: S101 — just wrote it, must exist
        return entry

    def update_notification_bind_status(
        self,
        user_id: str,
        *,
        status: str | None,
        error: str | None,
    ) -> None:
        """Persist a fresh notification-bind outcome without touching the session itself.

        Used by the "重試通知綁定" retry path (``TgService.rebind_notification``)
        — the session string / phone / status columns are untouched; only
        the two notification-bind columns are updated. Idempotent no-op if
        *user_id* has no ``tg_session`` row.
        """
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(TgSessionRow)
                .where(TgSessionRow.user_id == user_id)
                .values(notification_bind_status=status, notification_bind_error=error)
            )
            session.execute(stmt)

    def revoke(self, user_id: str) -> None:
        """Flip ``status`` to ``'revoked'``. Idempotent no-op if no row exists.

        Does not delete the row (or the encrypted session string) — a
        revoked session is disconnected from the live client pool by the
        caller (``TgService``) but the row is kept for audit / re-bind
        history. Use a fresh :meth:`upsert` to re-bind.
        """
        with self._db.session() as session:
            stmt = sqlalchemy.update(TgSessionRow).where(TgSessionRow.user_id == user_id).values(status='revoked')
            session.execute(stmt)

    def mark_expired(self, user_id: str) -> None:
        """Flip ``status`` to ``'expired'`` — called when reconnecting a stored
        session fails (e.g. revoked from another device / Telegram-side logout)."""
        with self._db.session() as session:
            stmt = sqlalchemy.update(TgSessionRow).where(TgSessionRow.user_id == user_id).values(status='expired')
            session.execute(stmt)

    def touch_last_active(self, user_id: str) -> None:
        with self._db.session() as session:
            stmt = (
                sqlalchemy.update(TgSessionRow)
                .where(TgSessionRow.user_id == user_id)
                .values(last_active_at=_now_iso())
            )
            session.execute(stmt)

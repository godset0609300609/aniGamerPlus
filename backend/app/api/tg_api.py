"""Endpoints for the Telegram User API downloader — per-Discord-user session
bind (QR/phone login), watched-chat CRUD, and download history.

Every route requires a valid Discord user (``require_any_user``) and is
scoped to that user's own data. Read endpoints additionally accept an
admin-only ``?user_id=`` override (mirrors ``animelist_api``'s admin-view
convention) so an admin can inspect another user's Telegram integration
state for support purposes — but nobody can start a login flow, or
add/edit/delete a watched chat, on another user's behalf; that has no
sensible meaning (it's *their* Telegram account being authenticated).
"""

from __future__ import annotations

import functools
import pathlib
import typing as T

import anyio.to_thread
import fastapi

from .. import rate_limit
from ..models import (
    SimpleStatus,
    TgAvailableChat,
    TgCodeRequest,
    TgDownloadedMedia,
    TgDownloadsPage,
    TgLoginStatusResponse,
    TgPasswordRequest,
    TgPhoneLoginRequest,
    TgPhoneLoginResponse,
    TgQrLoginResponse,
    TgRebindNotificationResponse,
    TgSessionStatus,
    TgWatchedChat,
    TgWatchedChatCreate,
    TgWatchedChatUpdate,
)
from ..persistence.tg_watched_chat_repo import DuplicateWatchedChatError, TooManyWatchedChatsError
from ..persistence.user_repo import UserRepository, UserRow
from ..services._factory import container_bound
from ..services.tg_service import TgService
from .deps import require_any_user

router = fastapi.APIRouter(prefix='/tg', tags=['tg_downloader'])

get_tg_service: T.Callable[[], TgService | None] = container_bound(lambda c: c.tg_service)
"""FastAPI dependency resolver for :class:`TgService`. ``None`` when
``TG_API_ID``/``TG_API_HASH`` are not configured — every route below turns
that into a 503 rather than a 500."""

get_user_repo: T.Callable[[], UserRepository] = container_bound(lambda c: c.user_repo)
"""FastAPI dependency resolver for :class:`UserRepository` — only needed
here to validate an admin-supplied ``?user_id=`` override."""


def _require_service(service: TgService | None) -> TgService:
    if service is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Telegram User API 尚未設定（缺少 TG_API_ID / TG_API_HASH）',
        )
    return service


async def _scoped_user_id(
    user: UserRow,
    user_id_query: str | None,
    user_repo: UserRepository,
) -> str:
    """Resolve the effective ``user_id`` for a read endpoint.

    Admins may pass ``?user_id=`` to inspect another user's state; anyone
    else is always scoped to their own ``user.id``. An admin-supplied
    ``user_id`` that doesn't resolve to a real user is rejected with 404
    rather than silently falling back to the admin's own id.
    """
    if user_id_query is None or user_id_query == user.id:
        return user.id
    if user.role != 'admin':
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_403_FORBIDDEN, detail='Admin role required')
    target = await anyio.to_thread.run_sync(functools.partial(user_repo.get, user_id_query))
    if target is None:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail='user not found')
    return user_id_query


# ---------------------------------------------------------------------------
# Session — QR login
# ---------------------------------------------------------------------------


@router.post('/session/qr-login', response_model=TgQrLoginResponse)
@rate_limit.limiter.limit(rate_limit.tg_login_rate_limit, key_func=rate_limit.session_or_ip_key)
async def start_qr_login(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> TgQrLoginResponse:
    svc = _require_service(service)
    login_token, qr_url, qr_png = await svc.start_qr_login(user.id)
    return TgQrLoginResponse(login_token=login_token, qr_code_url=qr_url, qr_code_png_base64=qr_png)


@router.get('/session/qr-login/{login_token}', response_model=TgLoginStatusResponse)
@rate_limit.limiter.limit(rate_limit.tg_login_poll_rate_limit, key_func=rate_limit.session_or_ip_key)
async def poll_qr_login(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    login_token: str,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> TgLoginStatusResponse:
    svc = _require_service(service)
    result = await svc.poll_qr_login(login_token, user.id)
    return TgLoginStatusResponse(
        status=result['status'], error=result.get('error'), telegram_handle=result.get('telegram_handle')
    )


@router.post('/session/qr-login/{login_token}/password', response_model=TgLoginStatusResponse)
@rate_limit.limiter.limit(rate_limit.tg_login_submit_rate_limit, key_func=rate_limit.session_or_ip_key)
async def submit_qr_password(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    login_token: str,
    payload: TgPasswordRequest,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> TgLoginStatusResponse:
    svc = _require_service(service)
    result = await svc.submit_qr_password(login_token, payload.password, user.id)
    return TgLoginStatusResponse(
        status=result['status'], error=result.get('error'), telegram_handle=result.get('telegram_handle')
    )


# ---------------------------------------------------------------------------
# Session — phone login
# ---------------------------------------------------------------------------


@router.post('/session/phone-login', response_model=TgPhoneLoginResponse)
@rate_limit.limiter.limit(rate_limit.tg_login_rate_limit, key_func=rate_limit.session_or_ip_key)
async def start_phone_login(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    payload: TgPhoneLoginRequest,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> TgPhoneLoginResponse:
    svc = _require_service(service)
    login_token = await svc.send_phone_code(user.id, payload.phone)
    return TgPhoneLoginResponse(login_token=login_token, phone=payload.phone)


@router.post('/session/phone-login/{login_token}/code', response_model=TgLoginStatusResponse)
@rate_limit.limiter.limit(rate_limit.tg_login_submit_rate_limit, key_func=rate_limit.session_or_ip_key)
async def submit_phone_code(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    login_token: str,
    payload: TgCodeRequest,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> TgLoginStatusResponse:
    svc = _require_service(service)
    result = await svc.submit_phone_code(login_token, payload.code, user.id)
    return TgLoginStatusResponse(
        status=result['status'], error=result.get('error'), telegram_handle=result.get('telegram_handle')
    )


@router.post('/session/phone-login/{login_token}/password', response_model=TgLoginStatusResponse)
@rate_limit.limiter.limit(rate_limit.tg_login_submit_rate_limit, key_func=rate_limit.session_or_ip_key)
async def submit_phone_password(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    login_token: str,
    payload: TgPasswordRequest,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> TgLoginStatusResponse:
    svc = _require_service(service)
    result = await svc.submit_phone_password(login_token, payload.password, user.id)
    return TgLoginStatusResponse(
        status=result['status'], error=result.get('error'), telegram_handle=result.get('telegram_handle')
    )


# ---------------------------------------------------------------------------
# Session — status / unbind
# ---------------------------------------------------------------------------


@router.get('/session', response_model=TgSessionStatus)
async def get_session_status(
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(get_user_repo)],
    user_id: str | None = fastapi.Query(default=None),
) -> TgSessionStatus:
    effective_user_id = await _scoped_user_id(user, user_id, user_repo)
    # notification_bound reflects the *legacy* Bot-API binding
    # (users.telegram_chat_id, set via /api/profile/telegram/start-link) —
    # independent of whether the Telegram User API feature is configured at
    # all, so this is resolved even when `service` is None.
    target_user = await anyio.to_thread.run_sync(functools.partial(user_repo.get, effective_user_id))
    notification_bound = target_user is not None and target_user.telegram_chat_id is not None

    if service is None:
        return TgSessionStatus(status='no_session', notification_bound=notification_bound)
    entry = await service.get_status(effective_user_id)
    if entry is None:
        return TgSessionStatus(status='no_session', notification_bound=notification_bound)
    return TgSessionStatus(
        status=entry.status,  # type: ignore[arg-type]
        phone_tail4=entry.phone_tail4,
        telegram_user_id=entry.telegram_user_id,
        last_active_at=entry.last_active_at,
        notification_bound=notification_bound,
        notification_bind_status=entry.notification_bind_status,
        notification_bind_error=entry.notification_bind_error,
    )


@router.delete('/session', response_model=SimpleStatus)
async def delete_session(
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> SimpleStatus:
    svc = _require_service(service)
    await svc.revoke_session(user.id)
    return SimpleStatus()


@router.post('/session/rebind-notification', response_model=TgRebindNotificationResponse)
@rate_limit.limiter.limit(rate_limit.tg_login_rate_limit, key_func=rate_limit.session_or_ip_key)
async def rebind_notification(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> TgRebindNotificationResponse:
    """Retry the notification-bind ``/start`` for the caller's own already-bound session.

    Backs the "重試通知綁定" button shown next to the "帳號已綁定，通知綁定
    失敗" status in Settings. Rate-limited the same as the login-start
    endpoints (:data:`rate_limit.tg_login_rate_limit`) since it's a
    similarly-shaped live hydrogram call, gated per user.
    """
    svc = _require_service(service)
    outcome = await svc.rebind_notification(user.id)
    if outcome is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail='沒有可用的 Telegram 帳號 session，請先完成帳號綁定',
        )
    return TgRebindNotificationResponse(
        notification_bind_status=outcome.result.value, notification_bind_error=outcome.detail
    )


# ---------------------------------------------------------------------------
# Watched chats
# ---------------------------------------------------------------------------


@router.get('/chats', response_model=list[TgWatchedChat])
async def list_watched_chats(
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(get_user_repo)],
    user_id: str | None = fastapi.Query(default=None),
) -> list[TgWatchedChat]:
    svc = _require_service(service)
    effective_user_id = await _scoped_user_id(user, user_id, user_repo)
    return await svc.list_watched_chats(effective_user_id)


@router.post('/chats', response_model=TgWatchedChat, status_code=fastapi.status.HTTP_201_CREATED)
@rate_limit.limiter.limit(rate_limit.tg_login_rate_limit, key_func=rate_limit.session_or_ip_key)
async def create_watched_chat(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    payload: TgWatchedChatCreate,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> TgWatchedChat:
    """Add a watched chat.

    Rate-limited the same as the login-start endpoints
    (:data:`rate_limit.tg_login_rate_limit`, HIGH-6 of the security audit)
    — a create can trigger a backfill scan (a live MTProto walk), same cost
    class as a login start. Also enforced: a per-user cap on total watched
    chats (see ``TgWatchedChatRepository.insert`` /
    :data:`~app.persistence.tg_watched_chat_repo._MAX_WATCHED_CHATS_PER_USER`),
    independent of how quickly they're added.
    """
    svc = _require_service(service)
    try:
        return await svc.add_watched_chat(user.id, payload)
    except DuplicateWatchedChatError as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT, detail='此聊天已在監控列表中'
        ) from exc
    except TooManyWatchedChatsError as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT, detail='已達可監控聊天數量上限'
        ) from exc


@router.patch('/chats/{watched_chat_id}', response_model=TgWatchedChat)
@rate_limit.limiter.limit(rate_limit.tg_login_rate_limit, key_func=rate_limit.session_or_ip_key)
async def update_watched_chat(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    watched_chat_id: int,
    payload: TgWatchedChatUpdate,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> TgWatchedChat:
    """Update a watched chat.

    Rate-limited the same as ``create_watched_chat`` above (HIGH-6) — an
    update toggling ``backfill_enabled`` False -> True triggers the same
    live-MTProto-scan cost as create.
    """
    svc = _require_service(service)
    updated = await svc.update_watched_chat(user.id, watched_chat_id, payload)
    if updated is None:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail='watched chat not found')
    return updated


@router.delete('/chats/{watched_chat_id}', response_model=SimpleStatus)
async def delete_watched_chat(
    watched_chat_id: int,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> SimpleStatus:
    svc = _require_service(service)
    await svc.delete_watched_chat(user.id, watched_chat_id)
    return SimpleStatus()


@router.post('/chats/{watched_chat_id}/backfill/retry', response_model=TgWatchedChat)
@rate_limit.limiter.limit(rate_limit.tg_login_rate_limit, key_func=rate_limit.session_or_ip_key)
async def retry_backfill(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    watched_chat_id: int,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> TgWatchedChat:
    """Manually (re-)trigger a backfill scan — retry after ``failed``, or re-run after ``done``.

    Rate-limited the same as the login-start endpoints
    (:data:`rate_limit.tg_login_rate_limit`) since it dispatches a
    potentially long-running live MTProto scan, gated per user.
    """
    svc = _require_service(service)
    result = await svc.retry_backfill(user.id, watched_chat_id)
    if result is None:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail='watched chat not found')
    return result


@router.get('/chats/available', response_model=list[TgAvailableChat])
@rate_limit.limiter.limit(rate_limit.tg_chats_available_rate_limit, key_func=rate_limit.session_or_ip_key)
async def list_available_chats(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
) -> list[TgAvailableChat]:
    svc = _require_service(service)
    watched = {w.chat_id for w in await svc.list_watched_chats(user.id)}
    dialogs = await svc.list_available_chats(user.id)
    out: list[TgAvailableChat] = []
    for dialog in dialogs:
        chat = dialog.chat
        title = chat.title or chat.first_name or str(chat.id)
        out.append(
            TgAvailableChat(
                chat_id=chat.id,
                title=title,
                type=str(chat.type.value if hasattr(chat.type, 'value') else chat.type),
                already_watched=chat.id in watched,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


@router.get('/downloads', response_model=TgDownloadsPage)
async def list_downloads(
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TgService | None, fastapi.Depends(get_tg_service)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(get_user_repo)],
    user_id: str | None = fastapi.Query(default=None),
    page: int = fastapi.Query(default=1, ge=1),
    size: int = fastapi.Query(default=50, ge=10, le=200),
) -> TgDownloadsPage:
    svc = _require_service(service)
    effective_user_id = await _scoped_user_id(user, user_id, user_repo)
    items, total = await svc.list_downloads(effective_user_id, page=page, size=size)
    return TgDownloadsPage(
        items=[
            TgDownloadedMedia(
                id=e.id,
                chat_id=e.chat_id,
                chat_title=e.chat_title,
                message_id=e.message_id,
                file_name=e.file_name,
                file_size=e.file_size,
                downloaded_at=e.downloaded_at,
                # HIGH-2 (security audit): the DB stores the full server-side
                # path (needed internally — e.g. to display it in Telegram
                # notifications for the machine's operator), but the API
                # response must never leak the server's absolute filesystem
                # layout to the client. Only the basename is meaningful to
                # the end user anyway (they already know their own
                # bangumi_dir).
                local_path=pathlib.Path(e.local_path).name,
            )
            for e in items
        ],
        total=total,
        page=page,
        size=size,
    )


__all__ = ['router']

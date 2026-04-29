"""Discord OAuth2 + Telegram Mini App authentication endpoints.

Five routes::

    GET  /api/auth/login            — redirect to Discord authorize page
    GET  /api/auth/callback         — handle Discord redirect, set session
    POST /api/auth/logout           — clear session
    GET  /api/auth/me               — return current user info (requires session)
    POST /api/auth/telegram-webapp  — verify Telegram initData, issue session

All handlers are ``async def`` (memory: FastAPI routes are async).
"""

from __future__ import annotations

import collections.abc
import secrets
import typing as T

import anyio.to_thread
import fastapi
import httpx
import pydantic

from ..auth.deps import get_user_repo
from ..auth.discord_oauth import DiscordOAuthClient
from ..models import AppSettings
from ..persistence.settings_repo import SettingsRepository
from ..persistence.user_repo import UserRepository
from ..services._factory import container_bound
from ..services.telegram_webapp_auth import (
    InitDataVerificationError,
    verify_telegram_webapp_initdata,
)

router = fastapi.APIRouter(prefix='/api/auth', tags=['auth'])

# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


def _build_get_settings() -> collections.abc.Callable[[], AppSettings]:
    """Return a zero-arg dependency that loads AppSettings each call."""
    cached_load_fn: list[collections.abc.Callable[[], AppSettings]] = []

    def factory() -> AppSettings:
        if not cached_load_fn:
            from ..core import build_container

            cached_load_fn.append(build_container().settings_repo.load)
        return cached_load_fn[0]()

    return factory


get_settings = _build_get_settings()
"""FastAPI dependency: resolves current :class:`AppSettings`."""

get_oauth_client = container_bound(lambda c: c.oauth_client)
"""FastAPI dependency resolver for :class:`DiscordOAuthClient`."""

get_settings_repo = container_bound(lambda c: c.settings_repo)
"""FastAPI dependency resolver for :class:`SettingsRepository`."""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get('/login')
async def login(
    request: fastapi.Request,
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
    oauth: T.Annotated[DiscordOAuthClient, fastapi.Depends(get_oauth_client)],
) -> fastapi.responses.RedirectResponse:
    """Initiate Discord OAuth2 login.

    Generates a CSRF ``state`` token, stores it in the session, and
    redirects the browser to the Discord authorize URL.

    Returns 404 if ``auth.enabled`` is ``False``.
    """
    if not settings.auth.enabled:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail='Discord OAuth is not enabled',
        )

    state = secrets.token_urlsafe(32)
    request.session['oauth_state'] = state

    url = oauth.build_authorize_url(state)
    return fastapi.responses.RedirectResponse(url, status_code=302)


@router.get('/callback')
async def callback(
    request: fastapi.Request,
    code: str,
    state: str,
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
    oauth: T.Annotated[DiscordOAuthClient, fastapi.Depends(get_oauth_client)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(get_user_repo)],
) -> fastapi.responses.RedirectResponse:
    """Handle the Discord OAuth2 callback.

    Validates the CSRF state, exchanges the code for a token, fetches user
    info, upserts the user row, handles ``bootstrap_admin_ids`` auto-
    promotion, sets ``session['user_id']``, and redirects to ``/``.
    """
    if not settings.auth.enabled:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail='Discord OAuth is not enabled',
        )

    # Validate CSRF state.
    session_state: str | None = request.session.pop('oauth_state', None)
    if not session_state or not secrets.compare_digest(session_state, state):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail='OAuth state mismatch — possible CSRF attack',
        )

    # Exchange code → token → user info.
    try:
        token_data = await oauth.exchange_code(code, state)
    except httpx.HTTPStatusError as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail=f'Discord token exchange failed: {exc.response.status_code}',
        ) from exc

    access_token = str(token_data['access_token'])

    try:
        user_info = await oauth.fetch_user_info(access_token)
    except httpx.HTTPStatusError as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail=f'Discord user info fetch failed: {exc.response.status_code}',
        ) from exc

    discord_id = str(user_info['id'])
    username = str(user_info.get('username', ''))
    avatar_url_raw = user_info.get('avatar_url')
    avatar_url: str | None = str(avatar_url_raw) if avatar_url_raw is not None else None

    # Determine whether this user should be auto-promoted to admin.
    bootstrap_ids: list[str] = list(settings.auth.bootstrap_admin_ids)
    role: str | None = 'admin' if discord_id in bootstrap_ids else None

    # Upsert — preserves existing role unless we're setting one explicitly.
    _did = discord_id
    _un = username
    _av = avatar_url
    _ro = role
    await anyio.to_thread.run_sync(lambda: user_repo.upsert(id=_did, username=_un, avatar_url=_av, role=_ro))

    # Set the session.
    request.session['user_id'] = discord_id

    return fastapi.responses.RedirectResponse('/', status_code=302)


@router.post('/logout')
async def logout(request: fastapi.Request) -> dict:
    """Clear the session.  Always succeeds."""
    request.session.clear()
    return {'ok': True}


class TelegramWebAppLoginRequest(pydantic.BaseModel):
    """Request body for POST /api/auth/telegram-webapp."""

    init_data: str = pydantic.Field(..., min_length=1, alias='initData')

    model_config = pydantic.ConfigDict(populate_by_name=True)


@router.get('/me')
async def me(
    request: fastapi.Request,
    settings: T.Annotated[AppSettings, fastapi.Depends(get_settings)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(get_user_repo)],
) -> dict:
    """Return the authenticated user's profile.

    When ``auth.enabled`` is ``False`` (the default single-user mode),
    returns a sentinel admin user so the frontend skips the login gate.

    When ``auth.enabled`` is ``True`` and no session is present, raises
    ``HTTP 401``.
    """
    if not settings.auth.enabled:
        # Auth disabled — return an anonymous admin sentinel so the
        # frontend continues to work in the legacy single-user mode.
        return {
            'id': '__anonymous_admin__',
            'username': '本機使用者',
            'avatar_url': None,
            'role': 'admin',
            'telegram_bound': False,
            'telegram_notify_enabled': True,
        }

    user_id: str | None = request.session.get('user_id')
    if not user_id:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail='Authentication required',
        )
    uid = user_id
    user = await anyio.to_thread.run_sync(lambda: user_repo.get(uid))
    if user is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail='User not found',
        )
    return {
        'id': user.id,
        'username': user.username,
        'avatar_url': user.avatar_url,
        'role': user.role,
        'telegram_bound': user.telegram_chat_id is not None,
        'telegram_notify_enabled': user.telegram_notify_enabled,
    }


@router.post('/telegram-webapp')
async def telegram_webapp_login(
    request: fastapi.Request,
    payload: TelegramWebAppLoginRequest,
    settings_repo: T.Annotated[SettingsRepository, fastapi.Depends(get_settings_repo)],
    user_repo: T.Annotated[UserRepository, fastapi.Depends(get_user_repo)],
) -> dict[str, object]:
    """Verify Telegram Mini App initData and issue a server session.

    Replaces Discord OAuth for users who launch the dashboard via the
    Telegram bot's "🌐 開啟網頁版" button.  The user must already be
    bound (have a row with matching ``telegram_chat_id``); we never
    auto-create accounts here — pre-binding via /start is required.
    """
    settings = await anyio.to_thread.run_sync(settings_repo.load)
    bot_token = settings.telegram.bot_token
    if not bot_token:
        raise fastapi.HTTPException(status_code=503, detail='Telegram 未設定')

    try:
        verified = verify_telegram_webapp_initdata(payload.init_data, bot_token)
    except InitDataVerificationError as exc:
        raise fastapi.HTTPException(status_code=401, detail='initData 驗證失敗') from exc

    tg_user = verified.get('user')
    if not isinstance(tg_user, dict) or 'id' not in tg_user:
        raise fastapi.HTTPException(status_code=401, detail='缺少 user 欄位')
    chat_id = int(tg_user['id'])

    user = await anyio.to_thread.run_sync(user_repo.find_by_telegram_chat_id, chat_id)
    if user is None:
        raise fastapi.HTTPException(status_code=401, detail='請先在 bot 完成 /start 綁定')

    request.session['user_id'] = user.id
    return {'user_id': user.id, 'username': user.username, 'role': user.role}

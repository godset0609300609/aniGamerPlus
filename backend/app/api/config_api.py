"""Endpoints for reading and updating the web-visible config keys."""

from __future__ import annotations

import typing as T

import fastapi

from ..models import ConfigSchema, CookieUpdateRequest, SimpleStatus, WebSettings
from ..persistence.cookie_repo import CookieRepository
from ..persistence.user_repo import UserRow
from ..services._factory import container_bound
from ..services.config_service import ConfigService, get_config_service
from .deps import require_admin_user, require_any_user

router = fastapi.APIRouter(tags=['config'])

# ---------------------------------------------------------------------------
# Cookie repo dependency — container-bound, same pattern as other services.
# ---------------------------------------------------------------------------

get_cookie_repo: T.Callable[[], CookieRepository] = container_bound(lambda c: c.cookie_repo)
"""FastAPI dependency resolver for :class:`CookieRepository`."""


@router.get('/config/schema', response_model=ConfigSchema)
async def config_schema(
    _: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[ConfigService, fastapi.Depends(get_config_service)],
) -> ConfigSchema:
    return ConfigSchema(keys=service.schema_keys())


@router.get('/config', response_model=WebSettings)
async def read_config(
    _: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[ConfigService, fastapi.Depends(get_config_service)],
) -> WebSettings:
    return service.read()


@router.put('/config', response_model=SimpleStatus)
async def write_config(
    payload: WebSettings,
    _: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[ConfigService, fastapi.Depends(get_config_service)],
) -> SimpleStatus:
    service.write(payload)
    return SimpleStatus()


@router.put('/config/cookie', response_model=SimpleStatus)
async def put_cookie(
    payload: CookieUpdateRequest,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[CookieRepository, fastapi.Depends(get_cookie_repo)],
) -> SimpleStatus:
    """Set the Bahamut cookie string.  Admin only.  Never returned back."""
    repo.write(payload.cookie)
    return SimpleStatus()


@router.get('/config/cookie/status')
async def cookie_status(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[CookieRepository, fastapi.Depends(get_cookie_repo)],
) -> dict[str, bool]:
    """Return whether a cookie is currently configured (true/false only)."""
    return {'configured': repo.exists_and_nonempty()}

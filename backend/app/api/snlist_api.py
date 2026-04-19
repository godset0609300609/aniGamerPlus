"""Endpoints for reading / writing ``sn_list.txt``."""

from __future__ import annotations

import typing as T

import fastapi
import fastapi.responses

from ..models import SimpleStatus
from ..persistence.user_repo import UserRow
from ..services.snlist_service import SnListService, get_snlist_service
from .deps import require_admin_user

router = fastapi.APIRouter(tags=['sn_list'])


@router.get('/sn_list', response_class=fastapi.responses.PlainTextResponse)
async def get_sn_list(
    _: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[SnListService, fastapi.Depends(get_snlist_service)],
) -> str:
    return service.read()


@router.put('/sn_list', response_model=SimpleStatus)
async def put_sn_list(
    request: fastapi.Request,
    _: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[SnListService, fastapi.Depends(get_snlist_service)],
) -> SimpleStatus:
    body = (await request.body()).decode('utf-8')
    service.write(body)
    return SimpleStatus()

"""Endpoints for the structured "追番清單" view.

Permission rules
----------------
* ``GET /api/anime-list``:
  - anonymous → 401
  - downloader → 200, entries filtered to own sn list
  - admin → 200, all entries (with ``owner_username``)
* ``PUT /api/anime-list``:
  - anonymous → 401
  - downloader → 200, can only replace own entries (400 if foreign owner_id)
  - admin → 200, can replace any user's entries
"""

from __future__ import annotations

import typing as T

import fastapi

from ..models import AnimeListPayload, SimpleStatus
from ..persistence.user_repo import UserRow
from ..services.animelist_service import AnimeListService, get_animelist_service
from .deps import require_any_user

router = fastapi.APIRouter(tags=['anime_list'])


@router.get('/anime-list', response_model=AnimeListPayload)
async def get_anime_list(
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[AnimeListService, fastapi.Depends(get_animelist_service)],
) -> AnimeListPayload:
    return AnimeListPayload(entries=service.list_entries(user))


@router.put('/anime-list', response_model=SimpleStatus)
async def put_anime_list(
    payload: AnimeListPayload,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[AnimeListService, fastapi.Depends(get_animelist_service)],
) -> SimpleStatus:
    service.replace_entries(user, payload.entries)
    return SimpleStatus()

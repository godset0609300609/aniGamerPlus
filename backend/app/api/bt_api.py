"""Endpoints for the BT downloader pipeline — RSS feeds / keyword filters / entries.

Every route is admin-only, mirroring the "global shared, admin managed"
decision for the BT downloader filter list. All repo/service calls are sync
(SQLAlchemy, httpx) and are offloaded via ``anyio.to_thread.run_sync`` so the
route handlers themselves stay ``async def``.
"""

from __future__ import annotations

import typing as T

import anyio.to_thread
import fastapi

from .. import rate_limit
from ..bt_downloader.feed_fetcher import FeedFetchError
from ..models import (
    BtDispatchResponse,
    BtEntriesPage,
    BtFeed,
    BtFeedCreate,
    BtFeedEntry,
    BtFeedProbeRequest,
    BtFeedUpdate,
    BtFilter,
    BtFilterPayload,
    BtMatchCountRequest,
    BtMatchCountResponse,
    BtProbeResult,
    SimpleStatus,
)
from ..persistence.bt_feed_entry_repo import BtFeedEntryRepository
from ..persistence.bt_feed_repo import BtFeedRepository, DuplicateFeedError
from ..persistence.bt_filter_repo import BtFilterRepository
from ..persistence.user_repo import UserRow
from ..services._factory import container_bound
from ..services.bt_downloader_service import BtDownloaderService
from ..services.bt_manual_dispatch_service import (
    BtManualDispatchService,
    EntryNotFound,
    PutioApiError,
    PutioAuthFailed,
    PutioTokenMissing,
)
from ..services.bt_probe_service import BtProbeService
from .deps import require_admin_user

router = fastapi.APIRouter(prefix='/bt', tags=['bt_downloader'])

# ---------------------------------------------------------------------------
# Container-bound dependency resolvers.
# ---------------------------------------------------------------------------

get_bt_feed_repo: T.Callable[[], BtFeedRepository] = container_bound(lambda c: c.bt_feed_repo)
"""FastAPI dependency resolver for :class:`BtFeedRepository`."""

get_bt_filter_repo: T.Callable[[], BtFilterRepository] = container_bound(lambda c: c.bt_filter_repo)
"""FastAPI dependency resolver for :class:`BtFilterRepository`."""

get_bt_feed_entry_repo: T.Callable[[], BtFeedEntryRepository] = container_bound(lambda c: c.bt_feed_entry_repo)
"""FastAPI dependency resolver for :class:`BtFeedEntryRepository`."""

get_bt_probe_service: T.Callable[[], BtProbeService] = container_bound(lambda c: c.bt_probe_service)
"""FastAPI dependency resolver for :class:`BtProbeService`."""

get_bt_downloader_service: T.Callable[[], BtDownloaderService] = container_bound(lambda c: c.bt_downloader_service)
"""FastAPI dependency resolver for :class:`BtDownloaderService`."""

get_bt_manual_dispatch_service: T.Callable[[], BtManualDispatchService] = container_bound(
    lambda c: c.bt_manual_dispatch_service
)
"""FastAPI dependency resolver for :class:`BtManualDispatchService`."""


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------


@router.get('/feeds', response_model=list[BtFeed])
async def list_feeds(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[BtFeedRepository, fastapi.Depends(get_bt_feed_repo)],
    entry_repo: T.Annotated[BtFeedEntryRepository, fastapi.Depends(get_bt_feed_entry_repo)],
) -> list[BtFeed]:
    feeds = await anyio.to_thread.run_sync(repo.list_all)
    counts = await anyio.to_thread.run_sync(entry_repo.count_by_feed)
    return [feed.model_copy(update={'entry_count': counts.get(feed.id, 0)}) for feed in feeds]


@router.post('/feeds', response_model=BtFeed, status_code=fastapi.status.HTTP_201_CREATED)
async def create_feed(
    payload: BtFeedCreate,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[BtFeedRepository, fastapi.Depends(get_bt_feed_repo)],
) -> BtFeed:
    try:
        return await anyio.to_thread.run_sync(lambda: repo.create(payload))
    except DuplicateFeedError as exc:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_409_CONFLICT, detail='URL 已存在') from exc
    except ValueError as exc:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch('/feeds/{feed_id}', response_model=BtFeed)
async def update_feed(
    feed_id: int,
    payload: BtFeedUpdate,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[BtFeedRepository, fastapi.Depends(get_bt_feed_repo)],
) -> BtFeed:
    try:
        updated = await anyio.to_thread.run_sync(lambda: repo.update(feed_id, payload))
    except DuplicateFeedError as exc:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_409_CONFLICT, detail='URL 已存在') from exc
    if updated is None:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail='feed not found')
    return updated


@router.delete('/feeds/{feed_id}', response_model=SimpleStatus)
async def delete_feed(
    feed_id: int,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[BtFeedRepository, fastapi.Depends(get_bt_feed_repo)],
) -> SimpleStatus:
    existing = await anyio.to_thread.run_sync(lambda: repo.get(feed_id))
    if existing is None:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail='feed not found')
    await anyio.to_thread.run_sync(lambda: repo.delete(feed_id))
    return SimpleStatus()


@router.post('/feeds/probe', response_model=BtProbeResult)
@rate_limit.limiter.limit(rate_limit.bt_probe_rate_limit, key_func=rate_limit.session_or_ip_key)
async def probe_feed(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    payload: BtFeedProbeRequest,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[BtProbeService, fastapi.Depends(get_bt_probe_service)],
) -> BtProbeResult:
    try:
        return await anyio.to_thread.run_sync(lambda: service.probe(payload.url))
    except FeedFetchError as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail=f'RSS 抓取失敗: {exc.url}',
        ) from exc


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@router.get('/filters', response_model=list[BtFilter])
async def list_filters(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[BtFilterRepository, fastapi.Depends(get_bt_filter_repo)],
) -> list[BtFilter]:
    return await anyio.to_thread.run_sync(repo.list_all)


@router.put('/filters', response_model=SimpleStatus)
async def replace_filters(
    payload: BtFilterPayload,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[BtFilterRepository, fastapi.Depends(get_bt_filter_repo)],
) -> SimpleStatus:
    await anyio.to_thread.run_sync(lambda: repo.replace_all(payload.filters))
    return SimpleStatus()


@router.post('/filters/match-count', response_model=BtMatchCountResponse)
async def match_count(
    payload: BtMatchCountRequest,
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[BtDownloaderService, fastapi.Depends(get_bt_downloader_service)],
) -> BtMatchCountResponse:
    count, over_cap = await anyio.to_thread.run_sync(lambda: service.count_matching(payload.keywords))
    return BtMatchCountResponse(count=count, over_cap=over_cap)


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


@router.get('/entries', response_model=BtEntriesPage)
async def list_entries(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[BtFeedEntryRepository, fastapi.Depends(get_bt_feed_entry_repo)],
    days: int = fastapi.Query(default=7, ge=1, le=30, description='Days to look back'),
    filter_id: int | None = fastapi.Query(default=None, ge=1, description='Restrict to entries matched by this filter'),
    putio_status: str | None = fastapi.Query(
        default=None,
        max_length=40,
        description='Restrict by putio_status; use "__unassigned__" for NULL',
    ),
    page: int = fastapi.Query(default=1, ge=1),
    size: int = fastapi.Query(default=50, ge=10, le=200),
    q: str | None = fastapi.Query(
        default=None, min_length=1, max_length=200, description='Case-insensitive title substring'
    ),
) -> BtEntriesPage:
    d = days
    fid = filter_id
    unassigned_only = putio_status == '__unassigned__'
    status = None if unassigned_only else putio_status
    items, total = await anyio.to_thread.run_sync(
        lambda: repo.list_paginated(
            days=d,
            filter_id=fid,
            putio_status=status,
            unassigned_only=unassigned_only,
            q=q,
            page=page,
            size=size,
        )
    )
    return BtEntriesPage(items=items, total=total, page=page, size=size)


@router.get('/entries/search', response_model=list[BtFeedEntry])
async def search_entries(
    _user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    repo: T.Annotated[BtFeedEntryRepository, fastapi.Depends(get_bt_feed_entry_repo)],
    q: str = fastapi.Query(..., min_length=1, max_length=200),
    limit: int = fastapi.Query(default=20, ge=1, le=50),
) -> list[BtFeedEntry]:
    return await anyio.to_thread.run_sync(lambda: repo.search_by_title(q, limit=limit))


@router.post('/entries/{entry_id}/dispatch', response_model=BtDispatchResponse)
@rate_limit.limiter.limit(rate_limit.bt_dispatch_rate_limit, key_func=rate_limit.session_or_ip_key)
async def dispatch_entry(
    entry_id: int,
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    user: T.Annotated[UserRow, fastapi.Depends(require_admin_user)],
    service: T.Annotated[BtManualDispatchService, fastapi.Depends(get_bt_manual_dispatch_service)],
) -> BtDispatchResponse:
    """Manually (re-)dispatch a single entry to Put.io, regardless of filter-match state.

    Works whether the entry has already matched a filter or not, and
    whether it was previously dispatched or not — a re-dispatch overwrites
    ``putio_transfer_id`` with the new transfer's id.
    """
    try:
        result = await anyio.to_thread.run_sync(lambda: service.dispatch(entry_id, user.id))
    except EntryNotFound as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail='entry not found'
        ) from exc
    except PutioTokenMissing as exc:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PutioAuthFailed as exc:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except PutioApiError as exc:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BtDispatchResponse(**result)

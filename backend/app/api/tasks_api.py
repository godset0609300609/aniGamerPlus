"""Manual download task endpoint and task history endpoint."""

from __future__ import annotations

import typing as T

import anyio.to_thread
import fastapi

from .. import rate_limit
from ..models import ManualTaskRequest, SimpleStatus, TaskHistoryEntryOut
from ..persistence.task_history_repo import TaskHistoryEntry, TaskHistoryRepository
from ..persistence.user_repo import UserRow
from ..services.progress_service import ProgressService, get_progress_service
from ..services.task_service import TaskService, get_task_service
from .deps import require_any_user

router = fastapi.APIRouter(tags=['tasks'])


# ---------------------------------------------------------------------------
# TaskHistoryRepository dependency
# ---------------------------------------------------------------------------


def _get_task_history_repo() -> TaskHistoryRepository:
    from ..core import build_container

    return build_container().task_history_repo


get_task_history_repo: T.Callable[[], TaskHistoryRepository] = _get_task_history_repo


def _entry_to_out(entry: TaskHistoryEntry) -> TaskHistoryEntryOut:
    """Convert a repo :class:`TaskHistoryEntry` to the API output model."""
    return TaskHistoryEntryOut(
        id=entry.id,
        sn=entry.sn,
        filename=entry.filename,
        bangumi_name=entry.bangumi_name,
        episode=entry.episode,
        resolution=entry.resolution,
        final_status=entry.final_status,
        retries=entry.retries,
        started_at=entry.started_at.isoformat() if entry.started_at is not None else None,
        finished_at=entry.finished_at.isoformat() if entry.finished_at is not None else '',
        owner_id=entry.owner_id,
        source=entry.source,
        external_id=entry.external_id,
    )


@router.post('/tasks/manual', response_model=SimpleStatus)
@rate_limit.limiter.limit(rate_limit.tasks_manual_rate_limit, key_func=rate_limit.session_or_ip_key)
async def manual_task(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    payload: ManualTaskRequest,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TaskService, fastapi.Depends(get_task_service)],
) -> SimpleStatus:
    await service.enqueue(payload, user)
    return SimpleStatus()


@router.get('/tasks/history', response_model=list[TaskHistoryEntryOut])
async def task_history(
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    history_repo: T.Annotated[TaskHistoryRepository, fastapi.Depends(get_task_history_repo)],
    days: int = fastapi.Query(default=7, ge=1, le=90, description='Days to look back'),
) -> list[TaskHistoryEntryOut]:
    """Return task history from the DB for the last ``days`` days.

    * admin: sees all users' history.
    * downloader: sees only their own history.
    """
    user_filter: str | None = None if user.role == 'admin' else user.id
    d = days
    u = user_filter
    entries = await anyio.to_thread.run_sync(lambda: history_repo.list_recent(days=d, user_id=u))
    return [_entry_to_out(e) for e in entries]


@router.delete('/tasks/{sn}')
async def cancel_task(
    sn: int,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    service: T.Annotated[TaskService, fastapi.Depends(get_task_service)],
) -> dict[str, str]:
    """Cancel a running/queued task.

    Downloader role can only cancel their own tasks; admin can cancel any.
    Returns 404 if the task is not visible to the caller.
    Returns 503 if the scheduler is unreachable.
    """
    await service.cancel_task(sn, user)
    return {'status': 'ok'}


@router.post('/monitor/progress/{sn}/force-finish', response_model=SimpleStatus)
@rate_limit.limiter.limit(rate_limit.tasks_manual_rate_limit, key_func=rate_limit.session_or_ip_key)
async def dismiss_progress(
    request: fastapi.Request,  # required by slowapi's @limiter.limit decorator
    sn: int,
    user: T.Annotated[UserRow, fastapi.Depends(require_any_user)],
    progress: T.Annotated[ProgressService, fastapi.Depends(get_progress_service)],
) -> SimpleStatus:
    """Force-finish a stuck live-progress entry so it disappears from MonitorView.

    Unlike ``DELETE /api/tasks/{sn}`` (which signals a *live* actor to stop),
    this closes out the entry directly via ``ProgressBus.force_finish`` —
    the only way to dismiss a ghost card whose owning process is already
    dead (see ``BtProgressReconciler``'s module docstring). Downloader role
    can only dismiss their own tasks; admin can dismiss any. Idempotent:
    dismissing an already-terminal entry is a no-op. Returns 404 if the
    task is not visible to the caller.
    """
    await progress.force_finish(sn, user, status='已取消')
    return SimpleStatus()

"""WebSocket streaming task progress to the Web UI.

Authentication
--------------
The WebSocket handshake reads ``user_id`` from the session cookie.  If there
is no valid session (and ``auth.enabled`` is ``True``), the connection is
rejected with close code ``4401`` (app-specific, in the 4000-4999 range).

When ``auth.enabled`` is ``False`` (single-user mode) the sentinel admin is
used, which means all in-flight tasks are visible.

Filtering
---------
After handshake, every snapshot sent through the socket is pre-filtered by
:meth:`ProgressService.snapshot` according to the caller's role:

* admin — all tasks
* downloader — only tasks whose ``owner_id`` matches the user's id
"""

from __future__ import annotations

import asyncio
import typing as T

import fastapi

from ..persistence.user_repo import UserRow
from ..services.progress_service import ProgressService, get_progress_service
from .deps import current_user_opt

router = fastapi.APIRouter(tags=['progress'])

_POLL_INTERVAL_SECONDS = 1.0
_WS_CLOSE_UNAUTHENTICATED = 4401


@router.websocket('/ws/tasks_progress')
async def tasks_progress(
    ws: fastapi.WebSocket,
    progress: T.Annotated[ProgressService, fastapi.Depends(get_progress_service)],
    user: T.Annotated[UserRow | None, fastapi.Depends(current_user_opt)],
) -> None:
    """Stream per-user-filtered task progress snapshots over WebSocket.

    Rejects unauthenticated connections with close code 4401 when auth is
    enabled.  In single-user mode ``user`` is always the sentinel admin.
    """
    if user is None:
        # auth.enabled=True and no session — reject before accepting.
        await ws.close(code=_WS_CLOSE_UNAUTHENTICATED)
        return

    await ws.accept()
    try:
        while True:
            snapshot = progress.snapshot(user)
            # Send the ``tasks`` sub-mapping directly, matching the legacy
            # frontend wire shape: ``{ "<sn>": { rate, status, filename } }``.
            await ws.send_json({sn: entry.model_dump() for sn, entry in snapshot.tasks.items()})
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    except fastapi.WebSocketDisconnect:
        return

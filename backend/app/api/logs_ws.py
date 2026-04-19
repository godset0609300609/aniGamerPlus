"""WebSocket + REST endpoints for streaming/querying log records.

Authentication
--------------
Connections without a valid session are rejected with close code 4401.
In single-user mode (auth disabled) the sentinel admin is used, which
means all log records are visible.

Visibility policy
-----------------
``admin`` — all records.
``downloader`` — only records *without* an ``sn`` field (system-level).

Rationale: sn-scoped filtering would require a reverse lookup from sn →
owner_id (via the anime_list entry or the progress bus).  That coupling is
deferred; for now downloaders see system-level logs (no sn tag), which
covers connection notices, scheduler events, etc., without leaking other
users' download details.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import typing as T

import fastapi

from ..log_config import get_ring_buffer_handler
from ..persistence.user_repo import UserRow
from .deps import current_user_opt

router = fastapi.APIRouter(tags=['logs'])

_WS_CLOSE_UNAUTHENTICATED = 4401


async def _next_from_either(
    q: asyncio.Queue[T.Any],
    bridge: asyncio.Queue[T.Any],
) -> object:
    """Return the next item from *q* or *bridge*, whichever is ready first.

    Uses :func:`asyncio.wait` with ``FIRST_COMPLETED``.  When both queues
    happen to have items ready simultaneously (both tasks land in ``done``),
    the second consumed item is put back into its source queue via
    ``put_nowait`` so it is not lost on the next loop iteration.
    """
    get_q = asyncio.create_task(q.get())
    get_bridge = asyncio.create_task(bridge.get())
    done, pending = await asyncio.wait(
        {get_q, get_bridge},
        return_when=asyncio.FIRST_COMPLETED,
    )
    # Cancel whichever task is still waiting — it has not yet consumed an item
    # so nothing is lost.
    for task in pending:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # When both tasks complete simultaneously (common when a burst of records
    # arrives), ``done`` contains both.  Return the bridge result (disconnect
    # sentinel priority) and put the log-entry result back into *q* so the
    # next loop iteration delivers it.
    if get_q in done and get_bridge in done:
        q_result = get_q.result()
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(q_result)
        return get_bridge.result()

    # Normal case: exactly one task completed.
    return next(iter(done)).result()


def _entry_visible(entry: dict[str, T.Any], user: UserRow) -> bool:
    """Return True when *user* is allowed to see this log entry.

    Admins see everything.  Downloaders only see records that carry no
    ``sn`` attribute (system-level messages).  Records with an ``sn`` are
    download-specific; without an owner-lookup table we can't confirm the
    record belongs to *this* downloader, so they are filtered out.
    """
    if user.role == 'admin':
        return True
    # Downloader: only system-level records (sn is None / absent).
    return entry.get('sn') is None


@router.websocket('/ws/logs')
async def stream_logs(
    ws: fastapi.WebSocket,
    user: T.Annotated[UserRow | None, fastapi.Depends(current_user_opt)],
) -> None:
    """Stream log records over WebSocket.

    1. Sends historical snapshot (up to ``RingBufferHandler.BUFFER_SIZE``
       records) immediately after accepting, filtered by visibility policy.
    2. Pushes each new record as it is emitted by the logging system.

    Rejects unauthenticated connections with close code 4401.
    """
    if user is None:
        await ws.close(code=_WS_CLOSE_UNAUTHENTICATED)
        return

    handler = get_ring_buffer_handler()
    await ws.accept()

    # Sentinel object to signal disconnect from the watchdog task.
    _DISCONNECT = object()
    # Bridge queue: populated either by the ring buffer fan-out or by the
    # watchdog task (which adds _DISCONNECT when the client sends a close frame
    # or the receive raises).  This lets a single await drain both signals.
    bridge: asyncio.Queue[object] = asyncio.Queue()

    q = handler.subscribe(asyncio.get_running_loop())

    async def _watchdog() -> None:
        """Forward client-initiated disconnect into *bridge*."""
        try:
            await ws.receive()  # blocks until client sends data or close
        except Exception:  # noqa: BLE001
            pass
        finally:
            with contextlib.suppress(asyncio.QueueFull):
                bridge.put_nowait(_DISCONNECT)

    watchdog = asyncio.create_task(_watchdog(), name='logs-ws-watchdog')
    try:
        # Deliver historical records first so the UI can pre-populate.
        for entry in handler.snapshot():
            if _entry_visible(entry, user):
                await ws.send_json(entry)

        while True:
            # Wait for either a new log entry or a disconnect sentinel.
            item = await _next_from_either(q, bridge)
            if item is _DISCONNECT:
                break
            log_entry: dict[str, T.Any] = item  # type: ignore[assignment]
            if _entry_visible(log_entry, user):
                try:
                    await ws.send_json(log_entry)
                except Exception:  # noqa: BLE001
                    break
    except fastapi.WebSocketDisconnect, asyncio.CancelledError:
        pass
    finally:
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await watchdog
        handler.unsubscribe(q)


@router.get('/logs')
async def get_log_snapshot(
    user: T.Annotated[UserRow | None, fastapi.Depends(current_user_opt)],
    level: str = 'INFO',
    limit: int = 500,
) -> list[dict[str, T.Any]]:
    """Return the current ring-buffer snapshot as JSON.

    Accepts optional ``?level=WARNING`` filter (case-insensitive) and
    ``?limit=N`` to cap the response.  Useful for non-WS clients or for
    pre-populating a UI that connects later.

    Returns HTTP 401 when auth is enabled and there is no session.
    """
    if user is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail='Authentication required',
        )

    handler = get_ring_buffer_handler()
    level_upper = level.upper()
    level_no = logging.getLevelName(level_upper)
    if not isinstance(level_no, int):
        level_no = logging.INFO

    results: list[dict[str, T.Any]] = []
    for entry in handler.snapshot():
        if not _entry_visible(entry, user):
            continue
        entry_level_no = logging.getLevelName(entry.get('level', 'INFO'))
        if isinstance(entry_level_no, int) and entry_level_no >= level_no:
            results.append(entry)

    return results[-limit:]

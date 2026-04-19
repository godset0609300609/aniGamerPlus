"""HTTP + WebSocket proxy to the scheduler process.

The API process uses this class to forward manual-task requests and to
maintain a live mirror of the scheduler's progress snapshot via a
long-running WebSocket subscription.

If the scheduler is down:

* ``enqueue_manual`` raises :class:`SchedulerUnreachable`.
* ``latest_snapshot`` returns ``{}``.
* ``is_scheduler_up`` returns ``False``.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
import typing as T

import httpx

if T.TYPE_CHECKING:
    from ..downloader.progress import TaskProgress
    from ..models import ManualTaskRequest


_log = logging.getLogger(__name__)

# Reconnect back-off schedule (seconds): 1, 2, 4, 8, 16, then capped at 30.
_BACKOFF_SEQUENCE: tuple[float, ...] = (1, 2, 4, 8, 16)
_BACKOFF_CAP = 30.0

# Freshness window for is_scheduler_up() — wide enough to survive a short WS
# reconnect cycle (backoff up to 30 s) without falsely reporting the scheduler
# as down.  Only used by /api/health; NOT used to gate task enqueue.
_WS_FRESHNESS_SECONDS = 30.0


class SchedulerUnreachable(Exception):
    """Raised by :meth:`SchedulerProxy.enqueue_manual` when the scheduler HTTP
    endpoint is unreachable or returns a non-2xx response.

    Callers (e.g. :class:`~app.services.task_service.TaskService`) catch this
    and convert it to an :class:`~fastapi.HTTPException` with status 503.
    """


class SchedulerProxy:
    """Long-lived proxy to the internal scheduler HTTP/WS API."""

    def __init__(
        self,
        base_url: str,
        secret: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self._base_url = base_url.rstrip('/')
        self._secret = secret
        self._logger = logger or _log
        # Long-lived async HTTP client for REST calls.
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={'X-Internal-Secret': self._secret},
            timeout=10.0,
        )
        # Progress snapshot maintained by the WS subscription.
        self._last_snapshot: dict[int, TaskProgress] = {}
        # Monotonic timestamp of the last received WS message; None = never.
        self._last_ws_message_at: float | None = None

    # ------------------------------------------------------------------ public

    async def enqueue_manual(
        self,
        request: ManualTaskRequest,
        owner_id: str,
    ) -> None:
        """POST /internal/tasks/manual.

        Raises :class:`SchedulerUnreachable` if the scheduler HTTP endpoint
        cannot be reached or returns a non-2xx status.  The WS liveness state
        is intentionally *not* consulted here — the HTTP transport carries its
        own error signal.
        """
        payload = {
            'sn': str(request.sn),
            'resolution': request.resolution,
            'mode': request.mode,
            'thread': request.thread,
            'classify': request.classify,
            'danmu': request.danmu,
            'owner_id': owner_id,
        }
        try:
            resp = await self._client.post(
                '/internal/tasks/manual',
                json=payload,
                timeout=5.0,
            )
            resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise SchedulerUnreachable(f'Scheduler HTTP unreachable: {exc}') from exc
        except httpx.HTTPStatusError as exc:
            raise SchedulerUnreachable(f'Scheduler returned {exc.response.status_code}') from exc

    async def cancel_task(self, sn: int) -> None:
        """DELETE /internal/tasks/{sn}."""
        resp = await self._client.delete(f'/internal/tasks/{sn}')
        resp.raise_for_status()

    async def run_progress_subscription(self) -> None:
        """Maintain a WebSocket subscription to /internal/progress.

        Reconnects with exponential back-off (capped at 30 s) on any
        error.  Designed to run as a long-lived ``asyncio.Task``.
        """
        attempt = 0
        while True:
            try:
                await self._subscribe_once()
                # Clean WS close → reconnect immediately.
                attempt = 0
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                delay = _BACKOFF_SEQUENCE[min(attempt, len(_BACKOFF_SEQUENCE) - 1)]
                delay = min(delay, _BACKOFF_CAP)
                self._logger.warning(
                    'SchedulerProxy WS disconnected (retry in %.0fs): %s',
                    delay,
                    exc,
                    exc_info=True,
                )
                attempt += 1
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return

    async def fetch_health(self) -> dict[str, object]:
        """GET /internal/health from the scheduler process.

        Returns the raw JSON dict.  Raises ``httpx.RequestError`` if the
        scheduler is unreachable or ``httpx.HTTPStatusError`` on non-2xx.
        Times out after 1 second by default (callers may wrap with
        ``asyncio.wait_for`` for a stricter budget).
        """
        resp = await self._client.get('/internal/health', timeout=1.0)
        resp.raise_for_status()
        result: dict[str, object] = resp.json()
        return result

    async def close(self) -> None:
        """Close the HTTP client (call from lifespan shutdown)."""
        await self._client.aclose()

    def latest_snapshot(self) -> dict[int, TaskProgress]:
        """Return last received progress snapshot.  ``{}`` if none yet."""
        return dict(self._last_snapshot)

    def is_scheduler_up(self) -> bool:
        """True if a WS message arrived within the last ``_WS_FRESHNESS_SECONDS``.

        Used only by ``/api/health`` to surface aggregate scheduler status.
        Task enqueue/cancel do **not** consult this — they rely on the HTTP
        round-trip result instead so a short WS reconnect window never causes
        a spurious 503.
        """
        if self._last_ws_message_at is None:
            return False
        return time.monotonic() - self._last_ws_message_at < _WS_FRESHNESS_SECONDS

    # ------------------------------------------------------------------ internals

    async def _subscribe_once(self) -> None:
        """Open one WebSocket connection and drain messages until it closes."""
        import websockets.asyncio.client

        ws_url = self._base_url.replace('http://', 'ws://', 1).replace('https://', 'wss://', 1)
        ws_url = f'{ws_url}/internal/progress'
        extra_headers = {'X-Internal-Secret': self._secret}
        async with websockets.asyncio.client.connect(
            ws_url,
            additional_headers=extra_headers,
            ping_interval=30,
            ping_timeout=60,
        ) as ws:
            async for raw in ws:
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    data: dict[str, object] = json.loads(raw)
                    self._last_ws_message_at = time.monotonic()
                    self._last_snapshot = _parse_snapshot(data)
                except Exception:  # noqa: BLE001
                    self._logger.warning(
                        'SchedulerProxy: bad message from scheduler WS — skipping',
                        exc_info=True,
                    )


# ---------------------------------------------------------------------------
# Helper — parse the raw JSON snapshot from the scheduler WS
# ---------------------------------------------------------------------------


def _parse_snapshot(data: dict[str, object]) -> dict[int, TaskProgress]:
    """Convert the scheduler WS JSON to ``{sn: TaskProgress}``."""
    from ..downloader.progress import TaskProgress

    result: dict[int, TaskProgress] = {}
    for sn_str, raw_entry in data.items():
        if not isinstance(raw_entry, dict):
            continue
        entry: dict[str, object] = raw_entry
        try:
            sn = int(sn_str)
            started_at: datetime.datetime | None = None
            started_raw = entry.get('started_at')
            if isinstance(started_raw, str):
                started_at = datetime.datetime.fromisoformat(started_raw)

            finished_at: datetime.datetime | None = None
            finished_raw = entry.get('finished_at')
            if isinstance(finished_raw, str):
                finished_at = datetime.datetime.fromisoformat(finished_raw)

            cooldown_until: datetime.datetime | None = None
            cooldown_raw = entry.get('cooldown_until')
            if isinstance(cooldown_raw, str):
                cooldown_until = datetime.datetime.fromisoformat(cooldown_raw)

            rate_raw = entry.get('rate', 0.0)
            retries_raw = entry.get('retries', 0)
            result[sn] = TaskProgress(
                sn=sn,
                rate=float(rate_raw) if isinstance(rate_raw, (int, float)) else 0.0,
                status=str(entry.get('status', '')),
                filename=str(entry.get('filename', '')),
                bangumi_name=entry.get('bangumi_name'),  # type: ignore[arg-type]
                episode=entry.get('episode'),  # type: ignore[arg-type]
                resolution=entry.get('resolution'),  # type: ignore[arg-type]
                speed_mbps=entry.get('speed_mbps'),  # type: ignore[arg-type]
                eta_seconds=entry.get('eta_seconds'),  # type: ignore[arg-type]
                retries=int(retries_raw) if isinstance(retries_raw, (int, float)) else 0,
                started_at=started_at,
                finished_at=finished_at,
                cooldown_until=cooldown_until,
                owner_id=entry.get('owner_id'),  # type: ignore[arg-type]
            )
        except Exception:  # noqa: BLE001 — skip malformed entries
            continue
    return result

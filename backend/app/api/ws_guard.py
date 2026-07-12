"""Shared WebSocket hardening helpers for ``progress_ws`` and ``logs_ws``.

Two independent protections, both applied at the handshake before
``websocket.accept()``:

* :class:`WebSocketConnectionRegistry` — caps concurrent connections per
  user (across both WS endpoints combined) so a single account cannot
  exhaust server-side connection slots.
* :func:`is_origin_allowed` — rejects a handshake whose ``Origin`` header
  doesn't match the configured allowlist, mitigating Cross-Site WebSocket
  Hijacking (a malicious page in the browser opening a WS connection to
  this server using the victim's session cookie).
"""

from __future__ import annotations

import collections
import os
import threading

#: Maximum concurrent WebSocket connections allowed per user, summed across
#: ``/api/ws/tasks_progress`` and ``/api/ws/logs`` (one shared registry keyed
#: by user_id only, not by endpoint).
_MAX_WS_PER_USER = 5

#: Close code used when a user exceeds :data:`_MAX_WS_PER_USER`. In the
#: 4000-4999 application-reserved range, distinct from the standard 1008
#: "Policy Violation" used for RBAC / origin rejections. Mirrors HTTP 429
#: semantics ("Try Again Later" is the closest standard WS close-code
#: description for 4429-style codes; there is no official one, so this is a
#: private-use code documented here and on the client).
WS_CLOSE_TOO_MANY_CONNECTIONS = 4429

#: Default allowlist when ``ANIGAMERPLUS_WS_ALLOWED_ORIGINS`` is unset:
#: the local Vite dev server origins plus ``http://web``, the nginx service
#: name reachable inside the docker-compose network.
_DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = (
    'http://localhost:5173',
    'http://127.0.0.1:5173',
    'http://localhost:4173',
    'http://web',
)

_WS_ALLOWED_ORIGINS_ENV_VAR = 'ANIGAMERPLUS_WS_ALLOWED_ORIGINS'


class WebSocketConnectionRegistry:
    """Thread-safe ``{user_id: count}`` connection tracker.

    ``try_acquire`` / ``release`` bracket the lifetime of one WebSocket
    connection. A ``threading.Lock`` guards the counts even though both
    call sites currently run on the single asyncio event loop thread — this
    keeps the class correct regardless of how many worker threads end up
    calling into it (e.g. if a future deployment runs multiple uvicorn
    workers sharing this process-wide singleton).
    """

    def __init__(self, max_per_user: int = _MAX_WS_PER_USER) -> None:
        self._max_per_user = max_per_user
        self._counts: collections.Counter[str] = collections.Counter()
        self._lock = threading.Lock()

    def try_acquire(self, user_id: str) -> bool:
        """Increment *user_id*'s count and return True, unless already at the cap.

        Returns False (without incrementing) when *user_id* already holds
        :attr:`_max_per_user` concurrent connections.
        """
        with self._lock:
            if self._counts[user_id] >= self._max_per_user:
                return False
            self._counts[user_id] += 1
            return True

    def release(self, user_id: str) -> None:
        """Decrement *user_id*'s count. No-op if already at zero."""
        with self._lock:
            if self._counts[user_id] > 0:
                self._counts[user_id] -= 1
                if self._counts[user_id] == 0:
                    del self._counts[user_id]

    def count(self, user_id: str) -> int:
        """Return the current connection count for *user_id* (test helper)."""
        with self._lock:
            return self._counts[user_id]


_registry: WebSocketConnectionRegistry | None = None


def get_ws_connection_registry() -> WebSocketConnectionRegistry:
    """Return (and lazily create) the module-level singleton registry."""
    global _registry
    if _registry is None:
        _registry = WebSocketConnectionRegistry()
    return _registry


def allowed_ws_origins() -> tuple[str, ...]:
    """Return the configured Origin allowlist.

    Reads a comma-separated list from ``ANIGAMERPLUS_WS_ALLOWED_ORIGINS``
    when set; otherwise falls back to :data:`_DEFAULT_ALLOWED_ORIGINS`.
    """
    env = os.environ.get(_WS_ALLOWED_ORIGINS_ENV_VAR)
    if env:
        return tuple(o.strip() for o in env.split(',') if o.strip())
    return _DEFAULT_ALLOWED_ORIGINS


def is_origin_allowed(origin: str | None) -> bool:
    """Return True when *origin* is acceptable for a WebSocket handshake.

    A missing ``Origin`` header (``None``) is allowed: non-browser clients
    (server-to-server calls, CLI tools, most test harnesses) don't send
    one, and Cross-Site WebSocket Hijacking — the attack this check
    mitigates — is only possible from a browser context, which always sends
    an ``Origin`` header on cross-origin (and same-origin, in most modern
    browsers) requests. When an ``Origin`` header *is* present, it must
    match :func:`allowed_ws_origins`.
    """
    if origin is None:
        return True
    return origin in allowed_ws_origins()


__all__ = [
    'WS_CLOSE_TOO_MANY_CONNECTIONS',
    'WebSocketConnectionRegistry',
    'allowed_ws_origins',
    'get_ws_connection_registry',
    'is_origin_allowed',
]

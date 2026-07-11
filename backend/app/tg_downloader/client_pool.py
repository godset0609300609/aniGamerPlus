"""Manages one long-lived ``hydrogram.Client`` per bound Discord user.

Lazily connects on first use (``get``), keeps the connected client cached
for reuse (repeated ``get`` calls for the same user are cheap), and
disconnects on user revoke or app shutdown. ``client_factory`` is
injectable so tests substitute a stub instead of touching real MTProto.
"""

from __future__ import annotations

import asyncio
import contextlib
import typing as T

import hydrogram

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..persistence.tg_session_repo import TgSessionRepository

#: ``(name, session_string) -> hydrogram.Client``. Production default builds
#: a real in-memory-session hydrogram Client; tests inject a stub.
ClientFactory = T.Callable[..., 'hydrogram.Client']

_LOG_TAG = 'TG連線池'


class TgClientPool:
    """Per-user ``hydrogram.Client`` cache, keyed by Discord ``user_id``."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_repo: TgSessionRepository,
        *,
        logger: Logger | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_repo = session_repo
        self._logger = logger
        self._client_factory = client_factory or self._default_factory
        self._clients: dict[str, hydrogram.Client] = {}
        self._lock = asyncio.Lock()

    def _default_factory(self, *, name: str, session_string: str) -> hydrogram.Client:
        return hydrogram.Client(
            name,
            api_id=self._api_id,
            api_hash=self._api_hash,
            session_string=session_string,
            in_memory=True,
        )

    async def get(self, user_id: str) -> hydrogram.Client | None:
        """Return a connected client for *user_id*, connecting lazily on first use.

        Returns ``None`` if the user has no active ``tg_session`` row. If a
        stored session fails to reconnect (revoked elsewhere), the session is
        marked ``'expired'`` in the repo and ``None`` is returned.
        """
        async with self._lock:
            existing = self._clients.get(user_id)
            if existing is not None:
                return existing

            session_string = self._session_repo.get_decrypted_session_string(user_id)
            if session_string is None:
                return None

            client = self._client_factory(name=f'tg-{user_id}', session_string=session_string)
            try:
                await client.connect()
            except Exception as exc:  # noqa: BLE001 — reconnect failure, not our bug to raise
                self._log_error(f'user_id={user_id} 連線失敗，標記 session 為 expired: {exc}')
                self._session_repo.mark_expired(user_id)
                return None

            self._clients[user_id] = client
            self._session_repo.touch_last_active(user_id)
            return client

    async def disconnect(self, user_id: str) -> None:
        async with self._lock:
            client = self._clients.pop(user_id, None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def disconnect_all(self) -> None:
        async with self._lock:
            clients = list(self._clients.items())
            self._clients.clear()
        for _user_id, client in clients:
            with contextlib.suppress(Exception):
                await client.disconnect()

    def is_connected(self, user_id: str) -> bool:
        return user_id in self._clients

    def connected_user_ids(self) -> list[str]:
        return list(self._clients.keys())

    def _log_error(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(None, _LOG_TAG, message, display=False)

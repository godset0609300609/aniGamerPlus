"""Service around ``sn_list.txt`` — a thin wrapper over :class:`SnListRepository`."""

from __future__ import annotations

import functools
import typing as T

import anyio.to_thread

from ._factory import container_bound

if T.TYPE_CHECKING:
    from ..persistence.sn_list_repo import SnListRepository


class SnListService:
    def __init__(self, sn_list_repo: SnListRepository) -> None:
        self._repo = sn_list_repo

    async def read(self) -> str:
        return await anyio.to_thread.run_sync(self._repo.read_raw)

    async def write(self, content: str) -> None:
        await anyio.to_thread.run_sync(functools.partial(self._repo.write_raw, content))


get_snlist_service = container_bound(lambda c: SnListService(c.sn_list_repo))
"""FastAPI dependency resolver for :class:`SnListService`."""

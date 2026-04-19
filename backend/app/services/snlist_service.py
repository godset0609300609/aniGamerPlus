"""Service around ``sn_list.txt`` — a thin wrapper over :class:`SnListRepository`."""

from __future__ import annotations

import typing as T

from ._factory import container_bound

if T.TYPE_CHECKING:
    from ..persistence.sn_list_repo import SnListRepository


class SnListService:
    def __init__(self, sn_list_repo: SnListRepository) -> None:
        self._repo = sn_list_repo

    def read(self) -> str:
        return self._repo.read_raw()

    def write(self, content: str) -> None:
        self._repo.write_raw(content)


get_snlist_service = container_bound(lambda c: SnListService(c.sn_list_repo))
"""FastAPI dependency resolver for :class:`SnListService`."""

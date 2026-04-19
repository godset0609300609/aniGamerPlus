"""Shared factory helper for FastAPI dependency resolvers.

Every service module needs the same shape of "build-once-per-process,
lazily, from the container" factory. Rather than copy-paste six lru_cache
blocks, we centralise the pattern here.

Usage::

    from ._factory import container_bound

    get_x_service = container_bound(lambda c: XService(c.x_dep))

The returned callable is a zero-arg factory whose first call builds the
container, the second returns the cached service. FastAPI's
``Depends(get_x_service)`` works unchanged, and tests can still override
the dependency via ``app.dependency_overrides[get_x_service] = ...``.
"""

from __future__ import annotations

import collections.abc
import typing as T

if T.TYPE_CHECKING:
    from ..core import Container


def container_bound[R](
    build_fn: collections.abc.Callable[[Container], R],
) -> collections.abc.Callable[[], R]:
    """Return a zero-arg factory that lazily constructs ``build_fn(container)``.

    The container is created on first call via :func:`app.core.build_container`,
    reused thereafter. One cache slot per factory — the closure's ``cached``
    list is the storage.
    """
    cached: list[R] = []

    def factory() -> R:
        if not cached:
            # Local import breaks the app.core -> app.services import cycle.
            from ..core import build_container

            cached.append(build_fn(build_container()))
        return cached[0]

    return factory


__all__ = ['container_bound']

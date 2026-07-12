"""Bootstrap the dramatiq broker for the aniGamerPlus worker / API processes.

The first import wires a ``RedisBroker`` against ``ANIGAMERPLUS_REDIS_URL``,
adds the standard middleware stack plus :class:`dramatiq_abort.Abortable`
(Redis-backed) so :func:`TaskService.cancel_task` can interrupt running
downloads, and calls :func:`dramatiq.set_broker`.  Subsequent imports are
no-ops.

Reading the env var lazily means tests can monkeypatch
``ANIGAMERPLUS_REDIS_URL`` before importing modules that decorate actors.
"""

from __future__ import annotations

import os

import dramatiq
import dramatiq.brokers.redis
import dramatiq.middleware
import dramatiq_abort
import dramatiq_abort.backends

_DEFAULT_REDIS_URL = 'redis://127.0.0.1:6379/0'
_INITIALIZED = False

# Connect + op timeout (seconds) applied to broker Redis connections so an
# unreachable host (SYN dropped rather than refused — observed on Linux,
# unlike Windows' instant ECONNREFUSED) fails a command promptly instead of
# hanging forever.  Each broker round-trip (do_fetch/do_ack/etc.) is a single
# quick command, not a server-side blocking call, so this is safely below any
# legitimate operation latency.
_CONNECT_TIMEOUT_SECONDS = 2


def get_redis_url() -> str:
    return os.environ.get('ANIGAMERPLUS_REDIS_URL', _DEFAULT_REDIS_URL)


def _with_connect_timeout(url: str) -> str:
    """Append socket connect/op timeout query params to a Redis URL.

    ``RedisBroker(url=...)`` and ``RedisBackend.from_url(...)`` both build
    their connection pool via ``ConnectionPool.from_url(url)`` without
    forwarding extra keyword arguments, so the querystring is the only way
    to bound their connect timeout.
    """
    separator = '&' if '?' in url else '?'
    return (
        f'{url}{separator}socket_connect_timeout={_CONNECT_TIMEOUT_SECONDS}&socket_timeout={_CONNECT_TIMEOUT_SECONDS}'
    )


def init_broker() -> dramatiq.Broker:
    """Build and install the application's broker.  Idempotent."""
    global _INITIALIZED
    if _INITIALIZED:
        return dramatiq.get_broker()

    redis_url = _with_connect_timeout(get_redis_url())
    broker = dramatiq.brokers.redis.RedisBroker(url=redis_url)

    abortable = dramatiq_abort.Abortable(
        backend=dramatiq_abort.backends.RedisBackend.from_url(redis_url),
    )
    broker.add_middleware(abortable)
    broker.add_middleware(dramatiq.middleware.AsyncIO())

    dramatiq.set_broker(broker)
    _INITIALIZED = True
    return broker


def reset_for_tests() -> None:
    """Drop the broker so a stub broker can be installed in tests."""
    global _INITIALIZED
    _INITIALIZED = False

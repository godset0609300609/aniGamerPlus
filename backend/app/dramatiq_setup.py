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


def get_redis_url() -> str:
    return os.environ.get('ANIGAMERPLUS_REDIS_URL', _DEFAULT_REDIS_URL)


def init_broker() -> dramatiq.Broker:
    """Build and install the application's broker.  Idempotent."""
    global _INITIALIZED
    if _INITIALIZED:
        return dramatiq.get_broker()

    redis_url = get_redis_url()
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

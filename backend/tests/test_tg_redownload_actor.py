"""Tests for the tg_redownload_tick dramatiq actor."""

from __future__ import annotations

import inspect
import unittest.mock

import pytest


def _get_actor_coro(actor_obj: object) -> object:
    """Return the raw async def from a dramatiq actor."""
    fn = actor_obj.fn  # type: ignore[attr-defined]
    if hasattr(fn, '__wrapped__'):
        return fn.__wrapped__
    return fn


def test_actor_registered_on_broker() -> None:
    import dramatiq

    from app import tasks  # noqa: F401 — imports every actor module so it registers on the broker

    broker = dramatiq.get_broker()
    actor = broker.get_actor('tg_redownload_actor')
    assert actor.actor_name == 'tg_redownload_actor'


def test_actor_registered_with_correct_queue_and_time_limit() -> None:
    from app.tasks.tg_redownload_tick import tg_redownload_actor

    assert tg_redownload_actor.queue_name == 'downloads'
    assert tg_redownload_actor.options.get('max_retries', 0) == 0
    assert tg_redownload_actor.options.get('time_limit') == 60 * 60 * 1000


@pytest.mark.anyio
async def test_dispatches_to_redownload_service() -> None:
    from app.tasks.tg_redownload_tick import tg_redownload_actor

    coro_fn = _get_actor_coro(tg_redownload_actor)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_container = unittest.mock.MagicMock()
    fake_container.tg_redownload_service.run = unittest.mock.AsyncMock()

    with unittest.mock.patch('app.core.build_container', return_value=fake_container):
        await coro_fn('user-1', 42)

    fake_container.tg_redownload_service.run.assert_awaited_once_with(user_id='user-1', entry_id=42)


@pytest.mark.anyio
async def test_tg_redownload_service_none_is_noop() -> None:
    """TG_API_ID/TG_API_HASH not configured -> container.tg_redownload_service is None; must not raise."""
    from app.tasks.tg_redownload_tick import tg_redownload_actor

    coro_fn = _get_actor_coro(tg_redownload_actor)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_container = unittest.mock.MagicMock()
    fake_container.tg_redownload_service = None

    with unittest.mock.patch('app.core.build_container', return_value=fake_container):
        await coro_fn('user-1', 42)  # must not raise


@pytest.mark.anyio
async def test_service_error_propagates() -> None:
    """An unexpected failure inside the service must surface to dramatiq
    (so it logs/records the failed job), not be silently swallowed here."""
    from app.tasks.tg_redownload_tick import tg_redownload_actor

    coro_fn = _get_actor_coro(tg_redownload_actor)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_container = unittest.mock.MagicMock()
    fake_container.tg_redownload_service.run = unittest.mock.AsyncMock(side_effect=RuntimeError('boom'))

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        pytest.raises(RuntimeError, match='boom'),
    ):
        await coro_fn('user-1', 42)

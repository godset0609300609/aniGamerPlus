"""Tests for the tg_poll_tick dramatiq actor. Mirrors test_tg_backfill_actor.py's harness."""

from __future__ import annotations

import inspect
import os
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
    actor = broker.get_actor('tg_poll_tick')
    assert actor.actor_name == 'tg_poll_tick'


def test_actor_registered_with_correct_queue_and_time_limit() -> None:
    from app.tasks.tg_poll_tick import tg_poll_tick

    assert tg_poll_tick.queue_name == 'downloads'
    assert tg_poll_tick.options.get('max_retries', 0) == 0
    assert tg_poll_tick.options.get('time_limit') == 30 * 60 * 1000


@pytest.mark.anyio
async def test_dispatches_to_catchup_service_with_default_hours() -> None:
    from app.tasks.tg_poll_tick import tg_poll_tick

    coro_fn = _get_actor_coro(tg_poll_tick)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_container = unittest.mock.MagicMock()
    fake_container.tg_catchup_service.run_all = unittest.mock.AsyncMock()

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        unittest.mock.patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop('ANIGAMERPLUS_TG_CATCHUP_HOURS', None)
        await coro_fn()

    fake_container.tg_catchup_service.run_all.assert_awaited_once_with(24)


@pytest.mark.anyio
async def test_dispatches_with_env_configured_hours() -> None:
    from app.tasks.tg_poll_tick import tg_poll_tick

    coro_fn = _get_actor_coro(tg_poll_tick)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_container = unittest.mock.MagicMock()
    fake_container.tg_catchup_service.run_all = unittest.mock.AsyncMock()

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        unittest.mock.patch.dict(os.environ, {'ANIGAMERPLUS_TG_CATCHUP_HOURS': '48'}),
    ):
        await coro_fn()

    fake_container.tg_catchup_service.run_all.assert_awaited_once_with(48)


@pytest.mark.anyio
async def test_tg_catchup_service_none_is_noop() -> None:
    """TG_API_ID/TG_API_HASH not configured -> container.tg_catchup_service is None; must not raise."""
    from app.tasks.tg_poll_tick import tg_poll_tick

    coro_fn = _get_actor_coro(tg_poll_tick)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_container = unittest.mock.MagicMock()
    fake_container.tg_catchup_service = None

    with unittest.mock.patch('app.core.build_container', return_value=fake_container):
        await coro_fn()  # must not raise


def test_catchup_hours_env_var_invalid_falls_back_to_default() -> None:
    from app.tasks.tg_poll_tick import _catchup_hours

    with unittest.mock.patch.dict(os.environ, {'ANIGAMERPLUS_TG_CATCHUP_HOURS': 'not-a-number'}):
        assert _catchup_hours() == 24


def test_catchup_hours_env_var_non_positive_falls_back_to_default() -> None:
    from app.tasks.tg_poll_tick import _catchup_hours

    with unittest.mock.patch.dict(os.environ, {'ANIGAMERPLUS_TG_CATCHUP_HOURS': '0'}):
        assert _catchup_hours() == 24

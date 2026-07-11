"""Tests for the bt_remote_refresh_tick dramatiq actor."""

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
    actor = broker.get_actor('bt_remote_refresh_tick')
    assert actor.actor_name == 'bt_remote_refresh_tick'


def test_actor_registered_with_correct_queue() -> None:
    from app.tasks.bt_remote_refresh_tick import bt_remote_refresh_tick

    assert bt_remote_refresh_tick.queue_name == 'meta'
    assert bt_remote_refresh_tick.options.get('max_retries', 0) == 0


@pytest.mark.anyio
async def test_disabled_settings_skips_worker_call() -> None:
    from app.tasks.bt_remote_refresh_tick import bt_remote_refresh_tick

    coro_fn = _get_actor_coro(bt_remote_refresh_tick)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_settings_repo = unittest.mock.MagicMock()
    fake_settings_repo.load.return_value.bt_downloader.enabled = False
    fake_container = unittest.mock.MagicMock()
    fake_container.settings_repo = fake_settings_repo

    async def fake_to_thread(fn: object, *args: object, **kwargs: object) -> None:
        raise AssertionError('run_remote_refresh_iteration must not be called when bt_downloader is disabled')

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        unittest.mock.patch('asyncio.to_thread', side_effect=fake_to_thread),
    ):
        await coro_fn()

    fake_container.bt_landing_worker.run_remote_refresh_iteration.assert_not_called()


@pytest.mark.anyio
async def test_enabled_settings_calls_run_remote_refresh_iteration_via_to_thread() -> None:
    from app.tasks.bt_remote_refresh_tick import bt_remote_refresh_tick

    coro_fn = _get_actor_coro(bt_remote_refresh_tick)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_settings_repo = unittest.mock.MagicMock()
    fake_settings_repo.load.return_value.bt_downloader.enabled = True
    fake_container = unittest.mock.MagicMock()
    fake_container.settings_repo = fake_settings_repo

    calls: list[object] = []

    async def fake_to_thread(fn: object, *args: object, **kwargs: object) -> None:
        calls.append(fn)

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        unittest.mock.patch('asyncio.to_thread', side_effect=fake_to_thread),
    ):
        await coro_fn()

    assert calls == [fake_container.bt_landing_worker.run_remote_refresh_iteration]


@pytest.mark.anyio
async def test_run_remote_refresh_iteration_exception_propagates_but_does_not_crash_import() -> None:
    """A failure inside run_remote_refresh_iteration must surface to dramatiq's
    retry machinery (max_retries=0 means it's simply logged/dropped by the
    broker) rather than being silently swallowed here."""
    from app.tasks.bt_remote_refresh_tick import bt_remote_refresh_tick

    coro_fn = _get_actor_coro(bt_remote_refresh_tick)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_settings_repo = unittest.mock.MagicMock()
    fake_settings_repo.load.return_value.bt_downloader.enabled = True
    fake_container = unittest.mock.MagicMock()
    fake_container.settings_repo = fake_settings_repo

    async def fake_to_thread(fn: object, *args: object, **kwargs: object) -> None:
        raise RuntimeError('boom')

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        unittest.mock.patch('asyncio.to_thread', side_effect=fake_to_thread),
        pytest.raises(RuntimeError, match='boom'),
    ):
        await coro_fn()

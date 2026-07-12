"""Tests for the bt_retention_tick dramatiq actor."""

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
    actor = broker.get_actor('bt_retention_tick')
    assert actor.actor_name == 'bt_retention_tick'


def test_actor_registered_with_correct_queue() -> None:
    from app.tasks.bt_retention_tick import bt_retention_tick

    assert bt_retention_tick.queue_name == 'meta'
    assert bt_retention_tick.options.get('max_retries', 0) == 0


@pytest.mark.anyio
async def test_calls_prune_stale_via_to_thread_unconditionally() -> None:
    """Unlike bt_feed_tick / bt_landing_tick, retention must run even when
    bt_downloader is disabled — there is no settings.bt_downloader.enabled
    early-return guard."""
    from app.tasks.bt_retention_tick import bt_retention_tick

    coro_fn = _get_actor_coro(bt_retention_tick)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_settings_repo = unittest.mock.MagicMock()
    fake_settings_repo.load.return_value.bt_downloader.enabled = False
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

    assert calls == [fake_container.bt_retention_service.prune_stale]

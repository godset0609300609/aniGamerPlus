"""Tests for the run_bilibili_download dramatiq actor.

These tests call the actor's underlying coroutine directly via
``run_bilibili_download.__wrapped__`` (the original async def) to avoid
the dramatiq AsyncIO middleware event-loop requirement.
"""

from __future__ import annotations

import inspect
import unittest.mock

import pytest

from app.downloader.exceptions import TaskCancelledError


def _get_actor_coro(actor_obj: object) -> object:
    """Return the raw async def from a dramatiq actor."""
    fn = actor_obj.fn  # type: ignore[attr-defined]
    if hasattr(fn, '__wrapped__'):
        return fn.__wrapped__
    return fn


@pytest.mark.anyio
async def test_abort_raises_task_cancelled_error() -> None:
    import dramatiq_abort

    from app.tasks.bilibili_download import run_bilibili_download

    coro_fn = _get_actor_coro(run_bilibili_download)

    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_container = unittest.mock.MagicMock()
    fake_container.message_id_registry = None

    async def fake_to_thread(fn: object, *args: object, **kwargs: object) -> None:
        raise dramatiq_abort.Abort()

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        unittest.mock.patch('asyncio.to_thread', side_effect=fake_to_thread),
        unittest.mock.patch('dramatiq.middleware.CurrentMessage.get_current_message', return_value=None),
        pytest.raises(TaskCancelledError),
    ):
        await coro_fn(
            999,
            bvid='BV1xx411c7mD',
            resolution='1080',
            classify=True,
            owner_id=None,
        )


@pytest.mark.anyio
async def test_message_id_registry_set_and_cleared() -> None:
    from app.tasks.bilibili_download import run_bilibili_download

    coro_fn = _get_actor_coro(run_bilibili_download)

    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    registry = unittest.mock.AsyncMock()
    fake_container = unittest.mock.MagicMock()
    fake_container.message_id_registry = registry
    fake_container.bilibili_runner = unittest.mock.MagicMock()

    async def fake_to_thread(fn: object, *args: object, **kwargs: object) -> None:
        pass

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        unittest.mock.patch('dramatiq.middleware.CurrentMessage.get_current_message', return_value=None),
        unittest.mock.patch('asyncio.to_thread', side_effect=fake_to_thread),
    ):
        await coro_fn(
            1001,
            bvid='BV1xx411c7mD',
            resolution='1080',
            classify=True,
            owner_id=None,
        )

    registry.clear.assert_called_once_with(1001)


def test_actor_registered_with_correct_queue() -> None:
    from app.tasks.bilibili_download import run_bilibili_download

    assert run_bilibili_download.queue_name == 'downloads'
    assert run_bilibili_download.options.get('max_retries', 0) == 0


def test_actor_fn_is_callable() -> None:
    from app.tasks.bilibili_download import run_bilibili_download

    assert callable(run_bilibili_download.fn)


# ---------------------------------------------------------------------------
# fix #20: deferred b23.tv resolution via raw_input
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_raw_input_resolves_bvid_before_running() -> None:
    """When raw_input is given (bvid unknown at dispatch time), the actor
    resolves it here — in the worker — before calling bilibili_runner.run."""
    from app.tasks.bilibili_download import run_bilibili_download

    coro_fn = _get_actor_coro(run_bilibili_download)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_container = unittest.mock.MagicMock()
    fake_container.message_id_registry = None
    run_calls: list[dict] = []

    def _fake_run(task_sn: int, *, bvid: str, **kwargs: object) -> None:
        run_calls.append({'task_sn': task_sn, 'bvid': bvid})

    fake_container.bilibili_runner.run = _fake_run

    async def fake_to_thread(fn: object, *args: object, **kwargs: object) -> object:
        return fn(*args, **kwargs)  # type: ignore[operator]

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        unittest.mock.patch('dramatiq.middleware.CurrentMessage.get_current_message', return_value=None),
        unittest.mock.patch('asyncio.to_thread', side_effect=fake_to_thread),
        unittest.mock.patch(
            'app.downloader.bilibili.url_parser.parse_bilibili_input',
            return_value=('BV1zz433e9pF', 300003, False),
        ) as mock_parse,
    ):
        await coro_fn(
            2001,
            raw_input='https://b23.tv/shortlink',
            resolution='1080',
            classify=True,
            owner_id=None,
        )

    mock_parse.assert_called_once_with('https://b23.tv/shortlink')
    assert run_calls == [{'task_sn': 2001, 'bvid': 'BV1zz433e9pF'}]


@pytest.mark.anyio
async def test_raw_input_parse_failure_logs_and_skips_run() -> None:
    """A b23.tv link that fails to resolve (dead link, network error, ...)
    must log and return without ever calling bilibili_runner.run — it must
    not crash the actor."""
    from app.tasks.bilibili_download import run_bilibili_download

    coro_fn = _get_actor_coro(run_bilibili_download)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_container = unittest.mock.MagicMock()
    run_calls: list[dict] = []
    fake_container.bilibili_runner.run = lambda *a, **kw: run_calls.append({'a': a, 'kw': kw})  # noqa: ARG005

    async def fake_to_thread(fn: object, *args: object, **kwargs: object) -> object:
        return fn(*args, **kwargs)  # type: ignore[operator]

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        unittest.mock.patch(
            'app.downloader.bilibili.url_parser.parse_bilibili_input',
            side_effect=ValueError('cannot resolve b23.tv link'),
        ),
        unittest.mock.patch('asyncio.to_thread', side_effect=fake_to_thread),
    ):
        await coro_fn(
            2002,
            raw_input='https://b23.tv/dead-link',
            resolution='1080',
            classify=True,
            owner_id=None,
        )

    assert run_calls == [], 'bilibili_runner.run must not be called when resolution fails'
    fake_container.logger.error.assert_called_once()


@pytest.mark.anyio
async def test_bvid_given_without_raw_input_skips_resolution() -> None:
    """The common (non-b23) case: bvid was already resolved by the caller,
    so parse_bilibili_input must not be called at all."""
    from app.tasks.bilibili_download import run_bilibili_download

    coro_fn = _get_actor_coro(run_bilibili_download)
    if not inspect.iscoroutinefunction(coro_fn):
        pytest.skip('Cannot access raw async function; skipping')

    fake_container = unittest.mock.MagicMock()
    fake_container.message_id_registry = None
    run_calls: list[dict] = []
    fake_container.bilibili_runner.run = lambda task_sn, *, bvid, **kw: run_calls.append(  # noqa: ARG005
        {'task_sn': task_sn, 'bvid': bvid}
    )

    async def fake_to_thread(fn: object, *args: object, **kwargs: object) -> object:
        return fn(*args, **kwargs)  # type: ignore[operator]

    with (
        unittest.mock.patch('app.core.build_container', return_value=fake_container),
        unittest.mock.patch('dramatiq.middleware.CurrentMessage.get_current_message', return_value=None),
        unittest.mock.patch('asyncio.to_thread', side_effect=fake_to_thread),
        unittest.mock.patch('app.downloader.bilibili.url_parser.parse_bilibili_input') as mock_parse,
    ):
        await coro_fn(
            2003,
            bvid='BV1already411resolved',
            resolution='1080',
            classify=True,
            owner_id=None,
        )

    mock_parse.assert_not_called()
    assert run_calls == [{'task_sn': 2003, 'bvid': 'BV1already411resolved'}]

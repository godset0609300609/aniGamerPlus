"""Tests for ``NotificationBinder`` — asserts ``/start`` is sent via the
user's own Telegram session (not via the Bot API), and that every failure
mode returns a typed :class:`NotificationBindOutcome` instead of raising.
"""

from __future__ import annotations

import unittest.mock

import hydrogram.errors
import pytest

from app.tg_downloader.notification_binder import (
    NotificationBinder,
    NotificationBindOutcome,
    NotificationBindResult,
)


def _client_with_send_message() -> unittest.mock.AsyncMock:
    client = unittest.mock.AsyncMock()
    client.send_message = unittest.mock.AsyncMock()
    return client


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bind_sends_start_to_configured_bot(anyio_backend: str) -> None:
    client = _client_with_send_message()
    binder = NotificationBinder(lambda: 'aniGamerPlusBot')

    outcome = await binder.bind(client)

    assert outcome == NotificationBindOutcome(NotificationBindResult.SUCCESS)
    client.send_message.assert_awaited_once_with('@aniGamerPlusBot', '/start')


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bind_normalises_leading_at_sign(anyio_backend: str) -> None:
    client = _client_with_send_message()
    binder = NotificationBinder(lambda: '@aniGamerPlusBot')

    await binder.bind(client)

    client.send_message.assert_awaited_once_with('@aniGamerPlusBot', '/start')


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bind_trims_whitespace(anyio_backend: str) -> None:
    client = _client_with_send_message()
    binder = NotificationBinder(lambda: '  aniGamerPlusBot  ')

    outcome = await binder.bind(client)

    assert outcome.result is NotificationBindResult.SUCCESS
    client.send_message.assert_awaited_once_with('@aniGamerPlusBot', '/start')


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bind_returns_not_configured_when_bot_username_empty(anyio_backend: str) -> None:
    client = _client_with_send_message()
    binder = NotificationBinder(lambda: '')

    outcome = await binder.bind(client)

    assert outcome == NotificationBindOutcome(NotificationBindResult.BOT_USERNAME_NOT_CONFIGURED)
    client.send_message.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bind_returns_not_configured_when_bot_username_whitespace_only(anyio_backend: str) -> None:
    client = _client_with_send_message()
    binder = NotificationBinder(lambda: '   ')

    outcome = await binder.bind(client)

    assert outcome.result is NotificationBindResult.BOT_USERNAME_NOT_CONFIGURED
    client.send_message.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bind_returns_not_configured_when_bot_username_is_none(anyio_backend: str) -> None:
    client = _client_with_send_message()
    binder = NotificationBinder(lambda: None)

    outcome = await binder.bind(client)

    assert outcome.result is NotificationBindResult.BOT_USERNAME_NOT_CONFIGURED


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
@pytest.mark.parametrize('bad_username', ['ab', 'has space', 'toolongtoolongtoolongtoolongtoolong123'])
async def test_bind_returns_invalid_for_malformed_username(anyio_backend: str, bad_username: str) -> None:
    client = _client_with_send_message()
    binder = NotificationBinder(lambda: bad_username)

    outcome = await binder.bind(client)

    assert outcome.result is NotificationBindResult.BOT_USERNAME_INVALID
    client.send_message.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bind_flood_wait_returns_flood_wait_result(anyio_backend: str) -> None:
    client = _client_with_send_message()
    client.send_message.side_effect = hydrogram.errors.FloodWait(30)
    binder = NotificationBinder(lambda: 'aniGamerPlusBot')

    outcome = await binder.bind(client)  # must not raise

    assert outcome.result is NotificationBindResult.FLOOD_WAIT
    assert outcome.detail is not None and '30' in outcome.detail


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
@pytest.mark.parametrize(
    'exc',
    [
        hydrogram.errors.UsernameInvalid(),
        hydrogram.errors.UsernameNotOccupied(),
        hydrogram.errors.PeerIdInvalid(),
    ],
)
async def test_bind_bot_not_found_errors_return_bot_not_found_result(
    anyio_backend: str, exc: hydrogram.errors.RPCError
) -> None:
    client = _client_with_send_message()
    client.send_message.side_effect = exc
    binder = NotificationBinder(lambda: 'aniGamerPlusBot')

    outcome = await binder.bind(client)

    assert outcome.result is NotificationBindResult.BOT_NOT_FOUND


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bind_generic_rpc_error_returns_telegram_error_result(anyio_backend: str) -> None:
    client = _client_with_send_message()

    class _SomeOtherRpcError(hydrogram.errors.RPCError):
        def __init__(self) -> None:
            pass

        def __str__(self) -> str:
            return 'some other RPC failure'

    client.send_message.side_effect = _SomeOtherRpcError()
    binder = NotificationBinder(lambda: 'aniGamerPlusBot')

    outcome = await binder.bind(client)

    assert outcome.result is NotificationBindResult.TELEGRAM_ERROR
    assert outcome.detail == 'some other RPC failure'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bind_non_hydrogram_failure_is_swallowed_as_unknown_error(anyio_backend: str) -> None:
    """A non-hydrogram failure (network error, ...) must not propagate."""
    client = _client_with_send_message()
    client.send_message.side_effect = RuntimeError('boom')
    binder = NotificationBinder(lambda: 'aniGamerPlusBot')

    outcome = await binder.bind(client)  # must not raise

    assert outcome.result is NotificationBindResult.UNKNOWN_ERROR
    assert outcome.detail == 'boom'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_bind_logs_failure(anyio_backend: str) -> None:
    client = _client_with_send_message()
    client.send_message.side_effect = RuntimeError('boom')
    logged: list[str] = []

    class _FakeLogger:
        def error(self, sn: object, tag: str, detail: str = '', **kwargs: object) -> None:
            logged.append(detail)

    binder = NotificationBinder(lambda: 'aniGamerPlusBot', logger=_FakeLogger())  # type: ignore[arg-type]

    await binder.bind(client)

    assert any('boom' in line for line in logged)

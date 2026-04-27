"""Tests for :class:`TelegramClient` and :func:`escape_markdown_v2`."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.telegram_client import (
    TelegramApiError,
    TelegramBotBlockedError,
    TelegramChatNotFoundError,
    TelegramClient,
    escape_markdown_v2,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_response(result: object, status_code: int = 200) -> httpx.Response:
    """Build a fake successful Telegram API response."""
    body = json.dumps({'ok': True, 'result': result}).encode()
    return httpx.Response(status_code, content=body, headers={'content-type': 'application/json'})


def _err_response(
    description: str,
    error_code: int = 400,
    status_code: int = 400,
) -> httpx.Response:
    """Build a fake error Telegram API response."""
    body = json.dumps({'ok': False, 'error_code': error_code, 'description': description}).encode()
    return httpx.Response(status_code, content=body, headers={'content-type': 'application/json'})


def _make_client(transport: httpx.MockTransport) -> TelegramClient:
    client = TelegramClient.__new__(TelegramClient)
    client._base_url = 'https://api.telegram.org/botTEST_TOKEN'
    client._client = httpx.AsyncClient(transport=transport)
    return client


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_message_posts_correct_payload() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response({'message_id': 42, 'chat': {'id': 123456}})

    client = _make_client(httpx.MockTransport(handler))
    result = await client.send_message(123456, 'Hello *world*')
    await client.close()

    assert len(captured) == 1
    assert captured[0].url.path.endswith('/sendMessage')
    body = json.loads(captured[0].content)
    assert body['chat_id'] == 123456
    assert body['text'] == 'Hello *world*'
    assert body['parse_mode'] == 'MarkdownV2'
    assert body['disable_web_page_preview'] is True
    assert result['message_id'] == 42


@pytest.mark.anyio
async def test_send_message_without_parse_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert 'parse_mode' not in body
        return _ok_response({'message_id': 1})

    client = _make_client(httpx.MockTransport(handler))
    await client.send_message(1, 'plain text', parse_mode=None)
    await client.close()


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_error_403_blocked_raises_bot_blocked_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _err_response(
            description='Forbidden: bot was blocked by the user',
            error_code=403,
            status_code=403,
        )

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(TelegramBotBlockedError) as exc_info:
        await client.send_message(111, 'hi')
    await client.close()

    err = exc_info.value
    assert err.error_code == 403
    assert 'blocked' in err.description.lower()


@pytest.mark.anyio
async def test_error_400_chat_not_found_raises_chat_not_found_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _err_response(
            description='Bad Request: chat not found',
            error_code=400,
            status_code=400,
        )

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(TelegramChatNotFoundError) as exc_info:
        await client.send_message(999, 'hi')
    await client.close()

    err = exc_info.value
    assert err.error_code == 400
    assert 'chat not found' in err.description.lower()


@pytest.mark.anyio
async def test_generic_error_raises_telegram_api_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _err_response(description='some other error', error_code=500, status_code=500)

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(TelegramApiError) as exc_info:
        await client.send_message(1, 'hi')
    await client.close()

    # Should NOT be a subclass (just base TelegramApiError)
    assert type(exc_info.value) is TelegramApiError


# ---------------------------------------------------------------------------
# set_webhook
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_webhook_posts_correct_payload() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response(True)

    client = _make_client(httpx.MockTransport(handler))
    await client.set_webhook(
        'https://example.com/telegram/webhook',
        secret_token='mysecret',
        allowed_updates=['message', 'callback_query'],
    )
    await client.close()

    assert len(captured) == 1
    assert captured[0].url.path.endswith('/setWebhook')
    body = json.loads(captured[0].content)
    assert body['url'] == 'https://example.com/telegram/webhook'
    assert body['secret_token'] == 'mysecret'
    assert body['allowed_updates'] == ['message', 'callback_query']


# ---------------------------------------------------------------------------
# set_my_commands / delete_my_commands
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_my_commands_posts_correct_payload() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response(True)

    commands = [
        {'command': 'help', 'description': '說明'},
        {'command': 'status', 'description': '查看任務狀態'},
    ]
    client = _make_client(httpx.MockTransport(handler))
    await client.set_my_commands(commands)
    await client.close()

    assert len(captured) == 1
    assert captured[0].url.path.endswith('/setMyCommands')
    body = json.loads(captured[0].content)
    assert body == {'commands': commands}


@pytest.mark.anyio
async def test_delete_my_commands_calls_correct_method() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response(True)

    client = _make_client(httpx.MockTransport(handler))
    await client.delete_my_commands()
    await client.close()

    assert len(captured) == 1
    assert captured[0].url.path.endswith('/deleteMyCommands')


# ---------------------------------------------------------------------------
# get_webhook_info
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_webhook_info_returns_parsed_dict() -> None:
    webhook_info: dict[str, object] = {
        'url': 'https://example.com/telegram/webhook',
        'has_custom_certificate': False,
        'pending_update_count': 0,
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return _ok_response(webhook_info)

    client = _make_client(httpx.MockTransport(handler))
    result = await client.get_webhook_info()
    await client.close()

    assert result['url'] == 'https://example.com/telegram/webhook'
    assert result['pending_update_count'] == 0


# ---------------------------------------------------------------------------
# escape_markdown_v2
# ---------------------------------------------------------------------------


def test_escape_markdown_v2_all_special_chars() -> None:
    # The 18 special chars per Telegram MarkdownV2 spec
    special = r'_*[]()~`>#+-=|{}.!'
    escaped = escape_markdown_v2(special)
    for ch in special:
        assert f'\\{ch}' in escaped


def test_escape_markdown_v2_plain_text_unchanged() -> None:
    plain = 'Hello World 123'
    assert escape_markdown_v2(plain) == plain


def test_escape_markdown_v2_mixed() -> None:
    text = 'Download: 100%'
    result = escape_markdown_v2(text)
    # No special chars in 'Download: 100%' except nothing special — colon and % not in list
    assert result == text


def test_escape_markdown_v2_dot_escaped() -> None:
    assert escape_markdown_v2('v1.2.3') == r'v1\.2\.3'


def test_escape_markdown_v2_exclamation_escaped() -> None:
    assert escape_markdown_v2('Done!') == r'Done\!'

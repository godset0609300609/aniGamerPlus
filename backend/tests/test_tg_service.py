"""Tests for ``TgService.list_available_chats``.

Focused narrowly on this one method's two audit fixes — everything else
``TgService`` does is already covered end-to-end via ``test_tg_api.py``
(which drives it through the real FastAPI routes with its own client-pool
fake).

Blocker fix — ``ChannelForbidden`` AttributeError
    ``GET /api/tg/chats/available`` 500'd whenever the bound account had a
    dialog for a channel it was kicked from / restricted from: Telegram
    represents that as the raw ``ChannelForbidden`` type, and hydrogram's
    ``Chat._parse_channel_chat`` unconditionally reads attributes (e.g.
    ``channel.verified``) that only exist on the regular ``Channel`` type,
    raising ``AttributeError`` deep inside ``client.get_dialogs()`` —
    before any dialog in that batch is ever yielded to our code (Telegram's
    ``messages.getDialogs`` is paginated in batches of up to 100 and
    hydrogram builds every ``Dialog`` in a batch before yielding any of
    them). Once that happens, the async generator can't be resumed to skip
    past just the bad one — see ``list_available_chats``'s docstring.
    :class:`_RaisingDialogsClient` below reproduces that exact shape (an
    ``AttributeError`` raised mid-iteration, generator unusable afterward)
    to prove the method degrades to "whatever was fetched so far" instead
    of propagating the crash.

B-09/G-07 — unbounded fetch/response
    ``list_available_chats`` now caps the fetch at ``limit`` and reports
    ``truncated`` when more were available.
"""

from __future__ import annotations

import types
import typing as T

import pytest

from app.services.tg_service import TgService


class _RaisingDialogsClient:
    """Simulates hydrogram's real ``get_dialogs()`` failure mode: raises
    ``AttributeError`` partway through iteration and cannot be resumed
    afterward (calling ``__anext__`` again just raises ``StopAsyncIteration``,
    same as any exhausted/closed async generator)."""

    def __init__(self, dialogs: list[object], *, fail_after: int) -> None:
        self._dialogs = dialogs
        self._fail_after = fail_after

    async def get_dialogs(self, limit: int = 0):  # noqa: ANN201 — async generator, matches hydrogram's shape
        for i, dialog in enumerate(self._dialogs):
            if i == self._fail_after:
                raise AttributeError("'ChannelForbidden' object has no attribute 'verified'")
            yield dialog


class _FakeClientPool:
    def __init__(self, client: object | None) -> None:
        self._client = client

    async def get(self, user_id: str) -> object | None:  # noqa: ANN201
        return self._client


def _dialog(chat_id: int, title: str = 'chat') -> types.SimpleNamespace:
    chat = types.SimpleNamespace(
        id=chat_id, title=f'{title}-{chat_id}', first_name=None, type=types.SimpleNamespace(value='channel')
    )
    return types.SimpleNamespace(chat=chat)


def _make_service(client_pool: object) -> TgService:
    return TgService(
        T.cast('T.Any', None),  # session_repo — unused by list_available_chats
        T.cast('T.Any', None),  # watched_chat_repo
        T.cast('T.Any', None),  # downloaded_media_repo
        client_pool,  # type: ignore[arg-type]
        T.cast('T.Any', None),  # qr_login
        T.cast('T.Any', None),  # phone_login
        T.cast('T.Any', None),  # notification_binder
        T.cast('T.Any', None),  # watcher
    )


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_list_available_chats_skips_channel_forbidden(anyio_backend: str) -> None:
    dialogs = [_dialog(1), _dialog(2), _dialog(3)]
    # Two good dialogs yielded, then the third position raises — reproducing
    # a ChannelForbidden dialog landing mid-batch.
    client = _RaisingDialogsClient(dialogs, fail_after=2)
    service = _make_service(_FakeClientPool(client))

    result, truncated = await service.list_available_chats('user-1')

    assert truncated is False
    assert [d.chat.id for d in result] == [1, 2]  # never raised out to the caller


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_list_available_chats_channel_forbidden_as_first_dialog_returns_empty(
    anyio_backend: str,
) -> None:
    """Degenerate case: the very first dialog is the bad one — still no crash."""
    client = _RaisingDialogsClient([_dialog(1)], fail_after=0)
    service = _make_service(_FakeClientPool(client))

    result, truncated = await service.list_available_chats('user-1')

    assert result == []
    assert truncated is False


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_list_available_chats_no_client_returns_empty(anyio_backend: str) -> None:
    service = _make_service(_FakeClientPool(None))

    result, truncated = await service.list_available_chats('user-1')

    assert result == []
    assert truncated is False


# ---------------------------------------------------------------------------
# B-09/G-07 — size cap / truncation
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, dialogs: list[object]) -> None:
        self._dialogs = dialogs

    async def get_dialogs(self, limit: int = 0):  # noqa: ANN201
        for d in self._dialogs:
            yield d


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_list_available_chats_truncates_at_limit(anyio_backend: str) -> None:
    dialogs = [_dialog(i) for i in range(10)]
    service = _make_service(_FakeClientPool(_FakeClient(dialogs)))

    result, truncated = await service.list_available_chats('user-1', limit=5)

    assert truncated is True
    assert len(result) == 5


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_list_available_chats_under_limit_not_truncated(anyio_backend: str) -> None:
    dialogs = [_dialog(i) for i in range(3)]
    service = _make_service(_FakeClientPool(_FakeClient(dialogs)))

    result, truncated = await service.list_available_chats('user-1', limit=5)

    assert truncated is False
    assert len(result) == 3

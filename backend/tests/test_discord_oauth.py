"""Tests for :class:`DiscordOAuthClient`."""

from __future__ import annotations

import pytest

from app.auth.discord_oauth import DiscordOAuthClient
from app.models import DiscordAuthSettings


def _make_settings(**kwargs: object) -> DiscordAuthSettings:
    return DiscordAuthSettings(
        enabled=True,
        client_id='CLIENT_ID',
        client_secret='CLIENT_SECRET',
        redirect_uri='http://localhost:8000/api/auth/callback',
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# build_authorize_url
# ---------------------------------------------------------------------------


def test_build_authorize_url_contains_client_id() -> None:
    from unittest.mock import AsyncMock

    settings = _make_settings()
    client = DiscordOAuthClient(settings, AsyncMock())
    url = client.build_authorize_url('my_state')
    assert 'CLIENT_ID' in url


def test_build_authorize_url_contains_state() -> None:
    from unittest.mock import AsyncMock

    settings = _make_settings()
    client = DiscordOAuthClient(settings, AsyncMock())
    url = client.build_authorize_url('secret_state_xyz')
    assert 'secret_state_xyz' in url


def test_build_authorize_url_scope_identify() -> None:
    from unittest.mock import AsyncMock

    settings = _make_settings()
    client = DiscordOAuthClient(settings, AsyncMock())
    url = client.build_authorize_url('st')
    assert 'scope=identify' in url or 'scope%3Didentify' in url or 'identify' in url


def test_build_authorize_url_contains_redirect_uri() -> None:
    from unittest.mock import AsyncMock

    settings = _make_settings()
    client = DiscordOAuthClient(settings, AsyncMock())
    url = client.build_authorize_url('st')
    assert 'redirect_uri' in url


# ---------------------------------------------------------------------------
# exchange_code
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_exchange_code_posts_to_discord_token_url() -> None:
    """exchange_code should POST to the Discord token endpoint and return
    the parsed JSON body."""
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        'access_token': 'tok123',
        'token_type': 'Bearer',
        'expires_in': 604800,
        'scope': 'identify',
    }

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(return_value=fake_response)

    settings = _make_settings()
    client = DiscordOAuthClient(settings, mock_http)

    result = await client.exchange_code('auth_code_abc', 'state_xyz')

    assert result['access_token'] == 'tok123'
    mock_http.post.assert_called_once()
    call_kwargs = mock_http.post.call_args
    # First positional arg is the URL.
    assert 'token' in call_kwargs.args[0]


@pytest.mark.anyio
async def test_exchange_code_raises_on_http_error() -> None:
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        'bad request',
        request=MagicMock(),
        response=MagicMock(status_code=400),
    )

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(return_value=fake_response)

    settings = _make_settings()
    client = DiscordOAuthClient(settings, mock_http)

    with pytest.raises(httpx.HTTPStatusError):
        await client.exchange_code('bad_code', 'state')


# ---------------------------------------------------------------------------
# fetch_user_info
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_user_info_returns_avatar_url() -> None:
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        'id': '111222333',
        'username': 'alice',
        'avatar': 'abc123hash',
    }

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get = AsyncMock(return_value=fake_response)

    settings = _make_settings()
    client = DiscordOAuthClient(settings, mock_http)

    info = await client.fetch_user_info('tok_abc')

    assert info['username'] == 'alice'
    assert info['avatar_url'] == ('https://cdn.discordapp.com/avatars/111222333/abc123hash.png')


@pytest.mark.anyio
async def test_fetch_user_info_none_avatar_when_no_hash() -> None:
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        'id': '999',
        'username': 'bob',
        'avatar': None,
    }

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get = AsyncMock(return_value=fake_response)

    settings = _make_settings()
    client = DiscordOAuthClient(settings, mock_http)

    info = await client.fetch_user_info('tok_xyz')

    assert info['avatar_url'] is None


@pytest.mark.anyio
async def test_fetch_user_info_calls_bearer_auth() -> None:
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {'id': '1', 'username': 'u', 'avatar': None}

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get = AsyncMock(return_value=fake_response)

    settings = _make_settings()
    client = DiscordOAuthClient(settings, mock_http)

    await client.fetch_user_info('MY_ACCESS_TOKEN')

    mock_http.get.assert_called_once()
    call_kwargs = mock_http.get.call_args
    headers = call_kwargs.kwargs.get('headers', {})
    assert 'Bearer MY_ACCESS_TOKEN' in headers.get('Authorization', '')

"""Discord OAuth2 client.

Wraps the three-legged OAuth2 flow for Discord (authorization-code grant)
using ``httpx`` for async HTTP.  We do NOT use authlib's high-level
OAuth2Client integration so we can stay fully namespace-style (memory rule)
and avoid the starlette-session-based flow that authlib assumes.
"""

from __future__ import annotations

import typing as T

import httpx

if T.TYPE_CHECKING:
    from ..models import DiscordAuthSettings

_DISCORD_API_BASE = 'https://discord.com/api/v10'
_DISCORD_AUTHORIZE_URL = 'https://discord.com/oauth2/authorize'
_DISCORD_TOKEN_URL = f'{_DISCORD_API_BASE}/oauth2/token'
_DISCORD_USER_URL = f'{_DISCORD_API_BASE}/users/@me'


class DiscordOAuthClient:
    """Stateless helper for the Discord OAuth2 authorization-code flow.

    ``http_client`` must be an ``httpx.AsyncClient``-compatible object; it
    is injected so tests can substitute a fake without monkey-patching the
    module.
    """

    def __init__(
        self,
        settings: DiscordAuthSettings,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._http = http_client

    # ------------------------------------------------------------------
    # Step 1 — redirect the browser to Discord

    def build_authorize_url(self, state: str) -> str:
        """Return the Discord authorize URL the browser should be sent to.

        Scope is ``identify`` only — we just need the user's ID + username.
        """
        import urllib.parse

        params = {
            'client_id': self._settings.client_id,
            'redirect_uri': self._settings.redirect_uri,
            'response_type': 'code',
            'scope': 'identify',
            'state': state,
        }
        return f'{_DISCORD_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}'

    # ------------------------------------------------------------------
    # Step 2 — exchange the code for a token

    async def exchange_code(self, code: str, state: str) -> dict[str, object]:  # noqa: ARG002
        """Exchange an authorization ``code`` for a Discord access token.

        ``state`` is accepted for API symmetry (caller already validated it
        against the session before calling us).

        Returns the raw token payload from Discord::

            {
                "access_token": "...",
                "token_type": "Bearer",
                "expires_in": 604800,
                "refresh_token": "...",
                "scope": "identify",
            }
        """
        response = await self._http.post(
            _DISCORD_TOKEN_URL,
            data={
                'client_id': self._settings.client_id,
                'client_secret': self._settings.client_secret,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': self._settings.redirect_uri,
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        response.raise_for_status()
        result: dict[str, object] = response.json()
        return result

    # ------------------------------------------------------------------
    # Step 3 — fetch the authenticated user's profile

    async def fetch_user_info(self, access_token: str) -> dict[str, object]:
        """Call Discord ``/users/@me`` and return the raw user payload.

        The returned dict always has ``id`` and ``username``.  ``avatar``
        is a hex hash string when the user has a custom avatar, or ``None``
        when they use a default Discord avatar.

        We add a computed ``avatar_url`` key so callers don't need to know
        the CDN URL pattern::

            {
                "id": "123456789",
                "username": "alice",
                "avatar": "abc123",
                "avatar_url": "https://cdn.discordapp.com/avatars/123456789/abc123.png",
                ...
            }
        """
        response = await self._http.get(
            _DISCORD_USER_URL,
            headers={'Authorization': f'Bearer {access_token}'},
        )
        response.raise_for_status()
        data: dict[str, object] = response.json()

        # Materialise a ready-to-store avatar URL (or None).
        user_id = data.get('id', '')
        avatar_hash = data.get('avatar')
        if avatar_hash:
            data['avatar_url'] = f'https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png'
        else:
            data['avatar_url'] = None

        return data

"""Basic Auth service driven by ``settings.dashboard``."""

from __future__ import annotations

import base64
import secrets
import typing as T

import fastapi
import fastapi.security

from ._factory import container_bound

if T.TYPE_CHECKING:
    from ..models import DashboardSettings
    from ..persistence.settings_repo import SettingsRepository

_security = fastapi.security.HTTPBasic(auto_error=False)


class AuthService:
    """Performs Basic Auth checks for HTTP and WebSocket requests.

    Reads ``dashboard.BasicAuth`` / ``dashboard.username`` /
    ``dashboard.password`` from the current :class:`AppSettings` every
    call so runtime config changes take effect without restart.
    """

    def __init__(self, settings_repo: SettingsRepository) -> None:
        self._repo = settings_repo

    # -- settings ---------------------------------------------------------

    def _dashboard(self) -> DashboardSettings:
        return self._repo.load().dashboard

    def is_enabled(self) -> bool:
        return bool(self._dashboard().BasicAuth)

    # -- HTTP -------------------------------------------------------------

    def verify_http(self, credentials: fastapi.security.HTTPBasicCredentials | None) -> str:
        dashboard = self._dashboard()
        if not dashboard.BasicAuth:
            return 'anonymous'
        if credentials is None:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
                detail='Authentication required',
                headers={'WWW-Authenticate': 'Basic'},
            )
        if not self._credentials_match(credentials.username, credentials.password, dashboard):
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
                detail='Invalid credentials',
                headers={'WWW-Authenticate': 'Basic'},
            )
        return credentials.username

    # -- WebSocket --------------------------------------------------------

    def verify_ws(self, authorization: str | None) -> bool:
        dashboard = self._dashboard()
        if not dashboard.BasicAuth:
            return True
        if not authorization or not authorization.lower().startswith('basic '):
            return False
        try:
            decoded = base64.b64decode(authorization.split(' ', 1)[1]).decode('utf-8')
        except Exception:
            return False
        if ':' not in decoded:
            return False
        user, _, pw = decoded.partition(':')
        return self._credentials_match(user, pw, dashboard)

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _credentials_match(user: str, password: str, dashboard: DashboardSettings) -> bool:
        expected_user = (dashboard.username or '').encode('utf-8')
        expected_pw = (dashboard.password or '').encode('utf-8')
        return secrets.compare_digest(user.encode('utf-8'), expected_user) and secrets.compare_digest(
            password.encode('utf-8'), expected_pw
        )


get_auth_service = container_bound(lambda c: AuthService(c.settings_repo))
"""FastAPI dependency resolver for :class:`AuthService`."""


def require_auth(
    credentials: T.Annotated[fastapi.security.HTTPBasicCredentials | None, fastapi.Depends(_security)],
    service: T.Annotated[AuthService, fastapi.Depends(get_auth_service)],
) -> str:
    """Dependency that enforces Basic Auth when the dashboard enables it."""
    return service.verify_http(credentials)

"""Shared slowapi rate limiter for public / abuse-prone endpoints (fix #17).

Kept in its own module (not ``app.main``) so route modules under
``app/api/`` can import :data:`limiter` for the ``@limiter.limit(...)``
decorator without a circular import — ``app.main`` already imports every
router module at startup.

Rate-limit strings are exposed as zero-arg callables (not plain strings) so
they can be passed directly as slowapi's ``limit_value``: slowapi
re-invokes a callable ``limit_value`` on every request, so the current env
var value is honoured live, without a process restart — the same pattern
:class:`~app.services.telegram_rate_limiter.TelegramRateLimiter` uses for
``max_provider``.
"""

from __future__ import annotations

import os

import fastapi
import slowapi
import slowapi.errors
import slowapi.util

#: Applies to /api/auth/login, /callback, /telegram-webapp — per IP.
AUTH_RATE_LIMIT_ENV_VAR = 'ANIGAMERPLUS_RATE_LIMIT_AUTH'
#: Applies to POST /api/tasks/manual — per authenticated user (falls back to IP).
TASKS_MANUAL_RATE_LIMIT_ENV_VAR = 'ANIGAMERPLUS_RATE_LIMIT_TASKS_MANUAL'
#: Applies to POST /api/bt/feeds/probe — per authenticated (admin) user.
BT_PROBE_RATE_LIMIT_ENV_VAR = 'ANIGAMERPLUS_RATE_LIMIT_BT_PROBE'
#: Applies to POST /api/bt/entries/{id}/dispatch — per authenticated (admin) user.
BT_DISPATCH_RATE_LIMIT_ENV_VAR = 'ANIGAMERPLUS_RATE_LIMIT_BT_DISPATCH'
#: Applies to the Telegram User API login endpoints (QR start / phone send-code) — per user.
TG_LOGIN_RATE_LIMIT_ENV_VAR = 'ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN'
#: Applies to GET /api/tg/chats/available — a live MTProto query, per user.
TG_CHATS_AVAILABLE_RATE_LIMIT_ENV_VAR = 'ANIGAMERPLUS_RATE_LIMIT_TG_CHATS_AVAILABLE'
#: Applies to GET /api/tg/session/qr-login/{login_token} — the frontend's QR poll loop, per user.
TG_LOGIN_POLL_RATE_LIMIT_ENV_VAR = 'ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN_POLL'
#: Applies to the QR/phone 2FA-password and phone-code submit endpoints — per user.
TG_LOGIN_SUBMIT_RATE_LIMIT_ENV_VAR = 'ANIGAMERPLUS_RATE_LIMIT_TG_LOGIN_SUBMIT'

_DEFAULT_AUTH_RATE_LIMIT = '5/minute'
_DEFAULT_TASKS_MANUAL_RATE_LIMIT = '30/minute'
_DEFAULT_BT_PROBE_RATE_LIMIT = '10/minute'
_DEFAULT_BT_DISPATCH_RATE_LIMIT = '10/minute'
_DEFAULT_TG_LOGIN_RATE_LIMIT = '5/minute'
_DEFAULT_TG_CHATS_AVAILABLE_RATE_LIMIT = '10/minute'
# Matches the frontend's 2s QR-poll interval — 30/minute is "every 2 seconds"
# with a little headroom for retries/jitter, while still blocking abuse.
_DEFAULT_TG_LOGIN_POLL_RATE_LIMIT = '30/minute'
# Tighter than tg_login_rate_limit (5/min) — each attempt here is a code/
# password guess (2FA password, phone code). Telegram enforces its own
# throttling server-side too; this is defense-in-depth on top of that.
_DEFAULT_TG_LOGIN_SUBMIT_RATE_LIMIT = '10/minute'


def auth_rate_limit() -> str:
    """Limit string for the Discord/Telegram login endpoints — per IP."""
    return os.environ.get(AUTH_RATE_LIMIT_ENV_VAR, _DEFAULT_AUTH_RATE_LIMIT)


def tasks_manual_rate_limit() -> str:
    """Limit string for ``POST /api/tasks/manual`` — per authenticated user."""
    return os.environ.get(TASKS_MANUAL_RATE_LIMIT_ENV_VAR, _DEFAULT_TASKS_MANUAL_RATE_LIMIT)


def bt_probe_rate_limit() -> str:
    """Limit string for ``POST /api/bt/feeds/probe`` — per admin user."""
    return os.environ.get(BT_PROBE_RATE_LIMIT_ENV_VAR, _DEFAULT_BT_PROBE_RATE_LIMIT)


def bt_dispatch_rate_limit() -> str:
    """Limit string for ``POST /api/bt/entries/{id}/dispatch`` — per admin user."""
    return os.environ.get(BT_DISPATCH_RATE_LIMIT_ENV_VAR, _DEFAULT_BT_DISPATCH_RATE_LIMIT)


def tg_login_rate_limit() -> str:
    """Limit string for the Telegram User API login-start endpoints — per user."""
    return os.environ.get(TG_LOGIN_RATE_LIMIT_ENV_VAR, _DEFAULT_TG_LOGIN_RATE_LIMIT)


def tg_chats_available_rate_limit() -> str:
    """Limit string for ``GET /api/tg/chats/available`` — per user."""
    return os.environ.get(TG_CHATS_AVAILABLE_RATE_LIMIT_ENV_VAR, _DEFAULT_TG_CHATS_AVAILABLE_RATE_LIMIT)


def tg_login_poll_rate_limit() -> str:
    """Limit string for ``GET /api/tg/session/qr-login/{login_token}`` — per user."""
    return os.environ.get(TG_LOGIN_POLL_RATE_LIMIT_ENV_VAR, _DEFAULT_TG_LOGIN_POLL_RATE_LIMIT)


def tg_login_submit_rate_limit() -> str:
    """Limit string for the QR/phone 2FA-password and phone-code submit endpoints — per user."""
    return os.environ.get(TG_LOGIN_SUBMIT_RATE_LIMIT_ENV_VAR, _DEFAULT_TG_LOGIN_SUBMIT_RATE_LIMIT)


def session_or_ip_key(request: fastapi.Request) -> str:
    """Rate-limit key: the session's ``user_id`` when present, else the remote IP.

    Used for endpoints that sit behind auth (tasks/manual, bt probe) so a
    single abusive account can't dodge the cap by rotating source IPs
    behind a shared NAT. Falls back to IP for single-user mode (auth
    disabled — no ``user_id`` is ever written to the session).
    """
    user_id = request.session.get('user_id')
    if user_id:
        return str(user_id)
    return slowapi.util.get_remote_address(request)


#: Process-wide slowapi limiter. ``default_limits=[]`` — every route is
#: opt-in via an explicit ``@limiter.limit(...)`` decorator rather than a
#: blanket limit applied to the whole app.
limiter = slowapi.Limiter(key_func=slowapi.util.get_remote_address, default_limits=[])


def install(app: fastapi.FastAPI) -> None:
    """Wire the shared limiter + its 429 exception handler into *app*.

    Every ``@limiter.limit(...)``-decorated route depends on
    ``app.state.limiter`` being set (slowapi's default exceeded-handler
    reads it to build the rate-limit response headers) and on
    ``RateLimitExceeded`` being mapped to a 429 JSON response instead of
    propagating as an unhandled exception (FastAPI's default 500).
    """
    app.state.limiter = limiter
    app.add_exception_handler(slowapi.errors.RateLimitExceeded, slowapi._rate_limit_exceeded_handler)  # type: ignore[arg-type]

"""Sanitizes exception text before it hits the log file.

D-6 (security audit): hydrogram/MTProto exceptions occasionally interpolate
raw request/response payloads into their ``str()`` — which can include a
session string, an auth key, or other token-shaped data. That text must
never appear in an API response (see ``app.tg_downloader._login_common``'s
``_sanitize_login_error`` for the response-side half of that guarantee),
but it was still being written verbatim to the log file at every
``logger.warning``/``logger.error(..., f'...{exc}')`` call site across
``app.tg_downloader``. :func:`scrub_exception_for_log` gives those call
sites a single, shared place to redact token-shaped runs and cap the
overall length so one pathological exception can't balloon a log line.
"""

from __future__ import annotations

import re

#: Log lines longer than this (after redaction) are truncated with a
#: trailing ellipsis — bounds how much space one exception can consume.
_MAX_LOG_EXCEPTION_LENGTH = 200

#: Matches base64/base64url-alphabet runs of 40+ characters — long enough to
#: catch session strings / API tokens / auth-key material accidentally
#: embedded in an exception's message, short enough to leave ordinary
#: (non-secret-shaped) error text untouched.
_TOKEN_LIKE_RE = re.compile(r'[A-Za-z0-9+/_-]{40,}')


def scrub_exception_for_log(exc: BaseException | str) -> str:
    """Return a log-safe rendering of *exc*: token-redacted and length-capped."""
    text = str(exc)
    text = _TOKEN_LIKE_RE.sub('[REDACTED]', text)
    if len(text) > _MAX_LOG_EXCEPTION_LENGTH:
        text = text[:_MAX_LOG_EXCEPTION_LENGTH] + '...'
    return text


__all__ = ['scrub_exception_for_log']

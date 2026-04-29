"""Verify Telegram Mini App ``initData`` against the bot token via HMAC-SHA256.

Pure function — no I/O.  Caller is responsible for looking up the user
by telegram_chat_id once verification passes.

Algorithm (Telegram spec):
1. Parse the urlencoded init_data into key=value pairs.
2. Build data_check_string = "\n".join(sorted "key=value" entries excluding "hash").
3. secret_key = HMAC-SHA256(b"WebAppData", bot_token).
4. expected = HMAC-SHA256(secret_key, data_check_string).hexdigest().
5. constant-time compare with parsed["hash"].

Plus: reject if ``auth_date`` is older than ``max_age_seconds`` (default 24h)
to prevent replay.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import typing as T
import urllib.parse


_DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60


class InitDataVerificationError(Exception):
    """Raised when init_data signature/auth_date check fails."""


def verify_telegram_webapp_initdata(
    init_data: str,
    bot_token: str,
    *,
    now_unix: float | None = None,
    max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, T.Any]:
    """Verify init_data and return the parsed payload.

    Returns a dict whose ``user`` field is the parsed user JSON (Telegram
    sends the user as a JSON-encoded string inside the urlencoded form).
    On any failure raises :class:`InitDataVerificationError` with a short
    reason — do NOT leak details to the client; let FastAPI return a 401.
    """
    if not bot_token:
        raise InitDataVerificationError('bot_token is empty')

    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = parsed.pop('hash', None)
    if not received_hash:
        raise InitDataVerificationError('hash missing')

    auth_date_raw = parsed.get('auth_date')
    if not auth_date_raw:
        raise InitDataVerificationError('auth_date missing')
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise InitDataVerificationError('auth_date malformed') from exc

    current = time.time() if now_unix is None else float(now_unix)
    if current - auth_date > max_age_seconds:
        raise InitDataVerificationError('auth_date too old')
    if auth_date - current > 60:  # tolerate 60s clock skew
        raise InitDataVerificationError('auth_date in the future')

    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b'WebAppData', bot_token.encode('utf-8'), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode('utf-8'), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise InitDataVerificationError('hash mismatch')

    # Parse user JSON if present
    if 'user' in parsed:
        try:
            parsed['user'] = json.loads(parsed['user'])
        except json.JSONDecodeError as exc:
            raise InitDataVerificationError('user JSON malformed') from exc
    return parsed

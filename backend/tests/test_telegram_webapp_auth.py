"""Tests for the initData HMAC verifier."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

import pytest

from app.services.telegram_webapp_auth import (
    InitDataVerificationError,
    verify_telegram_webapp_initdata,
)

BOT_TOKEN = '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'


def _make_initdata(*, bot_token: str, auth_date: int, user_id: int = 999) -> str:
    user_json = json.dumps({'id': user_id, 'first_name': 'Test'}, separators=(',', ':'))
    fields = {
        'auth_date': str(auth_date),
        'query_id': 'AAH...',
        'user': user_json,
    }
    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret_key = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    fields['hash'] = h
    return urllib.parse.urlencode(fields)


def test_valid_initdata_returns_parsed_user() -> None:
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now)
    result = verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)
    assert result['user']['id'] == 999


def test_tampered_field_fails_hash_check() -> None:
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now)
    # tamper with auth_date — hash will not match
    tampered = init_data.replace(f'auth_date={now}', f'auth_date={now - 1000}')
    with pytest.raises(InitDataVerificationError):
        verify_telegram_webapp_initdata(tampered, BOT_TOKEN, now_unix=now)


def test_old_auth_date_rejected() -> None:
    now = int(time.time())
    old = now - 25 * 3600  # > 24h
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=old)
    with pytest.raises(InitDataVerificationError, match='too old'):
        verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)


def test_future_auth_date_rejected() -> None:
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now + 120)
    with pytest.raises(InitDataVerificationError, match='future'):
        verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)


def test_missing_hash_rejected() -> None:
    with pytest.raises(InitDataVerificationError, match='hash missing'):
        verify_telegram_webapp_initdata('auth_date=1', BOT_TOKEN, now_unix=2)


def test_empty_bot_token_rejected() -> None:
    with pytest.raises(InitDataVerificationError, match='bot_token is empty'):
        verify_telegram_webapp_initdata('auth_date=1', '', now_unix=2)


def test_wrong_token_fails() -> None:
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now)
    with pytest.raises(InitDataVerificationError, match='hash mismatch'):
        verify_telegram_webapp_initdata(init_data, 'wrong-token', now_unix=now)


def test_default_max_age_one_second_inside_window_accepted() -> None:
    """auth_date 86399s old (1s before 24h cutoff) should pass.
    Kills mutations that turn 24*60*60 into a smaller number."""
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now - 86399)
    result = verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)
    assert result['user']['id'] == 999


def test_default_max_age_at_exact_boundary_accepted() -> None:
    """auth_date exactly 86400s old should still pass (the comparison is strict >).
    Kills `> max_age_seconds` -> `>= max_age_seconds` mutation."""
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now - 86400)
    result = verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)
    assert result['user']['id'] == 999


def test_default_max_age_one_second_over_rejected() -> None:
    """auth_date 86401s old (1s past 24h) is rejected.
    Kills mutations that turn 24*60*60 into a larger number."""
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now - 86401)
    with pytest.raises(InitDataVerificationError, match='too old'):
        verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)


def test_custom_max_age_120_seconds() -> None:
    """auth_date 119s old with max_age=120 accepted; 121s rejected."""
    now = int(time.time())
    init_data_ok = _make_initdata(bot_token=BOT_TOKEN, auth_date=now - 119)
    result = verify_telegram_webapp_initdata(init_data_ok, BOT_TOKEN, now_unix=now, max_age_seconds=120)
    assert result['user']['id'] == 999

    init_data_old = _make_initdata(bot_token=BOT_TOKEN, auth_date=now - 121)
    with pytest.raises(InitDataVerificationError, match='too old'):
        verify_telegram_webapp_initdata(init_data_old, BOT_TOKEN, now_unix=now, max_age_seconds=120)


def test_clock_skew_59_seconds_future_accepted() -> None:
    """auth_date 59s in the future accepted (within 60s skew tolerance).
    Kills mutations that turn 60 into a smaller number."""
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now + 59)
    result = verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)
    assert result['user']['id'] == 999


def test_clock_skew_60_seconds_future_accepted() -> None:
    """auth_date exactly 60s in the future is accepted (the comparison is strict >).
    Kills mutations on the 60-skew comparison operator."""
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now + 60)
    result = verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)
    assert result['user']['id'] == 999


def test_clock_skew_61_seconds_future_rejected() -> None:
    """auth_date 61s in the future rejected.
    Kills mutations that turn 60 into a larger number."""
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now + 61)
    with pytest.raises(InitDataVerificationError, match='future'):
        verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)


def test_now_unix_none_uses_real_time(monkeypatch) -> None:
    """When now_unix is None, time.time() is used.
    Kills mutations on `is None` and `time.time()` calls."""
    fixed_time = 1_700_000_000.0
    import app.services.telegram_webapp_auth as auth_mod

    monkeypatch.setattr(auth_mod.time, 'time', lambda: fixed_time)
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=int(fixed_time))
    result = verify_telegram_webapp_initdata(init_data, BOT_TOKEN)
    assert result['user']['id'] == 999


def test_explicit_now_unix_takes_precedence_over_real_time(monkeypatch) -> None:
    """When now_unix is provided, time.time() is not called.
    Kills mutations that drop the conditional."""
    import app.services.telegram_webapp_auth as auth_mod

    real_time_called = []

    def fake_real_time() -> float:
        real_time_called.append(1)
        return 0.0

    monkeypatch.setattr(auth_mod.time, 'time', fake_real_time)
    now = 1_700_000_000.0
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=int(now))
    result = verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)
    assert result['user']['id'] == 999
    assert real_time_called == []  # time.time() must NOT have been called


def test_auth_date_value_zero_rejected() -> None:
    """auth_date='0' — '0' is a non-empty string so `if not auth_date_raw:` passes.
    Parse to int 0, which is way before now, so rejected as 'too old'."""
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=0)
    with pytest.raises(InitDataVerificationError, match='too old'):
        verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)


def test_user_field_is_parsed_to_dict() -> None:
    """The 'user' string in initData is JSON-decoded — confirm dict, not string.
    Kills mutations that drop the json.loads call."""
    now = int(time.time())
    init_data = _make_initdata(bot_token=BOT_TOKEN, auth_date=now)
    result = verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)
    assert isinstance(result['user'], dict)
    assert result['user']['id'] == 999
    assert result['user']['first_name'] == 'Test'


def test_user_json_malformed_rejected() -> None:
    """Bad JSON in user field -> InitDataVerificationError with 'user JSON malformed'."""
    now = int(time.time())
    fields = {
        'auth_date': str(now),
        'query_id': 'AAH...',
        'user': 'not-valid-json',
    }
    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret_key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    fields['hash'] = h
    init_data = urllib.parse.urlencode(fields)
    with pytest.raises(InitDataVerificationError, match='malformed'):
        verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)


def test_no_user_field_returns_parsed_without_user_key() -> None:
    """initData without 'user' field is still verified; result has no 'user' key.
    Kills mutations that turn `if 'user' in parsed:` into `if 'user' not in parsed:`."""
    now = int(time.time())
    fields = {
        'auth_date': str(now),
        'query_id': 'AAH...',
    }
    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret_key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    fields['hash'] = h
    init_data = urllib.parse.urlencode(fields)
    result = verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=now)
    assert 'user' not in result


def test_auth_date_non_integer_rejected() -> None:
    """auth_date='abc' raises 'auth_date malformed'.  Tests the ValueError branch."""
    fields = {
        'auth_date': 'abc',
        'query_id': 'AAH...',
        'user': '{"id":1}',
    }
    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret_key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    fields['hash'] = h
    init_data = urllib.parse.urlencode(fields)
    with pytest.raises(InitDataVerificationError, match='malformed'):
        verify_telegram_webapp_initdata(init_data, BOT_TOKEN, now_unix=time.time())

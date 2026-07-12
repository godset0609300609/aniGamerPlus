"""Tests for ``app.security.crypto`` — the Fernet symmetric-encryption helper.

D-2 (security audit): the module had no dedicated test file despite being
the sole thing standing between a hydrogram session string and plaintext at
rest. ``tests/conftest.py``'s autouse ``_tg_fernet_key`` fixture provides a
valid key for every test in the suite (and clears the memoized ``Fernet``
instance before/after); the "missing"/"malformed" cases below deliberately
override that for the duration of the test.
"""

from __future__ import annotations

import cryptography.fernet
import pytest

from app.security import crypto

#: A second, independently-generated valid Fernet key — distinct from the
#: one ``tests/conftest.py``'s autouse fixture installs — used to prove
#: decrypt fails cleanly under a key mismatch.
_OTHER_VALID_KEY = 'htUmqmFlfvopZ49Z-ohfpHQqW3HqnijsarieqrhXyW0='


def test_fernet_key_missing_raises_clean_error_without_leaking_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(crypto.FERNET_KEY_ENV_VAR, raising=False)
    crypto.reset_fernet_cache()

    with pytest.raises(crypto.FernetKeyMissingError) as exc_info:
        crypto.encrypt_str('secret')

    message = str(exc_info.value)
    # Names the env var (so an operator knows what to set) but never a key
    # value — there is no key value to leak here (that's the point of the
    # test), so this also guards against a future refactor accidentally
    # interpolating one in.
    assert crypto.FERNET_KEY_ENV_VAR in message
    assert 'generate_key' in message  # points at the fix, not just the symptom


def test_fernet_key_malformed_raises_same_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(crypto.FERNET_KEY_ENV_VAR, 'not-a-valid-fernet-key')
    crypto.reset_fernet_cache()

    with pytest.raises(crypto.FernetKeyMissingError) as exc_info:
        crypto.encrypt_str('secret')

    # The malformed value itself must never appear in the error text.
    assert 'not-a-valid-fernet-key' not in str(exc_info.value)


def test_encrypt_decrypt_round_trips() -> None:
    plaintext = 'a hydrogram session string, or any other secret'

    token = crypto.encrypt_str(plaintext)

    assert token != plaintext
    assert plaintext not in token
    assert crypto.decrypt_str(token) == plaintext


def test_decrypt_with_different_key_raises_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = crypto.encrypt_str('secret')

    monkeypatch.setenv(crypto.FERNET_KEY_ENV_VAR, _OTHER_VALID_KEY)
    crypto.reset_fernet_cache()

    with pytest.raises(cryptography.fernet.InvalidToken):
        crypto.decrypt_str(token)

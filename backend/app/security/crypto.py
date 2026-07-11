"""Fernet symmetric-encryption helper for secrets stored at rest.

Currently the sole consumer is :class:`~app.persistence.tg_session_repo.TgSessionRepository`
(encrypting hydrogram session strings before they hit ``tg_session``), but the
helper is deliberately generic — any future "encrypt this column" need can
reuse :func:`encrypt_str` / :func:`decrypt_str` instead of hand-rolling
Fernet plumbing again.

The key is read from the ``ANIGAMERPLUS_FERNET_KEY`` environment variable
(a urlsafe-base64-encoded 32-byte key, i.e. whatever
``cryptography.fernet.Fernet.generate_key()`` produces). It is read lazily
— importing this module never fails even when the env var is unset — so
deployments that don't use the Telegram User API feature are unaffected.
The error only surfaces the moment something actually tries to
encrypt/decrypt without a configured key.
"""

from __future__ import annotations

import functools
import os

import cryptography.fernet

#: Environment variable holding the urlsafe-base64 Fernet key.
FERNET_KEY_ENV_VAR = 'ANIGAMERPLUS_FERNET_KEY'


class FernetKeyMissingError(RuntimeError):
    """Raised when encryption/decryption is attempted without a configured key."""

    def __init__(self) -> None:
        super().__init__(
            f'{FERNET_KEY_ENV_VAR} is not set. Generate one with: '
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
            'and set it before using any feature that encrypts data at rest '
            '(e.g. the Telegram User API integration).'
        )


@functools.lru_cache(maxsize=1)
def _fernet() -> cryptography.fernet.Fernet:
    key = os.environ.get(FERNET_KEY_ENV_VAR, '')
    if not key:
        raise FernetKeyMissingError
    try:
        return cryptography.fernet.Fernet(key.encode('ascii'))
    except (ValueError, TypeError) as exc:
        raise FernetKeyMissingError from exc


def reset_fernet_cache() -> None:
    """Drop the cached :class:`~cryptography.fernet.Fernet` instance.

    Test-only escape hatch: :func:`_fernet` is memoised via
    :func:`functools.lru_cache`, so a test that ``monkeypatch.setenv``s
    :data:`FERNET_KEY_ENV_VAR` between cases needs this to avoid reusing a
    stale key cached by an earlier test.
    """
    _fernet.cache_clear()


def encrypt_str(plaintext: str) -> str:
    """Encrypt *plaintext*, returning an opaque urlsafe-base64 token string."""
    return _fernet().encrypt(plaintext.encode('utf-8')).decode('ascii')


def decrypt_str(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt_str`.

    Raises ``cryptography.fernet.InvalidToken`` if the token is malformed,
    was encrypted with a different key, or has expired (unused here — no
    ``ttl`` is passed).
    """
    return _fernet().decrypt(token.encode('ascii')).decode('utf-8')

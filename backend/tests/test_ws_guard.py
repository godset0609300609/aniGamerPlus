"""Unit tests for :mod:`app.api.ws_guard`."""

from __future__ import annotations

import pytest

from app.api.ws_guard import (
    WebSocketConnectionRegistry,
    allowed_ws_origins,
    get_ws_connection_registry,
    is_origin_allowed,
)

# ---------------------------------------------------------------------------
# WebSocketConnectionRegistry
# ---------------------------------------------------------------------------


def test_try_acquire_succeeds_up_to_the_cap() -> None:
    registry = WebSocketConnectionRegistry(max_per_user=3)
    assert registry.try_acquire('u1') is True
    assert registry.try_acquire('u1') is True
    assert registry.try_acquire('u1') is True
    assert registry.count('u1') == 3


def test_try_acquire_rejects_over_the_cap() -> None:
    registry = WebSocketConnectionRegistry(max_per_user=2)
    assert registry.try_acquire('u1') is True
    assert registry.try_acquire('u1') is True
    assert registry.try_acquire('u1') is False
    # Rejected attempt must not have incremented the count.
    assert registry.count('u1') == 2


def test_release_frees_a_slot() -> None:
    registry = WebSocketConnectionRegistry(max_per_user=1)
    assert registry.try_acquire('u1') is True
    assert registry.try_acquire('u1') is False

    registry.release('u1')
    assert registry.try_acquire('u1') is True


def test_release_below_zero_is_a_noop() -> None:
    registry = WebSocketConnectionRegistry()
    registry.release('never-acquired')  # must not raise
    assert registry.count('never-acquired') == 0


def test_counts_are_independent_per_user() -> None:
    registry = WebSocketConnectionRegistry(max_per_user=1)
    assert registry.try_acquire('u1') is True
    assert registry.try_acquire('u2') is True
    assert registry.try_acquire('u1') is False
    assert registry.try_acquire('u2') is False


def test_get_ws_connection_registry_returns_singleton() -> None:
    first = get_ws_connection_registry()
    second = get_ws_connection_registry()
    assert first is second


# ---------------------------------------------------------------------------
# Origin allowlist
# ---------------------------------------------------------------------------


def test_missing_origin_is_allowed() -> None:
    """Non-browser clients typically don't send an Origin header at all."""
    assert is_origin_allowed(None) is True


@pytest.mark.parametrize(
    'origin',
    ['http://localhost:5173', 'http://127.0.0.1:5173', 'http://localhost:4173', 'http://web'],
)
def test_default_allowed_origins(origin: str) -> None:
    assert is_origin_allowed(origin) is True


def test_unknown_origin_is_rejected() -> None:
    assert is_origin_allowed('http://evil.example') is False


def test_env_var_overrides_default_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_WS_ALLOWED_ORIGINS', 'https://my-domain.example, https://other.example')

    assert allowed_ws_origins() == ('https://my-domain.example', 'https://other.example')
    assert is_origin_allowed('https://my-domain.example') is True
    # The env var fully replaces the default list — localhost dev origins no
    # longer pass once a custom allowlist is configured.
    assert is_origin_allowed('http://localhost:5173') is False


def test_env_var_unset_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('ANIGAMERPLUS_WS_ALLOWED_ORIGINS', raising=False)
    assert 'http://localhost:5173' in allowed_ws_origins()
    assert 'http://web' in allowed_ws_origins()

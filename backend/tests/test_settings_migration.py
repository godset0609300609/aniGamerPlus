"""Tests for ``app.persistence.settings_migration.migrate``."""

from __future__ import annotations

from app.models import AppSettings
from app.persistence.settings_migration import (
    LATEST_CONFIG_VERSION,
    migrate,
)


def test_empty_input_yields_v17_2_defaults() -> None:
    out = migrate({})
    assert out['config_version'] == LATEST_CONFIG_VERSION
    # Pydantic is the gatekeeper: make sure the migrated dict validates.
    settings = AppSettings.model_validate(out)
    assert settings.config_version == LATEST_CONFIG_VERSION


def test_already_current_version_is_passed_through_as_copy() -> None:
    src = {'config_version': 17.2, 'bangumi_dir': '/data/b'}
    out = migrate(src)
    assert out == src
    assert out is not src  # fresh copy


def test_legacy_proxies_dict_collapses_to_scalar_proxy() -> None:
    src = {
        'config_version': 10.0,
        'proxies': {'1': 'http://foo', '2': ''},
    }
    out = migrate(src)
    assert out['proxy'] == 'http://foo'
    assert 'proxies' not in out
    assert out['config_version'] == LATEST_CONFIG_VERSION


def test_audio_language_jpn_is_stripped() -> None:
    src = {'config_version': 15.0, 'audio_language_jpn': True}
    out = migrate(src)
    assert 'audio_language_jpn' not in out
    # audio_language default populated
    assert out['audio_language'] is False


def test_migrate_is_idempotent() -> None:
    src = {
        'config_version': 2.0,
        'proxies': {'1': 'http://bar'},
        'audio_language_jpn': True,
    }
    once = migrate(src)
    twice = migrate(once)
    assert once == twice


def test_does_not_mutate_input() -> None:
    src = {'config_version': 5.0, 'proxies': {'1': 'http://x'}}
    snapshot = dict(src)
    migrate(src)
    assert src == snapshot


def test_legacy_config_without_auth_gets_default_auth_section() -> None:
    """Pre-v17.3 configs (no 'auth' key) load cleanly with default DiscordAuthSettings."""
    src = {
        'config_version': 16.0,
        'bangumi_dir': '/data/bangumi',
    }
    out = migrate(src)
    assert 'auth' in out
    assert out['auth']['enabled'] is False
    assert out['auth']['client_id'] == ''
    assert out['auth']['redirect_uri'] == 'http://localhost:8000/api/auth/callback'
    assert out['auth']['bootstrap_admin_ids'] == []
    assert out['auth']['session_secret'] == ''

    from app.models import AppSettings, DiscordAuthSettings

    settings = AppSettings.model_validate(out)
    assert isinstance(settings.auth, DiscordAuthSettings)
    assert settings.auth.enabled is False


def test_migrated_dict_validates_against_app_settings() -> None:
    # A "hostile" realistic legacy blob: mixture of removed keys, missing
    # keys, and odd shapes. The pydantic validator is the final arbiter.
    src = {
        'config_version': 1.0,
        'audio_language_jpn': True,
        'proxies': {'1': 'http://legacy'},
        'ftp': {'server': 'f', 'port': ''},
        'multi-thread': 2,
    }
    out = migrate(src)
    settings = AppSettings.model_validate(out)
    assert settings.multi_thread == 2
    assert settings.proxy == 'http://legacy'
    assert settings.ftp.server == 'f'

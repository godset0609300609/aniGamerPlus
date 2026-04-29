"""Tests for the full v17.2 ``AppSettings`` model."""

from __future__ import annotations

from app.models import AppSettings, DiscordAuthSettings, TelegramSettings, WebSettings


def test_empty_dict_validates_to_defaults() -> None:
    settings = AppSettings.model_validate({})
    assert settings.config_version == 17.2
    assert settings.database_version == 2.0
    assert settings.bangumi_dir == ''
    assert settings.multi_thread == 1
    assert settings.ftp.tls is True
    assert settings.dashboard.host == '127.0.0.1'


def test_multi_thread_alias_round_trips() -> None:
    settings = AppSettings.model_validate({'multi-thread': 4})
    assert settings.multi_thread == 4

    dumped = settings.model_dump(by_alias=True)
    assert 'multi-thread' in dumped
    assert dumped['multi-thread'] == 4
    assert 'multi_thread' not in dumped


def test_web_subset_projects_matching_fields() -> None:
    settings = AppSettings.model_validate({'multi-thread': 3, 'download_resolution': '720', 'danmu': True})
    subset = settings.web_subset()
    assert isinstance(subset, WebSettings)
    assert subset.multi_thread == 3
    assert subset.download_resolution == '720'
    assert subset.danmu is True


def test_extra_keys_are_ignored() -> None:
    # Extra keys silently dropped — no ValidationError, not round-tripped.
    settings = AppSettings.model_validate({'bangumi_dir': '/tmp/b', 'legacy_goo': 'keep-away'})
    dumped = settings.model_dump(by_alias=True)
    assert 'legacy_goo' not in dumped
    assert settings.bangumi_dir == '/tmp/b'


def test_config_version_defaults_to_17_2() -> None:
    settings = AppSettings.model_validate({})
    assert settings.config_version == 17.2


def test_ftp_port_accepts_empty_string_and_int() -> None:
    # Legacy convention: port may be "" until the user fills it in.
    a = AppSettings.model_validate({'ftp': {'port': ''}})
    b = AppSettings.model_validate({'ftp': {'port': 21}})
    assert a.ftp.port == ''
    assert b.ftp.port == 21


def test_auth_section_defaults() -> None:
    settings = AppSettings.model_validate({})
    assert isinstance(settings.auth, DiscordAuthSettings)
    assert settings.auth.enabled is False
    assert settings.auth.client_id == ''
    assert settings.auth.redirect_uri == 'http://localhost:8000/api/auth/callback'
    assert settings.auth.bootstrap_admin_ids == []
    assert settings.auth.session_secret == ''


def test_auth_section_round_trips() -> None:
    raw = {
        'auth': {
            'enabled': True,
            'client_id': 'my-client',
            'client_secret': 'secret',
            'redirect_uri': 'http://example.com/callback',
            'bootstrap_admin_ids': ['123', '456'],
            'session_secret': 'supersecret',
        }
    }
    settings = AppSettings.model_validate(raw)
    assert settings.auth.enabled is True
    assert settings.auth.client_id == 'my-client'
    assert settings.auth.bootstrap_admin_ids == ['123', '456']
    assert settings.auth.session_secret == 'supersecret'

    dumped = settings.model_dump(by_alias=True)
    assert dumped['auth']['enabled'] is True
    assert dumped['auth']['client_id'] == 'my-client'


def test_telegram_section_defaults() -> None:
    settings = AppSettings.model_validate({})
    assert isinstance(settings.telegram, TelegramSettings)
    assert settings.telegram.enabled is False
    assert settings.telegram.bot_token == ''
    assert settings.telegram.webhook_secret == ''
    assert settings.telegram.public_url == ''
    assert settings.telegram.notify_on == ['started', 'completed', 'failed', 'cancelled', 'auto_enqueue']
    assert settings.telegram.rate_limit_per_minute == 30


def test_telegram_section_round_trips() -> None:
    raw = {
        'telegram': {
            'enabled': True,
            'bot_token': '123:ABC',
            'webhook_secret': 'secret',
            'public_url': 'https://example.com',
            'notify_on': ['completed'],
            'rate_limit_per_minute': 60,
        }
    }
    settings = AppSettings.model_validate(raw)
    assert settings.telegram.enabled is True
    assert settings.telegram.bot_token == '123:ABC'
    assert settings.telegram.notify_on == ['completed']
    assert settings.telegram.rate_limit_per_minute == 60

    dumped = settings.model_dump(by_alias=True)
    assert dumped['telegram']['enabled'] is True
    assert dumped['telegram']['bot_token'] == '123:ABC'

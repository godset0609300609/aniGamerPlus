"""Tests for pydantic models (alias round-trip, validation)."""

from __future__ import annotations

import pydantic
import pytest

from app.models import BtDownloaderSettings, ManualTaskRequest, WebSettings


def test_web_settings_accepts_hyphenated_alias() -> None:
    payload = {'multi-thread': 3, 'download_resolution': '1080'}
    model = WebSettings.model_validate(payload)
    assert model.multi_thread == 3


def test_web_settings_round_trip_emits_alias() -> None:
    model = WebSettings(multi_thread=4)
    dumped = model.model_dump(by_alias=True)
    assert 'multi-thread' in dumped
    assert dumped['multi-thread'] == 4
    assert 'multi_thread' not in dumped


def test_web_settings_rejects_unknown_resolution() -> None:
    with pytest.raises(pydantic.ValidationError):
        WebSettings.model_validate({'download_resolution': '2160'})


def test_manual_task_request_validates_thread_range() -> None:
    with pytest.raises(pydantic.ValidationError):
        ManualTaskRequest(sn='1', thread=0)
    with pytest.raises(pydantic.ValidationError):
        ManualTaskRequest(sn='1', thread=100)


def test_manual_task_request_mode_literal() -> None:
    with pytest.raises(pydantic.ValidationError):
        ManualTaskRequest.model_validate({'sn': '1', 'mode': 'not-a-mode'})


def test_bt_downloader_dumps_with_kebab_case_aliases() -> None:
    model = WebSettings()
    dumped = model.model_dump(by_alias=True)

    assert 'bt-downloader' in dumped
    assert 'bt_downloader' not in dumped

    bt = dumped['bt-downloader']
    assert set(bt) == {
        'enabled',
        'poll-interval-seconds',
        'landing-poll-seconds',
        'hanzi-convert',
        'landing-dir',
        'entry-retention-days',
        'task-history-retention-days',
        'auto-delete-remote-on-landed',
    }


def test_bt_downloader_loads_from_kebab_case_config() -> None:
    payload = {
        'bt-downloader': {
            'enabled': True,
            'poll-interval-seconds': 120,
            'landing-poll-seconds': 45,
            'hanzi-convert': False,
            'landing-dir': '/tmp/landing',
            'entry-retention-days': 30,
            'task-history-retention-days': 60,
        }
    }
    model = WebSettings.model_validate(payload)

    assert model.bt_downloader.enabled is True
    assert model.bt_downloader.poll_interval_seconds == 120
    assert model.bt_downloader.landing_poll_seconds == 45
    assert model.bt_downloader.hanzi_convert is False
    assert model.bt_downloader.landing_dir == '/tmp/landing'
    assert model.bt_downloader.entry_retention_days == 30
    assert model.bt_downloader.task_history_retention_days == 60


def test_bt_downloader_loads_from_snake_case_for_backwards_compat() -> None:
    # populate_by_name=True must let legacy snake_case config.json values
    # (from before the alias fix) still load correctly.
    model = BtDownloaderSettings(
        enabled=True,
        poll_interval_seconds=90,
        landing_poll_seconds=40,
        hanzi_convert=False,
        landing_dir='/tmp/legacy',
        entry_retention_days=45,
        task_history_retention_days=120,
    )

    assert model.enabled is True
    assert model.poll_interval_seconds == 90
    assert model.landing_poll_seconds == 40
    assert model.hanzi_convert is False
    assert model.landing_dir == '/tmp/legacy'
    assert model.entry_retention_days == 45
    assert model.task_history_retention_days == 120


def test_bt_downloader_retention_days_default() -> None:
    model = BtDownloaderSettings()
    assert model.entry_retention_days == 90
    assert model.task_history_retention_days == 180


def test_bt_downloader_retention_days_reject_non_positive() -> None:
    with pytest.raises(pydantic.ValidationError):
        BtDownloaderSettings(entry_retention_days=0)
    with pytest.raises(pydantic.ValidationError):
        BtDownloaderSettings(task_history_retention_days=0)


def test_bt_downloader_auto_delete_remote_on_landed_defaults_true() -> None:
    model = BtDownloaderSettings()
    assert model.auto_delete_remote_on_landed is True


def test_bt_downloader_auto_delete_remote_on_landed_loads_from_kebab_case_config() -> None:
    payload = {'bt-downloader': {'auto-delete-remote-on-landed': False}}
    model = WebSettings.model_validate(payload)
    assert model.bt_downloader.auto_delete_remote_on_landed is False


def test_bt_downloader_auto_delete_remote_on_landed_loads_from_snake_case_for_backwards_compat() -> None:
    model = BtDownloaderSettings(auto_delete_remote_on_landed=False)
    assert model.auto_delete_remote_on_landed is False

"""Tests for BT downloader job registration in ``app.scheduler.aps_scheduler.ApsScheduler``."""

from __future__ import annotations

import types
import typing as T
import unittest.mock

import pytest


def _fake_settings_repo(
    *,
    bt_enabled: bool,
    poll_interval_seconds: int = 300,
    landing_poll_seconds: int = 60,
    check_frequency: int = 5,
) -> T.Any:
    repo = unittest.mock.MagicMock()
    repo.load.return_value = types.SimpleNamespace(
        check_frequency=check_frequency,
        bt_downloader=types.SimpleNamespace(
            enabled=bt_enabled,
            poll_interval_seconds=poll_interval_seconds,
            landing_poll_seconds=landing_poll_seconds,
        ),
    )
    return repo


class _SpyScheduler:
    """Records add_job calls without starting a real background thread."""

    def __init__(self) -> None:
        self.captured: list[dict[str, T.Any]] = []
        self.running: bool = False

    def add_job(self, *_args: object, **kwargs: object) -> None:
        self.captured.append(dict(kwargs))

    def start(self) -> None:
        self.running = True

    def shutdown(self, *, wait: bool = True) -> None:
        self.running = False


def _build_aps(settings_repo: T.Any) -> tuple[object, _SpyScheduler]:
    """Return (ApsScheduler instance, spy) with actor imports patched out."""
    import app.scheduler.aps_scheduler as mod

    spy = _SpyScheduler()

    fake_auto_scan = unittest.mock.MagicMock()
    fake_progress = unittest.mock.MagicMock()
    fake_health = unittest.mock.MagicMock()
    fake_bt_feed_tick = unittest.mock.MagicMock()
    fake_bt_landing_tick = unittest.mock.MagicMock()
    fake_bt_retention_tick = unittest.mock.MagicMock()
    fake_bt_remote_refresh_tick = unittest.mock.MagicMock()

    with (
        unittest.mock.patch.dict(
            'sys.modules',
            {
                'app.tasks.auto_scan': types.SimpleNamespace(auto_scan_tick=fake_auto_scan),
                'app.services.telegram_progress_publisher': types.SimpleNamespace(progress_publish_tick=fake_progress),
                'app.services.telegram_health_monitor': types.SimpleNamespace(health_check_tick=fake_health),
                'app.tasks.bt_feed_tick': types.SimpleNamespace(bt_feed_tick=fake_bt_feed_tick),
                'app.tasks.bt_landing_tick': types.SimpleNamespace(bt_landing_tick=fake_bt_landing_tick),
                'app.tasks.bt_retention_tick': types.SimpleNamespace(bt_retention_tick=fake_bt_retention_tick),
                'app.tasks.bt_remote_refresh_tick': types.SimpleNamespace(
                    bt_remote_refresh_tick=fake_bt_remote_refresh_tick
                ),
            },
        ),
    ):
        aps = mod.ApsScheduler(settings_repo)
        aps._scheduler = spy  # type: ignore[assignment]
        aps.start()

    return aps, spy


def test_bt_jobs_not_registered_when_disabled() -> None:
    _aps, spy = _build_aps(_fake_settings_repo(bt_enabled=False))

    ids = {job.get('id') for job in spy.captured}
    assert 'bt_feed_tick' not in ids
    assert 'bt_landing_tick' not in ids
    assert 'bt_remote_refresh_tick' not in ids
    # bt_retention_tick is always registered (independent of bt_downloader.enabled).
    assert 'bt_retention_tick' in ids
    # The three baseline jobs plus bt_retention_tick.
    assert len(spy.captured) == 4


def test_bt_jobs_registered_when_enabled() -> None:
    _aps, spy = _build_aps(_fake_settings_repo(bt_enabled=True, poll_interval_seconds=120, landing_poll_seconds=45))

    ids = {job.get('id') for job in spy.captured}
    assert 'bt_feed_tick' in ids
    assert 'bt_landing_tick' in ids
    assert 'bt_retention_tick' in ids
    assert 'bt_remote_refresh_tick' in ids
    assert len(spy.captured) == 7

    feed_job = next(j for j in spy.captured if j.get('id') == 'bt_feed_tick')
    assert feed_job.get('seconds') == 120
    assert feed_job.get('replace_existing') is True
    assert feed_job.get('coalesce') is True
    assert feed_job.get('misfire_grace_time') == 60

    landing_job = next(j for j in spy.captured if j.get('id') == 'bt_landing_tick')
    assert landing_job.get('seconds') == 45
    assert landing_job.get('replace_existing') is True
    assert landing_job.get('coalesce') is True
    assert landing_job.get('misfire_grace_time') == 30

    remote_refresh_job = next(j for j in spy.captured if j.get('id') == 'bt_remote_refresh_tick')
    assert remote_refresh_job.get('seconds') == 600  # default when the env var is unset
    assert remote_refresh_job.get('replace_existing') is True
    assert remote_refresh_job.get('coalesce') is True
    assert remote_refresh_job.get('misfire_grace_time') == 60


def test_bt_remote_refresh_job_reads_interval_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ANIGAMERPLUS_BT_REMOTE_REFRESH_SECONDS', '120')
    _aps, spy = _build_aps(_fake_settings_repo(bt_enabled=True))

    remote_refresh_job = next(j for j in spy.captured if j.get('id') == 'bt_remote_refresh_tick')
    assert remote_refresh_job.get('seconds') == 120

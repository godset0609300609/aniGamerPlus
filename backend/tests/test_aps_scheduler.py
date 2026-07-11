"""Tests for ``app.scheduler.aps_scheduler.ApsScheduler``."""

from __future__ import annotations

import types
import typing as T
import unittest.mock


def _fake_settings_repo(check_frequency: int = 5) -> T.Any:
    repo = unittest.mock.MagicMock()
    repo.load.return_value = types.SimpleNamespace(
        check_frequency=check_frequency,
        bt_downloader=types.SimpleNamespace(
            enabled=False,
            poll_interval_seconds=300,
            landing_poll_seconds=60,
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


def _build_aps(check_frequency: int = 5) -> tuple[object, _SpyScheduler]:
    """Return (ApsScheduler instance, spy) with actor imports patched out."""
    import app.scheduler.aps_scheduler as mod

    spy = _SpyScheduler()

    fake_auto_scan = unittest.mock.MagicMock()
    fake_progress = unittest.mock.MagicMock()
    fake_health = unittest.mock.MagicMock()
    fake_retention = unittest.mock.MagicMock()

    with (
        unittest.mock.patch.dict(
            'sys.modules',
            {
                'app.tasks.auto_scan': types.SimpleNamespace(auto_scan_tick=fake_auto_scan),
                'app.services.telegram_progress_publisher': types.SimpleNamespace(progress_publish_tick=fake_progress),
                'app.services.telegram_health_monitor': types.SimpleNamespace(health_check_tick=fake_health),
                'app.tasks.bt_retention_tick': types.SimpleNamespace(bt_retention_tick=fake_retention),
            },
        ),
    ):
        aps = mod.ApsScheduler(_fake_settings_repo(check_frequency))
        aps._scheduler = spy  # type: ignore[assignment]
        aps.start()

    return aps, spy


def test_aps_scheduler_periodic_jobs_have_misfire_settings() -> None:
    """All periodic jobs must carry coalesce + max_instances + misfire_grace_time."""
    _aps, spy = _build_aps()

    assert len(spy.captured) == 4, f'expected 4 jobs, got {len(spy.captured)}'

    for job in spy.captured:
        assert job.get('coalesce') is True, f'coalesce missing on {job}'
        assert job.get('max_instances', 1) >= 2, f'max_instances too low on {job}'
        assert job.get('misfire_grace_time', 0) >= 30, f'misfire_grace_time too low on {job}'


def test_aps_scheduler_replace_existing_is_set() -> None:
    """replace_existing=True keeps start() idempotent-safe."""
    _aps, spy = _build_aps()

    for job in spy.captured:
        assert job.get('replace_existing') is True, f'replace_existing missing on {job}'


def test_aps_scheduler_auto_scan_uses_check_frequency() -> None:
    """auto_scan_tick interval uses the value from settings_repo."""
    _aps, spy = _build_aps(check_frequency=10)

    auto_scan = next(j for j in spy.captured if j.get('id') == 'auto_scan_tick')
    assert auto_scan.get('minutes') == 10


def test_aps_scheduler_bt_retention_tick_registered_daily() -> None:
    """bt_retention_tick is always scheduled (independent of bt_downloader.enabled),
    on a 24-hour interval."""
    _aps, spy = _build_aps()

    retention = next(j for j in spy.captured if j.get('id') == 'bt_retention_tick')
    assert retention.get('hours') == 24


def test_aps_scheduler_bt_retention_tick_registered_even_when_bt_downloader_disabled() -> None:
    """Unlike bt_feed_tick / bt_landing_tick, bt_retention_tick has no bt_downloader.enabled gate."""
    _aps, spy = _build_aps()

    job_ids = {j.get('id') for j in spy.captured}
    assert 'bt_retention_tick' in job_ids
    # bt_downloader.enabled is False in _fake_settings_repo, so the gated
    # BT-specific ticks must be absent while retention is still present.
    assert 'bt_feed_tick' not in job_ids
    assert 'bt_landing_tick' not in job_ids

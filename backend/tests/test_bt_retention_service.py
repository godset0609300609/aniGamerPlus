"""Tests for BtRetentionService.

Orchestration-level tests — repos are hand-written fakes so we can assert
exactly which retention-day values get threaded through from settings.
"""

from __future__ import annotations

import typing as T

from app.models import AppSettings, BtDownloaderSettings
from app.services.bt_retention_service import BtRetentionService


class FakeBtFeedEntryRepo:
    def __init__(self, deleted: int = 0) -> None:
        self.deleted = deleted
        self.delete_stale_calls: list[int] = []

    def delete_stale(self, days: int) -> int:
        self.delete_stale_calls.append(days)
        return self.deleted


class FakeTaskHistoryRepo:
    def __init__(self, deleted: int = 0) -> None:
        self.deleted = deleted
        self.delete_older_than_calls: list[int] = []

    def delete_older_than(self, days: int) -> int:
        self.delete_older_than_calls.append(days)
        return self.deleted


class FakeSettingsRepo:
    def __init__(self, bt_downloader: BtDownloaderSettings) -> None:
        self._settings = AppSettings(bt_downloader=bt_downloader)

    def load(self) -> AppSettings:
        return self._settings


class FakeLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[T.Any, ...]] = []

    def info(self, sn: object, tag: str, detail: str = '', **kwargs: object) -> None:
        self.info_calls.append((sn, tag, detail))


def test_prune_stale_uses_configured_retention_days() -> None:
    entry_repo = FakeBtFeedEntryRepo(deleted=3)
    history_repo = FakeTaskHistoryRepo(deleted=5)
    settings_repo = FakeSettingsRepo(BtDownloaderSettings(entry_retention_days=42, task_history_retention_days=99))

    service = BtRetentionService(entry_repo, history_repo, settings_repo)
    deleted_entries, deleted_history = service.prune_stale()

    assert deleted_entries == 3
    assert deleted_history == 5
    assert entry_repo.delete_stale_calls == [42]
    assert history_repo.delete_older_than_calls == [99]


def test_prune_stale_uses_defaults_when_settings_unset() -> None:
    entry_repo = FakeBtFeedEntryRepo()
    history_repo = FakeTaskHistoryRepo()
    settings_repo = FakeSettingsRepo(BtDownloaderSettings())

    service = BtRetentionService(entry_repo, history_repo, settings_repo)
    service.prune_stale()

    assert entry_repo.delete_stale_calls == [90]
    assert history_repo.delete_older_than_calls == [180]


def test_prune_stale_logs_when_rows_deleted() -> None:
    entry_repo = FakeBtFeedEntryRepo(deleted=2)
    history_repo = FakeTaskHistoryRepo(deleted=0)
    settings_repo = FakeSettingsRepo(BtDownloaderSettings())
    logger = FakeLogger()

    service = BtRetentionService(entry_repo, history_repo, settings_repo, logger=logger)
    service.prune_stale()

    assert len(logger.info_calls) == 1


def test_prune_stale_does_not_log_when_nothing_deleted() -> None:
    entry_repo = FakeBtFeedEntryRepo(deleted=0)
    history_repo = FakeTaskHistoryRepo(deleted=0)
    settings_repo = FakeSettingsRepo(BtDownloaderSettings())
    logger = FakeLogger()

    service = BtRetentionService(entry_repo, history_repo, settings_repo, logger=logger)
    service.prune_stale()

    assert logger.info_calls == []


def test_prune_stale_clamps_retention_days_to_minimum_one() -> None:
    """Defensive clamp even though the pydantic field already enforces ge=1."""
    entry_repo = FakeBtFeedEntryRepo()
    history_repo = FakeTaskHistoryRepo()

    class ZeroDaysSettingsRepo:
        def load(self) -> AppSettings:
            settings = AppSettings(bt_downloader=BtDownloaderSettings())
            # Direct attribute assignment bypasses field validation (pydantic
            # only validates on assignment when validate_assignment=True,
            # which BtDownloaderSettings does not set) — simulates a
            # hand-built settings object with an out-of-range value even
            # though the ge=1 field constraint prevents this via normal
            # construction / JSON load.
            settings.bt_downloader.entry_retention_days = 0
            settings.bt_downloader.task_history_retention_days = -5
            return settings

    service = BtRetentionService(entry_repo, history_repo, ZeroDaysSettingsRepo())
    service.prune_stale()

    assert entry_repo.delete_stale_calls == [1]
    assert history_repo.delete_older_than_calls == [1]

"""Tests for BT downloader field wiring on ``app.core.Container``.

Builds a real container against an isolated ``tmp_path`` workspace (via
``ANIGAMERPLUS_WORKSPACE_DIR``) and an unreachable Redis URL so the
Redis-dependent branch takes its documented "unavailable" fallback path
instead of requiring a live Redis server.
"""

from __future__ import annotations

import collections.abc
import json
import pathlib
import time
import typing as T

import pytest


@pytest.fixture
def isolated_container(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> collections.abc.Iterator[T.Any]:
    from app.core import build_container

    monkeypatch.setenv('ANIGAMERPLUS_WORKSPACE_DIR', str(tmp_path))
    # Deliberately unreachable so build_container()'s try/except takes the
    # "Redis unavailable" fallback path instead of requiring a live server.
    monkeypatch.setenv('ANIGAMERPLUS_REDIS_URL', 'redis://127.0.0.1:1/0')

    build_container.cache_clear()
    container = build_container()
    try:
        yield container
    finally:
        container.database.dispose()
        build_container.cache_clear()


def test_build_container_with_unreachable_redis_fails_fast_into_fallback(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for a Linux-only boot hang: ``redis.Redis.from_url``
    previously had no ``socket_connect_timeout`` (infinite default), so on a
    host that drops the SYN to an unreachable address instead of refusing it
    (127.0.0.1:1 on Linux CI, unlike Windows' instant ECONNREFUSED),
    ``ping()`` in ``build_container()`` blocked forever instead of taking the
    documented "Redis unavailable" fallback. Asserts both that the call
    returns quickly and that the fallback path was actually taken (not just
    that we got lucky and didn't hang)."""
    from app.core import build_container

    monkeypatch.setenv('ANIGAMERPLUS_WORKSPACE_DIR', str(tmp_path))
    monkeypatch.setenv('ANIGAMERPLUS_REDIS_URL', 'redis://127.0.0.1:1/0')

    build_container.cache_clear()
    start = time.monotonic()
    container = build_container()
    elapsed = time.monotonic() - start
    try:
        # socket_connect_timeout=2 on the ping()'d sync client bounds the
        # worst case; well under the old "hangs forever" behavior and under
        # pytest-timeout's global backstop.
        assert elapsed < 10, f'build_container() took {elapsed:.1f}s against an unreachable Redis'
        assert container.redis_client_sync is None
        assert container.redis_client_async is None
        assert container.redis_progress_reader is None
    finally:
        container.database.dispose()
        build_container.cache_clear()


def test_bt_fields_are_wired_with_correct_types(isolated_container: T.Any) -> None:
    from app.bt_downloader.landing_worker import LandingWorker
    from app.persistence.bt_feed_entry_repo import BtFeedEntryRepository
    from app.persistence.bt_feed_repo import BtFeedRepository
    from app.persistence.bt_filter_repo import BtFilterRepository
    from app.persistence.putio_token_repo import PutioTokenRepository
    from app.services.bt_downloader_service import BtDownloaderService
    from app.services.bt_probe_service import BtProbeService

    container = isolated_container

    assert container.putio_token_repo is not None
    assert isinstance(container.putio_token_repo, PutioTokenRepository)

    assert container.bt_feed_repo is not None
    assert isinstance(container.bt_feed_repo, BtFeedRepository)

    assert container.bt_filter_repo is not None
    assert isinstance(container.bt_filter_repo, BtFilterRepository)

    assert container.bt_feed_entry_repo is not None
    assert isinstance(container.bt_feed_entry_repo, BtFeedEntryRepository)

    assert container.bt_downloader_service is not None
    assert isinstance(container.bt_downloader_service, BtDownloaderService)

    assert container.bt_probe_service is not None
    assert isinstance(container.bt_probe_service, BtProbeService)

    assert container.bt_landing_worker is not None
    assert isinstance(container.bt_landing_worker, LandingWorker)


def test_bt_progress_bus_is_separate_instance_with_no_history_repo(isolated_container: T.Any) -> None:
    """bt_progress_bus feeds MonitorView for BT rows but must not also own
    task_history persistence — BtDownloaderService/LandingWorker already
    write task_history directly, so a history_repo-backed ProgressBus here
    would double-INSERT a row per dispatch (see the Container.bt_progress_bus
    field docstring)."""
    from app.downloader.progress import ProgressBus
    from app.services.bt_manual_dispatch_service import BtManualDispatchService

    container = isolated_container

    assert isinstance(container.bt_progress_bus, ProgressBus)
    assert container.bt_progress_bus is not container.progress_bus
    assert container.bt_progress_bus._history_repo is None

    assert isinstance(container.bt_manual_dispatch_service, BtManualDispatchService)
    assert container.bt_downloader_service._progress_bus is container.bt_progress_bus
    assert container.bt_manual_dispatch_service._progress_bus is container.bt_progress_bus
    assert container.bt_landing_worker._progress_bus is container.bt_progress_bus


def test_bt_notify_event_send_is_none_without_telegram_bot_token(isolated_container: T.Any) -> None:
    """No bot_token configured (the isolated_container default) -> BT telegram wiring stays a no-op."""
    container = isolated_container

    assert container.bt_downloader_service._notify_event_send is None
    assert container.bt_landing_worker._notify_event_send is None


def test_bt_notify_event_send_wired_when_telegram_enabled(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a bot_token configured, both BT collaborators get a live notify_event_send closure."""
    from app.core import build_container

    monkeypatch.setenv('ANIGAMERPLUS_WORKSPACE_DIR', str(tmp_path))
    monkeypatch.setenv('ANIGAMERPLUS_REDIS_URL', 'redis://127.0.0.1:1/0')

    config_path = tmp_path / 'config.json'
    config_path.write_text(
        json.dumps({'telegram': {'bot_token': 'test-token', 'enabled': True}}),
        encoding='utf-8',
    )

    build_container.cache_clear()
    container = build_container()
    try:
        assert container.bt_downloader_service._notify_event_send is not None
        assert container.bt_landing_worker._notify_event_send is not None
        # Same closure instance is reused across every BT + manual/scheduler collaborator.
        assert container.bt_downloader_service._notify_event_send is container.bt_landing_worker._notify_event_send
    finally:
        # bot_token is set, so a real TelegramClient (httpx.AsyncClient) was
        # constructed — close it to avoid an unclosed-client resource leak.
        if container.telegram_client is not None:
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(container.telegram_client.close())
            finally:
                loop.close()
        container.database.dispose()
        build_container.cache_clear()

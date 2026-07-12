"""Pytest fixtures.

The cutover retired the legacy ``FakeConfig`` / ``FakeAniGamerPlus`` pair
in favour of a lightweight :class:`FakeContainer` holding real instances
of the new repos / services, each pointed at ``tmp_path`` or an in-memory
fake so the tests don't hit the real workspace.

The ``client`` fixture builds a real ``fastapi.FastAPI`` via
``app.main.create_app`` and overrides every ``get_xxx_service`` to return
a service bound to the fake container's fields.
"""

from __future__ import annotations

import collections.abc
import contextlib
import dataclasses
import json
import logging
import pathlib
import socket
import sys
from typing import Any

import fastapi.testclient
import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def pytest_collection_finish(session: pytest.Session) -> None:
    """Undo hydrogram's import-time global uvloop event-loop-policy hijack.

    hydrogram/__init__.py sets uvloop as the process-wide asyncio event loop
    policy at import time. On Linux CI (uvloop present) this silently swaps
    the loop implementation for the entire test session the moment any TG
    test module is collected, which made an async test elsewhere hang for
    24+ min. Reset to the stdlib default here — after collection has imported
    every test module (so hydrogram has already run) but before any test
    body executes — so the suite runs under the same deterministic default
    loop as local dev (Windows has no uvloop, so it always did).

    Passing ``None`` (rather than constructing ``asyncio.DefaultEventLoopPolicy``
    directly) clears the explicit policy so ``get_event_loop_policy()`` lazily
    recreates the platform default on next use. This sidesteps a second,
    narrower Python 3.14 deprecation: unlike ``set_event_loop_policy`` itself
    (already ignored below because hydrogram triggers it too),
    ``asyncio.DefaultEventLoopPolicy`` has no matching ``ignore:`` entry in
    ``filterwarnings``, and with ``filterwarnings = ["error", ...]`` that
    warning would abort collection with an INTERNALERROR instead of just
    resetting the policy.
    """
    import asyncio

    asyncio.set_event_loop_policy(None)


@pytest.fixture(autouse=True)
def _stub_url_guard_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the SSRF guard's DNS resolution so tests never depend on real network.

    ``app.security.url_guard.is_safe_public_url`` calls ``socket.getaddrinfo``
    for any hostname that isn't an IP literal, to defend against DNS
    rebinding. Fixture URLs across the suite use throwaway hostnames
    (``a.example``, ``dmhy.org``, ...) that may not resolve — or may resolve
    differently — outside this sandbox, so every hostname resolves to a
    fixed public IP here. Tests that specifically exercise guard
    rejection/DNS-rebinding behaviour re-patch ``socket.getaddrinfo`` (or use
    an IP-literal URL, which never reaches this code path) within the test
    itself, which takes precedence over this fixture.
    """

    def _fake_getaddrinfo(_host: str, *_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))]

    monkeypatch.setattr('app.security.url_guard.socket.getaddrinfo', _fake_getaddrinfo)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Reset slowapi's in-memory limiter storage before each test.

    ``app.rate_limit.limiter`` is a process-wide singleton — every
    ``@limiter.limit(...)``-decorated route (auth login/callback/telegram-
    webapp, tasks/manual, bt feeds/probe) shares it across the whole test
    session. Without a reset, request counts from an earlier test would
    carry over (every ``TestClient`` request looks like it comes from the
    same "testclient" IP) and cause flaky 429s on tests that never intended
    to exercise rate limiting.
    """
    from app.rate_limit import limiter

    limiter.reset()


@pytest.fixture(autouse=True)
def _reset_ws_connection_registry() -> None:
    """Reset the module-level WS connection-registry singleton between tests.

    ``app.api.ws_guard.get_ws_connection_registry()`` returns a process-wide
    singleton so ``/api/ws/tasks_progress`` and ``/api/ws/logs`` share one
    per-user connection cap. Without resetting it, a per-user count left
    over from one test (e.g. an unclosed WS in a failed assertion) would
    leak into the next test's cap check.
    """
    import app.api.ws_guard as _wg

    _wg._registry = None


@pytest.fixture(autouse=True)
def _tg_fernet_key(monkeypatch: pytest.MonkeyPatch) -> collections.abc.Iterator[None]:
    """Provide a valid ``ANIGAMERPLUS_FERNET_KEY`` for every test.

    ``app.security.crypto`` memoises the parsed ``Fernet`` instance via
    ``functools.lru_cache``, so this both sets a fixed test key and clears
    that cache before/after each test — otherwise a test that runs after
    one which monkeypatched a *different* key would silently reuse the
    first test's cached ``Fernet`` instance.
    """
    from app.security import crypto

    monkeypatch.setenv(crypto.FERNET_KEY_ENV_VAR, 'KDLS-BvBYw4KYpq9qsWXC9Q9Dt8MuQrRdjz63WOpyYI=')
    crypto.reset_fernet_cache()
    yield
    crypto.reset_fernet_cache()


@pytest.fixture(autouse=True)
def _reset_app_log_handlers() -> collections.abc.Iterator[None]:
    """Strip handlers off the ``app.*`` logger tree between tests.

    ``Logger.__init__`` attaches a ``DailyLogFileHandler`` scoped to
    ``tmp_path`` on every instance; those handlers linger on the shared
    ``app.main`` / ``app.*`` loggers across tests and emit (or silently
    error-handle) into long-deleted temp directories. Clearing between
    tests keeps each test's filesystem assertions independent.
    """
    yield
    for name in list(logging.Logger.manager.loggerDict):
        if name == 'app' or name.startswith('app.'):
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                with contextlib.suppress(Exception):
                    handler.close()


# ---------------------------------------------------------------------------
# FakeManualRunner — captures calls in the ``ManualRunner.run`` shape.
# ---------------------------------------------------------------------------


class FakeManualRunner:
    """Captures calls to ``run``; the test harness asserts against ``run_calls``."""

    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    def run(self, sn: int | None, *, owner_id: str | None = None, **kwargs: Any) -> None:
        self.run_calls.append({'sn': sn, 'owner_id': owner_id, **kwargs})


# ---------------------------------------------------------------------------
# FakeContainer — composition root for tests.
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class FakeContainer:
    """Minimal Container stand-in for tests.

    Every field is a real instance of the corresponding class but pointing
    at ``tmp_path`` / in-memory state so no disk / network side-effects
    leak between tests.
    """

    paths: Any
    logger: Any
    settings_repo: Any
    sn_list_repo: Any
    cookie_repo: Any
    bilibili_cookie_repo: Any
    database: Any
    anime_repo: Any
    user_repo: Any
    anime_list_entry_repo: Any
    task_history_repo: Any
    task_id_map_repo: Any
    progress_bus: Any
    manual_runner: FakeManualRunner
    putio_token_repo: Any = None
    bt_feed_repo: Any = None
    bt_filter_repo: Any = None
    bt_feed_entry_repo: Any = None
    tg_session_repo: Any = None
    tg_watched_chat_repo: Any = None
    tg_downloaded_media_repo: Any = None


@pytest.fixture
def fake_container(
    tmp_path: pathlib.Path,
) -> collections.abc.Iterator[FakeContainer]:
    """Build a :class:`FakeContainer` with every field pointing at ``tmp_path``.

    Uses an on-disk SQLite DB under ``tmp_path`` with baseline migrations
    already applied, a ``SettingsRepository`` seeded with defaults, and a
    ``FakeManualRunner`` so no real downloads happen. The DB engine is
    disposed on fixture teardown so sqlite3 connection-finaliser warnings
    don't leak into pytest's unraisable-exception hook.
    """
    from app.downloader.progress import ProgressBus
    from app.logging_ import Logger
    from app.models import AppSettings
    from app.persistence.anime_list_repo import AnimeListEntryRepository
    from app.persistence.bilibili_cookie_repo import BilibiliCookieRepository
    from app.persistence.bt_feed_entry_repo import BtFeedEntryRepository
    from app.persistence.bt_feed_repo import BtFeedRepository
    from app.persistence.bt_filter_repo import BtFilterRepository
    from app.persistence.cookie_repo import CookieRepository
    from app.persistence.db import Database
    from app.persistence.paths import WorkspacePaths
    from app.persistence.putio_token_repo import PutioTokenRepository
    from app.persistence.repositories import AnimeRepository
    from app.persistence.settings_repo import SettingsRepository
    from app.persistence.sn_list_repo import SnListRepository
    from app.persistence.task_history_repo import TaskHistoryRepository
    from app.persistence.task_id_map_repo import TaskIdMapRepository
    from app.persistence.tg_downloaded_media_repo import TgDownloadedMediaRepository
    from app.persistence.tg_session_repo import TgSessionRepository
    from app.persistence.tg_watched_chat_repo import TgWatchedChatRepository
    from app.persistence.user_repo import UserRepository

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)

    # Seed a bangumi_dir / temp_dir under tmp_path so ``_normalise`` doesn't
    # rewrite them back to the fs defaults the user didn't choose.
    (tmp_path / 'bangumi').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'temp').mkdir(parents=True, exist_ok=True)

    # Seed a defaults ``config.json`` so ``settings_repo.load()`` works.
    defaults = AppSettings().model_dump(by_alias=True, exclude_none=False)
    paths.config_path.write_text(json.dumps(defaults, ensure_ascii=False, indent=4), encoding='utf-8')
    settings_repo = SettingsRepository(paths, logger)
    sn_list_repo = SnListRepository(paths, logger)
    cookie_repo = CookieRepository(paths, logger)
    bilibili_cookie_repo = BilibiliCookieRepository(paths)
    putio_token_repo = PutioTokenRepository(paths)

    database = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    database.run_baseline_migrations()
    anime_repo = AnimeRepository(database)
    user_repo = UserRepository(database)
    anime_list_entry_repo = AnimeListEntryRepository(database)
    task_history_repo = TaskHistoryRepository(database)
    task_id_map_repo = TaskIdMapRepository(database)
    bt_feed_repo = BtFeedRepository(database)
    bt_filter_repo = BtFilterRepository(database)
    bt_feed_entry_repo = BtFeedEntryRepository(database)
    tg_session_repo = TgSessionRepository(database)
    tg_watched_chat_repo = TgWatchedChatRepository(database)
    tg_downloaded_media_repo = TgDownloadedMediaRepository(database)

    progress_bus = ProgressBus()
    manual_runner = FakeManualRunner()

    container = FakeContainer(
        paths=paths,
        logger=logger,
        settings_repo=settings_repo,
        sn_list_repo=sn_list_repo,
        cookie_repo=cookie_repo,
        bilibili_cookie_repo=bilibili_cookie_repo,
        database=database,
        anime_repo=anime_repo,
        user_repo=user_repo,
        anime_list_entry_repo=anime_list_entry_repo,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
        progress_bus=progress_bus,
        manual_runner=manual_runner,
        putio_token_repo=putio_token_repo,
        bt_feed_repo=bt_feed_repo,
        bt_filter_repo=bt_filter_repo,
        bt_feed_entry_repo=bt_feed_entry_repo,
        tg_session_repo=tg_session_repo,
        tg_watched_chat_repo=tg_watched_chat_repo,
        tg_downloaded_media_repo=tg_downloaded_media_repo,
    )
    try:
        yield container
    finally:
        database.dispose()


# ---------------------------------------------------------------------------
# client — FastAPI TestClient with service deps overridden at fake_container.
# ---------------------------------------------------------------------------


@pytest.fixture
def client(
    fake_container: FakeContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> fastapi.testclient.TestClient:
    """FastAPI TestClient with every service bound to ``fake_container``.

    Sets ``ANIGAMERPLUS_DISABLE_SCHEDULER=1`` so the lifespan hook in
    :class:`app.main.DashboardApp` does not spawn a real background
    scheduler thread during the test run.

    Auth is bypassed by overriding ``current_user_opt`` with a function that
    always returns the sentinel admin (equivalent to ``auth.enabled=False``).
    This keeps every existing route test independent of the real session/DB
    auth layer while still exercising the full request path.
    """
    monkeypatch.setenv('ANIGAMERPLUS_DISABLE_SCHEDULER', '1')

    from app.api.deps import _SENTINEL_ADMIN, current_user_opt
    from app.api.health import HealthService, get_health_service
    from app.main import DashboardApp
    from app.services import (
        AnimeListService,
        ConfigService,
        ProgressService,
        SnListService,
        TaskService,
        get_animelist_service,
        get_config_service,
        get_progress_service,
        get_snlist_service,
        get_task_service,
    )

    # Build a dashboard app via a minimal Container-shaped proxy. We never
    # need the downloader collaborators here, so we leave them unset and
    # rely on dependency overrides to route every request at our fakes.
    container_proxy = _container_proxy(fake_container)
    app = DashboardApp(container_proxy).app

    config_service = ConfigService(fake_container.settings_repo)
    snlist_service = SnListService(fake_container.sn_list_repo)
    animelist_service = AnimeListService(
        fake_container.sn_list_repo,
        fake_container.anime_repo,
        fake_container.anime_list_entry_repo,
        fake_container.user_repo,
    )
    task_service = TaskService(
        fake_container.settings_repo,
        fake_container.manual_runner,
    )
    progress_service = ProgressService(
        fake_container.progress_bus,
        fake_container.user_repo,
    )
    health_service = HealthService(fake_container.paths)

    # Override every service dependency.
    app.dependency_overrides[get_config_service] = lambda: config_service
    app.dependency_overrides[get_snlist_service] = lambda: snlist_service
    app.dependency_overrides[get_animelist_service] = lambda: animelist_service
    app.dependency_overrides[get_task_service] = lambda: task_service
    app.dependency_overrides[get_progress_service] = lambda: progress_service
    app.dependency_overrides[get_health_service] = lambda: health_service

    # Auth bypass: always return the sentinel admin so auth.enabled=True
    # tests are not affected and all existing route tests keep passing.
    app.dependency_overrides[current_user_opt] = lambda: _SENTINEL_ADMIN

    return fastapi.testclient.TestClient(app)


def _container_proxy(fake: FakeContainer) -> Any:
    """Return an object with just enough attributes for :class:`DashboardApp`.

    :class:`DashboardApp` currently only reads ``container.settings_repo``
    and ``container.paths`` at run time; it doesn't touch any other field
    during FastAPI app construction. We build a tiny namespace and let
    dependency overrides take over for every actual endpoint.
    """
    import types

    proxy = types.SimpleNamespace(
        paths=fake.paths,
        logger=fake.logger,
        settings_repo=fake.settings_repo,
        sn_list_repo=fake.sn_list_repo,
        cookie_repo=fake.cookie_repo,
        bilibili_cookie_repo=fake.bilibili_cookie_repo,
        database=fake.database,
        anime_repo=fake.anime_repo,
        user_repo=fake.user_repo,
        anime_list_entry_repo=fake.anime_list_entry_repo,
        task_history_repo=fake.task_history_repo,
        task_id_map_repo=fake.task_id_map_repo,
        progress_bus=fake.progress_bus,
        manual_runner=fake.manual_runner,
        # telegram_client None by default; tests that need it override via
        # dependency_overrides.
        telegram_client=None,
        bilibili_runner=None,
    )
    return proxy

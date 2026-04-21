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

import asyncio
import collections.abc
import contextlib
import dataclasses
import json
import logging
import pathlib
import sys
from typing import Any

import fastapi.testclient
import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


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
# FakeSchedulerProxy — stand-in for SchedulerProxy in tests.
# ---------------------------------------------------------------------------


class FakeSchedulerProxy:
    """Captures enqueue/cancel calls; is_scheduler_up is controllable.

    ``enqueue_raises``: when set to a :class:`SchedulerUnreachable` instance,
    :meth:`enqueue_manual` raises it instead of recording a call.  This lets
    tests exercise the 503 path without the real HTTP client.
    """

    def __init__(self, *, up: bool = True) -> None:
        self._up = up
        self.enqueue_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[int] = []
        self.enqueue_raises: Exception | None = None

    def is_scheduler_up(self) -> bool:
        return self._up

    async def enqueue_manual(self, request: Any, owner_id: str) -> None:
        if self.enqueue_raises is not None:
            raise self.enqueue_raises
        self.enqueue_calls.append({'request': request, 'owner_id': owner_id})

    async def cancel_task(self, sn: int) -> None:
        self.cancel_calls.append(sn)

    def latest_snapshot(self) -> dict[int, Any]:
        return {}

    async def run_progress_subscription(self) -> None:
        await asyncio.sleep(9999)

    async def close(self) -> None:
        pass


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
    database: Any
    anime_repo: Any
    user_repo: Any
    anime_list_entry_repo: Any
    task_history_repo: Any
    progress_bus: Any
    manual_runner: FakeManualRunner
    # None = no proxy wired (tasks go in-process via manual_runner).
    scheduler_proxy: FakeSchedulerProxy | None = None


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
    from app.persistence.cookie_repo import CookieRepository
    from app.persistence.db import Database
    from app.persistence.paths import WorkspacePaths
    from app.persistence.repositories import AnimeRepository
    from app.persistence.settings_repo import SettingsRepository
    from app.persistence.sn_list_repo import SnListRepository
    from app.persistence.task_history_repo import TaskHistoryRepository
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

    database = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    database.run_baseline_migrations()
    anime_repo = AnimeRepository(database)
    user_repo = UserRepository(database)
    anime_list_entry_repo = AnimeListEntryRepository(database)
    task_history_repo = TaskHistoryRepository(database)

    progress_bus = ProgressBus()
    manual_runner = FakeManualRunner()

    container = FakeContainer(
        paths=paths,
        logger=logger,
        settings_repo=settings_repo,
        sn_list_repo=sn_list_repo,
        cookie_repo=cookie_repo,
        database=database,
        anime_repo=anime_repo,
        user_repo=user_repo,
        anime_list_entry_repo=anime_list_entry_repo,
        task_history_repo=task_history_repo,
        progress_bus=progress_bus,
        manual_runner=manual_runner,
        # Default: no proxy — tasks go in-process (existing test behaviour).
        scheduler_proxy=None,
    )
    try:
        yield container
    finally:
        database.dispose()


@pytest.fixture()
def fake_scheduler_proxy() -> FakeSchedulerProxy:
    """Return a :class:`FakeSchedulerProxy` with ``is_scheduler_up=True``."""
    return FakeSchedulerProxy(up=True)


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
        AuthService,
        ConfigService,
        ProgressService,
        SnListService,
        TaskService,
        get_animelist_service,
        get_auth_service,
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
        fake_container.scheduler_proxy,
    )
    progress_service = ProgressService(
        fake_container.progress_bus,
        fake_container.user_repo,
        fake_container.scheduler_proxy,
    )
    auth_service = AuthService(fake_container.settings_repo)
    health_service = HealthService(fake_container.paths)

    # Override every service dependency.
    app.dependency_overrides[get_config_service] = lambda: config_service
    app.dependency_overrides[get_snlist_service] = lambda: snlist_service
    app.dependency_overrides[get_animelist_service] = lambda: animelist_service
    app.dependency_overrides[get_task_service] = lambda: task_service
    app.dependency_overrides[get_progress_service] = lambda: progress_service
    app.dependency_overrides[get_auth_service] = lambda: auth_service
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
        database=fake.database,
        anime_repo=fake.anime_repo,
        user_repo=fake.user_repo,
        anime_list_entry_repo=fake.anime_list_entry_repo,
        task_history_repo=fake.task_history_repo,
        progress_bus=fake.progress_bus,
        manual_runner=fake.manual_runner,
        scheduler_proxy=fake.scheduler_proxy,
        # telegram_client None by default; tests that need it override via
        # dependency_overrides.
        telegram_client=None,
    )
    return proxy

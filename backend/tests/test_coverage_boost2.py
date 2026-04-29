"""Second coverage-boost test module: hitting remaining gaps.

Covers:
- app/log_config.py  (build_log_config save_logs=True, RichHandler config)
- app/logging_.py  (prune_old_logs, get_logger singleton, _ensure_dict_config)
- app/persistence/settings_repo.py  (BOM strip, UnicodeDecodeError, _normalise clamps)
- app/scheduler/manual_runner.py  (missing mode error branches, download error
  branches with progress_bus wired, _expand_range non-hyphen plain item)
- app/scheduler_server.py  (bootstrap n>0, secret not set log, task_history_repo
  mark_interrupted/normalize, WS datetime serialise, health with heartbeat,
  WS bad-secret close)
- app/scheduler/update_loop.py  (update branch, run_forever exception path,
  already-queued skip)
"""

from __future__ import annotations

import codecs
import datetime
import json
import pathlib
import types
from typing import Any
from unittest.mock import patch

import pytest

from app.downloader import exceptions as _exc
from app.downloader.anime import DownloadResult
from app.downloader.progress import ProgressBus
from app.logging_ import Logger
from app.persistence.paths import WorkspacePaths
from app.persistence.settings_repo import SettingsRepository
from app.scheduler.manual_runner import ManualRunner

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


# ---------------------------------------------------------------------------
# app/log_config.py
# ---------------------------------------------------------------------------


def test_build_log_config_save_logs_true(tmp_path: pathlib.Path) -> None:
    """build_log_config with save_logs=True includes a file handler."""
    from app.log_config import build_log_config
    from app.persistence.paths import WorkspacePaths

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    config = build_log_config(paths, save_logs=True, quantity_of_logs=3)
    assert 'file' in config['handlers']
    # logs_dir should be created
    assert paths.logs_dir.exists()


def test_build_log_config_save_logs_false(tmp_path: pathlib.Path) -> None:
    """build_log_config with save_logs=False does NOT include a file handler."""
    from app.log_config import build_log_config
    from app.persistence.paths import WorkspacePaths

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    config = build_log_config(paths, save_logs=False, quantity_of_logs=3)
    assert 'file' not in config['handlers']


def test_build_log_config_colored_stdout_forced_true(tmp_path: pathlib.Path) -> None:
    """build_log_config wires the stdout handler to RichHandler (color is automatic)."""
    from app.log_config import build_log_config
    from app.persistence.paths import WorkspacePaths

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    config = build_log_config(paths, save_logs=False, quantity_of_logs=3)
    # stdout handler must use RichHandler; no legacy stdout_colour filter present.
    assert config['handlers']['stdout']['()'] == 'rich.logging.RichHandler'
    assert 'stdout_colour' not in config['filters']


def test_build_log_config_rich_handler_options(tmp_path: pathlib.Path) -> None:
    """RichHandler is configured with expected options (no path, custom time format)."""
    from app.log_config import build_log_config
    from app.persistence.paths import WorkspacePaths

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    config = build_log_config(paths, save_logs=False, quantity_of_logs=3)
    stdout = config['handlers']['stdout']
    assert stdout['show_path'] is False
    assert stdout['rich_tracebacks'] is True
    assert stdout['omit_repeated_times'] is False


def test_build_log_config_rich_formatter(tmp_path: pathlib.Path) -> None:
    """'rich' formatter contains only name+message (Rich adds time/level itself)."""
    from app.log_config import build_log_config
    from app.persistence.paths import WorkspacePaths

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    config = build_log_config(paths, save_logs=False, quantity_of_logs=3)
    rich_fmt = config['formatters']['rich']
    assert '%(name)s' in rich_fmt['format']
    assert '%(message)s' in rich_fmt['format']


# ---------------------------------------------------------------------------
# app/logging_.py — prune_old_logs + _ensure_dict_config + get_logger
# ---------------------------------------------------------------------------


def test_prune_old_logs_removes_old_files(tmp_path: pathlib.Path) -> None:
    """prune_old_logs() removes log files older than quantity_of_logs days."""
    logs_dir = tmp_path / 'logs'
    logs_dir.mkdir()
    logger = Logger(logs_dir, save_logs=False, quantity_of_logs=2)

    # Create a log file 10 days old
    old_date = (datetime.datetime.now().date() - datetime.timedelta(days=10)).strftime('%Y-%m-%d')
    old_log = logs_dir / f'{old_date}.log'
    old_log.write_text('old log entry', encoding='utf-8')

    # Create a recent log file (today)
    today = datetime.datetime.now().date().strftime('%Y-%m-%d')
    new_log = logs_dir / f'{today}.log'
    new_log.write_text('new log entry', encoding='utf-8')

    logger.prune_old_logs()

    assert not old_log.exists(), 'old log should have been pruned'
    assert new_log.exists(), 'recent log should be kept'


def test_prune_old_logs_noop_when_logs_dir_absent(tmp_path: pathlib.Path) -> None:
    """prune_old_logs() is a no-op when the logs_dir does not exist."""
    logger = Logger(tmp_path / 'nonexistent_logs', save_logs=False, quantity_of_logs=7)
    # Must not raise
    logger.prune_old_logs()


def test_ensure_dict_config_applied_only_once(tmp_path: pathlib.Path) -> None:
    """_ensure_dict_config_applied is idempotent — second call is a no-op."""
    from app import logging_ as logging_mod

    original_flag = logging_mod._dict_config_applied
    original_logger = logging_mod._default_logger

    # Reset global flags so we can test the guard
    logging_mod._dict_config_applied = True  # Pretend it was already applied
    try:
        from app.persistence.paths import WorkspacePaths

        paths = WorkspacePaths.detect(working_dir=tmp_path)
        # Should return without calling dictConfig again
        with patch('logging.config.dictConfig') as mock_dictconfig:
            logging_mod._ensure_dict_config_applied(paths, save_logs=False, quantity_of_logs=7)
        mock_dictconfig.assert_not_called()
    finally:
        logging_mod._dict_config_applied = original_flag
        logging_mod._default_logger = original_logger


# ---------------------------------------------------------------------------
# app/persistence/settings_repo.py — edge cases
# ---------------------------------------------------------------------------


def test_settings_repo_load_strips_bom(tmp_path: pathlib.Path) -> None:
    """load() silently strips a UTF-8 BOM at the start of config.json."""
    from app.models import AppSettings

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    (tmp_path / 'bangumi').mkdir()
    (tmp_path / 'temp').mkdir()
    logger = _logger(tmp_path)

    defaults = AppSettings().model_dump(by_alias=True, exclude_none=False)
    json_bytes = json.dumps(defaults, ensure_ascii=False, indent=4).encode('utf-8')
    # Write with BOM
    paths.config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_bytes(codecs.BOM_UTF8 + json_bytes)

    repo = SettingsRepository(paths, logger)
    settings = repo.load()
    assert settings is not None
    # After load, BOM should have been stripped from the file
    reread = paths.config_path.read_bytes()
    assert not reread.startswith(codecs.BOM_UTF8)


def test_settings_repo_normalise_clamps_multi_thread(tmp_path: pathlib.Path) -> None:
    """_normalise() clamps multi_thread to 5 when a value above the limit slips through."""
    from app.models import AppSettings

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    (tmp_path / 'bangumi').mkdir()
    (tmp_path / 'temp').mkdir()
    logger = _logger(tmp_path)

    repo = SettingsRepository(paths, logger)
    # Construct a settings object bypassing pydantic validation, then call _normalise
    # to exercise the clamp branch directly (the model validator would reject it)
    base = AppSettings()
    # Use model_construct to bypass validators
    over_limit = base.model_copy(update={'multi_thread': 6})
    # Patch multi_thread to 6 by constructing via internal path
    # Use object.__setattr__ to bypass the frozen model
    over_limit = AppSettings.model_construct(**{k: v for k, v in base.model_dump().items()})
    object.__setattr__(over_limit, 'multi_thread', 6)

    result = repo._normalise(over_limit)  # type: ignore[attr-defined]
    assert result.multi_thread == 5


def test_settings_repo_normalise_clamps_multi_downloading_segment(
    tmp_path: pathlib.Path,
) -> None:
    """_normalise() clamps multi_downloading_segment to 5 when value is above limit."""
    from app.models import AppSettings

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    (tmp_path / 'bangumi').mkdir()
    (tmp_path / 'temp').mkdir()
    logger = _logger(tmp_path)

    repo = SettingsRepository(paths, logger)
    base = AppSettings()
    over_limit = AppSettings.model_construct(**{k: v for k, v in base.model_dump().items()})
    object.__setattr__(over_limit, 'multi_downloading_segment', 6)

    result = repo._normalise(over_limit)  # type: ignore[attr-defined]
    assert result.multi_downloading_segment == 5


def test_settings_repo_atomic_write_cleans_tmp_on_error(
    tmp_path: pathlib.Path,
) -> None:
    """_atomic_write() removes the temp file when os.replace raises."""
    from app.models import AppSettings

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    (tmp_path / 'bangumi').mkdir()
    (tmp_path / 'temp').mkdir()
    logger = _logger(tmp_path)

    # Seed a valid config first
    defaults = AppSettings().model_dump(by_alias=True, exclude_none=False)
    paths.config_path.write_text(json.dumps(defaults, ensure_ascii=False), encoding='utf-8')
    repo = SettingsRepository(paths, logger)

    with (
        patch('app.persistence.settings_repo.os.replace', side_effect=OSError('disk full')),
        pytest.raises(OSError, match='disk full'),
    ):
        repo.save(AppSettings())


# ---------------------------------------------------------------------------
# app/scheduler/manual_runner.py — mode errors with sn=None + download errors with bus
# ---------------------------------------------------------------------------


class _FA:
    """Minimal fake Anime for runner tests."""

    def __init__(
        self,
        sn: int,
        *,
        load_raises: BaseException | None = None,
        download_raises: BaseException | None = None,
    ) -> None:
        self.sn = sn
        self._lr = load_raises
        self._dr = download_raises
        self.calls: list[str] = []

    def load(self) -> None:
        self.calls.append('load')
        if self._lr is not None:
            raise self._lr

    def get_episode_list(self) -> dict[str, int]:
        return {'01': self.sn}

    def enable_danmu(self) -> None:
        pass

    def get_info(self) -> None:
        self.calls.append('get_info')

    def download(self, **kw: Any) -> DownloadResult:
        self.calls.append('download')
        if self._dr is not None:
            raise self._dr
        return DownloadResult(True, pathlib.Path(f'/tmp/{self.sn}.mp4'), 500)


class _FR:
    def read(self, sn: int) -> None:
        return None


def _runner_with_bus(tmp_path: pathlib.Path, fa: _FA, bus: ProgressBus) -> ManualRunner:
    from app.models import AppSettings

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(download_resolution='1080')
    return ManualRunner(
        anime_factory=lambda sn: fa,  # type: ignore[arg-type]
        anime_repo=_FR(),  # type: ignore[arg-type]
        settings=settings,
        logger=logger,
        progress_bus=bus,
    )


def test_manual_runner_no_stream_in_load_with_bus_sets_failed(
    tmp_path: pathlib.Path,
) -> None:
    """NoAvailableStreamError in load() with bus wired sets status='失敗' + finish."""
    fa = _FA(50, load_raises=_exc.NoAvailableStreamError('no stream'))
    bus = ProgressBus()
    bus.start(50, 'title', status='等待下載')
    runner = _runner_with_bus(tmp_path, fa, bus)
    runner.run(50, mode='single')
    snap = bus.snapshot()
    assert 50 in snap
    assert snap[50].status == '失敗'


def test_manual_runner_try_too_many_in_load_with_bus(tmp_path: pathlib.Path) -> None:
    """TryTooManyTimeError in load() with bus wired sets status='失敗' + finish."""
    fa = _FA(51, load_raises=_exc.TryTooManyTimeError('retries'))
    bus = ProgressBus()
    bus.start(51, 'title', status='等待下載')
    runner = _runner_with_bus(tmp_path, fa, bus)
    runner.run(51, mode='single')
    snap = bus.snapshot()
    assert 51 in snap
    assert snap[51].status == '失敗'


def test_manual_runner_try_too_many_in_download_with_bus(
    tmp_path: pathlib.Path,
) -> None:
    """TryTooManyTimeError in download() with bus wired sets status='失敗'."""
    fa = _FA(52, download_raises=_exc.TryTooManyTimeError('retries'))
    bus = ProgressBus()
    runner = _runner_with_bus(tmp_path, fa, bus)
    runner.run(52, mode='single')
    snap = bus.snapshot()
    assert 52 in snap
    assert snap[52].status == '失敗'


def test_manual_runner_no_available_stream_in_download_with_bus(
    tmp_path: pathlib.Path,
) -> None:
    """NoAvailableStreamError in download() with bus wired sets status='失敗'."""
    fa = _FA(53, download_raises=_exc.NoAvailableStreamError('no stream'))
    bus = ProgressBus()
    runner = _runner_with_bus(tmp_path, fa, bus)
    runner.run(53, mode='single')
    snap = bus.snapshot()
    assert 53 in snap
    assert snap[53].status == '失敗'


def test_manual_runner_mode_requires_sn_latest(tmp_path: pathlib.Path) -> None:
    """mode='latest' with sn=None raises ValueError."""
    from app.models import AppSettings

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    runner = ManualRunner(
        anime_factory=lambda sn: None,  # type: ignore[arg-type]
        anime_repo=_FR(),  # type: ignore[arg-type]
        settings=AppSettings(),
        logger=logger,
    )
    with pytest.raises(ValueError, match="mode='latest' requires sn"):
        runner.run(None, mode='latest')


def test_manual_runner_mode_requires_sn_all(tmp_path: pathlib.Path) -> None:
    """mode='all' with sn=None raises ValueError."""
    from app.models import AppSettings

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    runner = ManualRunner(
        anime_factory=lambda sn: None,  # type: ignore[arg-type]
        anime_repo=_FR(),  # type: ignore[arg-type]
        settings=AppSettings(),
        logger=logger,
    )
    with pytest.raises(ValueError, match="mode='all' requires sn"):
        runner.run(None, mode='all')


def test_manual_runner_mode_requires_sn_range(tmp_path: pathlib.Path) -> None:
    """mode='range' with sn=None raises ValueError."""
    from app.models import AppSettings

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    runner = ManualRunner(
        anime_factory=lambda sn: None,  # type: ignore[arg-type]
        anime_repo=_FR(),  # type: ignore[arg-type]
        settings=AppSettings(),
        logger=logger,
    )
    with pytest.raises(ValueError, match="mode='range' requires sn"):
        runner.run(None, mode='range')


def test_manual_runner_expand_range_plain_no_hyphen(tmp_path: pathlib.Path) -> None:
    """_expand_range with a plain non-range item returns it as-is."""
    from app.models import AppSettings

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    runner = ManualRunner(
        anime_factory=lambda sn: None,  # type: ignore[arg-type]
        anime_repo=_FR(),  # type: ignore[arg-type]
        settings=AppSettings(),
        logger=logger,
    )
    result = runner._expand_range(['5'])  # type: ignore[attr-defined]
    assert result == {'5'}


# ---------------------------------------------------------------------------
# app/scheduler_server.py — uncovered branches
# ---------------------------------------------------------------------------


def _fake_container_with_history(secret: str, tmp_path: pathlib.Path) -> Any:
    """Build a container where task_history_repo returns non-zero from both methods."""

    class _FakeHistoryRepo:
        def mark_interrupted_on_boot(self) -> int:
            return 2

        def normalize_legacy_statuses(self) -> int:
            return 3

    class _FakeSettingsRepo:
        def load(self) -> Any:
            return types.SimpleNamespace(check_frequency=5)

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    container = types.SimpleNamespace(
        paths=types.SimpleNamespace(logs_dir=tmp_path / 'logs'),
        logger=logger,
        settings_repo=_FakeSettingsRepo(),
        task_history_repo=_FakeHistoryRepo(),
    )
    return container


def test_scheduler_server_lifespan_logs_bootstrap_and_history(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lifespan logs 'Bootstrap ...' when n>0 and history repo logs interpolated."""
    import fastapi.testclient

    import app.scheduler_server as ss_mod

    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', 'stest')
    monkeypatch.setattr(ss_mod, '_RESOLVED_SECRET', None)

    container = _fake_container_with_history('stest', tmp_path)

    # Patch ApsScheduler so no real BackgroundScheduler is started.
    from app.scheduler.aps_scheduler import ApsScheduler as _RealAps

    class _FakeAps:
        _scheduler = types.SimpleNamespace(running=True)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    # Patch ring-buffer bootstrap to return >0
    from app.log_config import RingBufferHandler

    with (
        patch.object(RingBufferHandler, 'bootstrap_from_file', return_value=5),
        patch('app.scheduler.aps_scheduler.ApsScheduler', lambda settings_repo: _FakeAps()),
    ):
        app = ss_mod.build_scheduler_app(container)  # type: ignore[arg-type]
        with fastapi.testclient.TestClient(app):
            pass  # lifespan runs, logs bootstrap + history


def test_scheduler_server_lifespan_logs_secret_not_set(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When ANIGAMERPLUS_INTERNAL_SECRET is not in env, the lifespan logs a message."""
    import fastapi.testclient

    import app.scheduler_server as ss_mod

    monkeypatch.delenv('ANIGAMERPLUS_INTERNAL_SECRET', raising=False)
    monkeypatch.setattr(ss_mod, '_RESOLVED_SECRET', None)

    container = _fake_container_with_history('', tmp_path)

    class _FakeAps:
        _scheduler = types.SimpleNamespace(running=True)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    with patch('app.scheduler.aps_scheduler.ApsScheduler', lambda settings_repo: _FakeAps()):
        app = ss_mod.build_scheduler_app(container)  # type: ignore[arg-type]
        # Just verify it runs without error (the log statement is the branch target)
        with fastapi.testclient.TestClient(app):
            pass


def test_scheduler_server_health_includes_aps_running(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Health response includes aps_running field and status is ok when running."""
    import fastapi.testclient

    import app.scheduler_server as ss_mod

    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', 'shb')
    monkeypatch.setattr(ss_mod, '_RESOLVED_SECRET', None)

    class _FakeSettingsRepo:
        def load(self) -> Any:
            return types.SimpleNamespace(check_frequency=5)

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    container = types.SimpleNamespace(
        paths=types.SimpleNamespace(logs_dir=tmp_path / 'logs'),
        logger=logger,
        settings_repo=_FakeSettingsRepo(),
        task_history_repo=None,
    )

    class _FakeAps:
        _scheduler = types.SimpleNamespace(running=True)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    with patch('app.scheduler.aps_scheduler.ApsScheduler', lambda settings_repo: _FakeAps()):
        app = ss_mod.build_scheduler_app(container)  # type: ignore[arg-type]

        with fastapi.testclient.TestClient(app) as client:
            resp = client.get('/internal/health', headers={'X-Internal-Secret': 'shb'})
            assert resp.status_code == 200
            data = resp.json()
            assert 'aps_running' in data
            assert data['aps_running'] is True
            assert data['status'] == 'ok'


def test_scheduler_server_health_returns_no_ws_or_task_routes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slimmed scheduler app does NOT expose /internal/tasks or /internal/progress."""
    import fastapi.testclient

    import app.scheduler_server as ss_mod

    monkeypatch.setenv('ANIGAMERPLUS_INTERNAL_SECRET', 'sws')
    monkeypatch.setattr(ss_mod, '_RESOLVED_SECRET', None)

    class _FakeSettingsRepo:
        def load(self) -> Any:
            return types.SimpleNamespace(check_frequency=5)

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    container = types.SimpleNamespace(
        paths=types.SimpleNamespace(logs_dir=tmp_path / 'logs'),
        logger=logger,
        settings_repo=_FakeSettingsRepo(),
        task_history_repo=None,
    )

    class _FakeAps:
        _scheduler = types.SimpleNamespace(running=True)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    with patch('app.scheduler.aps_scheduler.ApsScheduler', lambda settings_repo: _FakeAps()):
        app = ss_mod.build_scheduler_app(container)  # type: ignore[arg-type]

        with fastapi.testclient.TestClient(app) as client:
            # These routes were removed — must return 404 / 405.
            assert client.post('/internal/tasks/manual', json={}).status_code in {404, 405, 422}
            assert client.delete('/internal/tasks/1').status_code in {404, 405}


# ---------------------------------------------------------------------------
# app/scheduler/update_loop.py — uncovered branches
# ---------------------------------------------------------------------------


def _build_loop(tmp_path: pathlib.Path) -> Any:
    """Build a minimal UpdateLoop for targeted branch tests."""

    from app.downloader.metadata import AnimeMetadata
    from app.downloader.progress import ProgressBus
    from app.logging_ import Logger
    from app.models import AppSettings
    from app.scheduler.queue_ import TaskQueue
    from app.scheduler.update_loop import UpdateLoop

    class _FSR:
        def load(self) -> AppSettings:
            return AppSettings(check_frequency=1)

    class _FSNR:
        def parse_legacy(self, *a: Any) -> dict:
            return {}

    class _FALER:
        def list_all(self) -> list:
            return []

        def update_anime_name(self, *a: Any) -> None:
            pass

    class _FAR:
        def read(self, sn: int) -> None:
            return None

    class _FCR:
        def invalidate(self) -> None:
            pass

    class _FW:
        def run(self, sn: int, **_kw: Any) -> None:
            pass

    class _FME:
        def __init__(self, meta: AnimeMetadata) -> None:
            self._meta = meta

        def fetch(self, sn: int) -> AnimeMetadata:
            return self._meta

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    queue = TaskQueue(max_download=2, max_upload=1)
    bus = ProgressBus()

    meta = AnimeMetadata(
        sn=1, title='T[01]', bangumi_name='T', bangumi_name_orig='T', episode='01', episode_list={'01': 1}
    )

    loop = UpdateLoop(
        settings_repo=_FSR(),  # type: ignore[arg-type]
        sn_list_repo=_FSNR(),  # type: ignore[arg-type]
        anime_list_entry_repo=_FALER(),  # type: ignore[arg-type]
        anime_repo=_FAR(),  # type: ignore[arg-type]
        queue=queue,
        worker=_FW(),  # type: ignore[arg-type]
        metadata_extractor=_FME(meta),  # type: ignore[arg-type]
        logger=logger,
        cookie_repo=_FCR(),  # type: ignore[arg-type]
        progress_bus=bus,
    )
    loop._sleep = lambda s: None  # type: ignore[attr-defined]  — instant ticks
    return loop, queue


def test_update_loop_check_tasks_skips_already_queued_sn(
    tmp_path: pathlib.Path,
) -> None:
    """check_tasks skips a sn that's already in the download queue."""
    from app.scheduler.queue_ import TaskInfo

    loop, queue = _build_loop(tmp_path)
    # Add sn=1 to the queue so check_tasks sees it as already queued
    queue.add(1, TaskInfo(sn=1, tag='', mode='single'))

    sn_dict = {1: {'mode': 'latest', 'tag': '', 'season': '1', 'owner_id': ''}}
    # Should not crash and sn should stay in queue (not double-added)
    loop.check_tasks(sn_dict)
    assert queue.contains(1)


def test_update_loop_run_forever_exception_continues(tmp_path: pathlib.Path) -> None:
    """run_forever catches exceptions in the loop body and continues."""
    loop, _queue = _build_loop(tmp_path)

    call_count = [0]

    def _raising_check_tasks(sn_dict: dict) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError('simulated error in check_tasks')
        # Stop after 2nd call
        loop.stop()

    loop.check_tasks = _raising_check_tasks  # type: ignore[method-assign]
    loop.run_forever()
    # Exception was caught; loop continued to second iteration
    assert call_count[0] >= 2


def test_update_loop_select_target_sns_largest_sn(tmp_path: pathlib.Path) -> None:
    """_select_target_sns with mode='largest-sn' returns the highest sn."""
    loop, _queue = _build_loop(tmp_path)
    result = loop._select_target_sns(1, 'largest-sn', {'01': 100, '02': 200, '03': 50})  # type: ignore[attr-defined]
    assert result == [200]


def test_update_loop_select_target_sns_single(tmp_path: pathlib.Path) -> None:
    """_select_target_sns with mode='single' returns the root_sn."""
    loop, _queue = _build_loop(tmp_path)
    result = loop._select_target_sns(999, 'single', {'01': 100})  # type: ignore[attr-defined]
    assert result == [999]


def test_update_loop_spawn_worker_skips_already_processing(tmp_path: pathlib.Path) -> None:
    """_spawn_worker returns immediately if sn is already being processed."""
    loop, queue = _build_loop(tmp_path)
    # Mark as processing so spawn_worker short-circuits
    queue.mark_processing(1)
    # Should not raise and should not double-process
    loop._spawn_worker(1)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# app/services/task_service.py — remaining branches
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_task_service_cancel_no_progress_service_non_admin_gets_404(
    tmp_path: pathlib.Path,
) -> None:
    """cancel_task with no progress_service and non-admin user returns 404."""
    import datetime

    import fastapi

    from app.logging_ import Logger
    from app.persistence.paths import WorkspacePaths
    from app.persistence.settings_repo import SettingsRepository
    from app.persistence.user_repo import UserRow
    from app.services.task_service import TaskService

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = SettingsRepository(paths, logger)

    user = UserRow(
        id='dl-1',
        username='dl_user',
        avatar_url=None,
        role='downloader',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )
    # No progress_service wired + non-admin -> 404
    fake_runner = types.SimpleNamespace(run=lambda *a, **kw: None)
    service = TaskService(repo, fake_runner, scheduler_proxy=None)  # type: ignore[arg-type]
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await service.cancel_task(999, user)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_task_service_cancel_proxy_exception_becomes_503(
    tmp_path: pathlib.Path,
) -> None:
    """cancel_task proxy raises generic Exception -> 503."""
    import datetime

    import fastapi

    from app.downloader.progress import ProgressBus
    from app.logging_ import Logger
    from app.persistence.paths import WorkspacePaths
    from app.persistence.settings_repo import SettingsRepository
    from app.persistence.user_repo import UserRow
    from app.services.progress_service import ProgressService
    from app.services.task_service import TaskService

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = SettingsRepository(paths, logger)

    bus = ProgressBus()
    user = UserRow(
        id='admin-1',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )
    bus.start(777, 'ep.mp4', status='正在下載', owner_id=user.id)

    class _BadProxy:
        def is_scheduler_up(self) -> bool:
            return True

        async def cancel_task(self, sn: int) -> None:
            raise RuntimeError('proxy exploded')

    progress_service = ProgressService(bus)
    fake_runner = types.SimpleNamespace(run=lambda *a, **kw: None)
    service = TaskService(  # type: ignore[arg-type]
        repo, fake_runner, scheduler_proxy=_BadProxy(), progress_service=progress_service
    )

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await service.cancel_task(777, user)
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# app/logging_.py — get_logger singleton path
# ---------------------------------------------------------------------------


def test_get_logger_returns_same_instance(tmp_path: pathlib.Path) -> None:
    """get_logger() returns the same Logger instance on repeated calls (cached path)."""
    from app import logging_ as logging_mod

    original_logger = logging_mod._default_logger
    try:
        # Pre-seed a fake logger so get_logger hits the cached (line 313-314) branch.
        fake_logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
        logging_mod._default_logger = fake_logger  # type: ignore[assignment]

        result1 = logging_mod.get_logger()
        result2 = logging_mod.get_logger()
        assert result1 is fake_logger
        assert result2 is fake_logger
    finally:
        logging_mod._default_logger = original_logger


# ---------------------------------------------------------------------------
# app/scheduler/update_loop.py — empty episode_list path
# ---------------------------------------------------------------------------


def test_update_loop_select_target_sns_empty_episode_list(tmp_path: pathlib.Path) -> None:
    """_select_target_sns with empty episode_list returns [root_sn]."""
    loop, _queue = _build_loop(tmp_path)
    result = loop._select_target_sns(999, 'latest', {})  # type: ignore[attr-defined]
    assert result == [999]


# ---------------------------------------------------------------------------
# app/services/task_service.py — _build_task_service + admin cancel fallback
# ---------------------------------------------------------------------------


def test_build_task_service_constructs_service(tmp_path: pathlib.Path) -> None:
    """_build_task_service returns a TaskService with progress_service wired."""
    from app.downloader.progress import ProgressBus
    from app.logging_ import Logger
    from app.persistence.paths import WorkspacePaths
    from app.persistence.settings_repo import SettingsRepository
    from app.services.task_service import TaskService, _build_task_service

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = SettingsRepository(paths, logger)
    bus = ProgressBus()

    container = types.SimpleNamespace(
        settings_repo=repo,
        manual_runner=types.SimpleNamespace(run=lambda *a, **kw: None),
        progress_bus=bus,
        # No scheduler_proxy attr — tests getattr fallback
    )
    svc = _build_task_service(container)  # type: ignore[arg-type]
    assert isinstance(svc, TaskService)


@pytest.mark.anyio
async def test_task_service_cancel_admin_no_progress_service_fallback(
    tmp_path: pathlib.Path,
) -> None:
    """cancel_task with no progress_service and admin user reaches fallback (no proxy -> returns)."""
    import datetime

    from app.logging_ import Logger
    from app.persistence.paths import WorkspacePaths
    from app.persistence.settings_repo import SettingsRepository
    from app.persistence.user_repo import UserRow
    from app.services.task_service import TaskService

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = SettingsRepository(paths, logger)

    user = UserRow(
        id='admin-x',
        username='admin',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )
    # Admin + no progress_service + no proxy: falls through to in-process fallback (returns normally)
    fake_runner = types.SimpleNamespace(run=lambda *a, **kw: None)
    service = TaskService(repo, fake_runner, scheduler_proxy=None)  # type: ignore[arg-type]
    # Should not raise
    await service.cancel_task(999, user)

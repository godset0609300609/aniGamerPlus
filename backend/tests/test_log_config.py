"""Tests for :mod:`app.log_config` — focuses on the ring-buffer,
the panel allowlist filter, and related helpers.

Specifically:
* ``RingBufferHandler._format()`` must return ``record.getMessage()`` as the
  ``message`` field — NOT the full pre-formatted line that embeds timestamp
  and level prefix (which would appear twice in the UI).
* ``_PanelAllowlistFilter`` must pass ``app.*`` INFO unconditionally, and for
  every other logger name only pass WARNING or above.
* The ring buffer must receive records with ``display=False`` (lifecycle
  events like 自動掃描) — the stdout_display filter must NOT be wired to it.
* The file handler must still receive httpx records when save_logs=True.
"""

from __future__ import annotations

import logging
import logging.config
import pathlib

import pytest

import app.log_config as _lc
from app.log_config import (
    DailyLogFileHandler,
    RingBufferHandler,
    _CliAuditNoiseFilter,
    _DisplayFilter,
    _PanelAllowlistFilter,
    _UvicornWsNoiseFilter,
    build_log_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    name: str = 'app.main',
    msg: str = 'hello world',
    level: int = logging.INFO,
) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname='',
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )


def _make_handler_with_formatter() -> RingBufferHandler:
    """Return a handler with the full ``default`` formatter attached —
    simulating what ``build_log_config`` wires in production."""
    handler = RingBufferHandler()
    # Attach the same format string used by LOG_FORMAT / DATE_FORMAT so we can
    # confirm _format() ignores it and uses getMessage() instead.
    formatter = logging.Formatter(
        fmt='%(asctime)s  %(levelname)-5s  %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    handler.setFormatter(formatter)
    return handler


# ---------------------------------------------------------------------------
# Issue 2: _format() must use record.getMessage(), not self.format(record)
# ---------------------------------------------------------------------------


def test_format_returns_raw_message_without_formatter() -> None:
    """_format() returns the raw message when NO formatter is attached."""
    handler = RingBufferHandler()
    record = _make_record(msg='raw message')
    entry = handler._format(record)
    assert entry['message'] == 'raw message'


def test_format_returns_raw_message_with_formatter_attached() -> None:
    """_format() must return record.getMessage() even when a formatter IS
    attached — NOT the full pre-formatted line containing timestamp + level."""
    handler = _make_handler_with_formatter()
    record = _make_record(msg='scheduler disconnected')
    entry = handler._format(record)

    # The raw message must be returned — no timestamp or level prefix.
    assert entry['message'] == 'scheduler disconnected'
    # Negative: the full formatted line from self.format() would contain
    # the date and level.  If that leaked in, the message would start with
    # a year or contain "INFO".
    assert not entry['message'].startswith('20'), (
        'message must not start with a timestamp (would duplicate prefix in UI)'
    )
    assert 'INFO' not in entry['message'], (
        'message must not embed the log level (already in the level field)'
    )


def test_format_message_with_percent_formatting() -> None:
    """getMessage() resolves %-style args correctly."""
    handler = RingBufferHandler()
    record = logging.LogRecord(
        name='app.test',
        level=logging.INFO,
        pathname='',
        lineno=0,
        msg='value is %d',
        args=(42,),
        exc_info=None,
    )
    entry = handler._format(record)
    assert entry['message'] == 'value is 42'


def test_format_includes_correct_level_and_name() -> None:
    """Structured fields (level, name) are still populated correctly."""
    handler = _make_handler_with_formatter()
    record = _make_record(name='app.scheduler', msg='test msg', level=logging.WARNING)
    entry = handler._format(record)
    assert entry['level'] == 'WARNING'
    assert entry['name'] == 'app.scheduler'


# ---------------------------------------------------------------------------
# _PanelAllowlistFilter unit tests
# ---------------------------------------------------------------------------


class TestPanelAllowlistFilter:
    def setup_method(self) -> None:
        self.f = _PanelAllowlistFilter()

    # --- app.* always passes ---

    def test_panel_allowlist_allows_app_main_info(self) -> None:
        """app.main INFO must pass through."""
        assert self.f.filter(_make_record('app.main', level=logging.INFO)) is True

    def test_panel_allowlist_allows_app_nested_info(self) -> None:
        """Deeply nested app.* loggers must pass through at INFO."""
        assert self.f.filter(_make_record('app.api.foo', level=logging.INFO)) is True
        assert self.f.filter(_make_record('app.downloader.bar', level=logging.INFO)) is True

    def test_panel_allowlist_allows_bare_app_info(self) -> None:
        """The bare 'app' logger must pass through at INFO."""
        assert self.f.filter(_make_record('app', level=logging.INFO)) is True

    # --- non-app INFO is dropped ---

    def test_panel_allowlist_drops_uvicorn_access_info(self) -> None:
        """uvicorn.access INFO must be dropped."""
        assert self.f.filter(_make_record('uvicorn.access', level=logging.INFO)) is False

    def test_panel_allowlist_drops_uvicorn_error_info(self) -> None:
        """uvicorn.error INFO (startup msg, connection lifecycle) must be dropped."""
        assert self.f.filter(_make_record('uvicorn.error', level=logging.INFO)) is False

    def test_panel_allowlist_drops_httpx_info(self) -> None:
        """httpx INFO must be dropped."""
        assert self.f.filter(_make_record('httpx', level=logging.INFO)) is False

    def test_panel_allowlist_drops_alembic_info(self) -> None:
        """alembic.runtime.migration INFO must be dropped."""
        assert self.f.filter(_make_record('alembic.runtime.migration', level=logging.INFO)) is False

    def test_panel_allowlist_drops_uvicorn_root_info(self) -> None:
        """The bare 'uvicorn' logger INFO must be dropped."""
        assert self.f.filter(_make_record('uvicorn', level=logging.INFO)) is False

    # --- non-app WARNING/ERROR/CRITICAL pass ---

    def test_panel_allowlist_allows_uvicorn_error_warning(self) -> None:
        """uvicorn.error WARNING must pass through."""
        assert self.f.filter(_make_record('uvicorn.error', level=logging.WARNING)) is True

    def test_panel_allowlist_allows_alembic_error(self) -> None:
        """alembic ERROR must pass through."""
        assert self.f.filter(_make_record('alembic.runtime.migration', level=logging.ERROR)) is True

    def test_panel_allowlist_allows_httpx_warning(self) -> None:
        """httpx WARNING must pass through."""
        assert self.f.filter(_make_record('httpx', level=logging.WARNING)) is True


# ---------------------------------------------------------------------------
# _CliAuditNoiseFilter unit tests
# ---------------------------------------------------------------------------


class TestCliAuditNoiseFilter:
    def setup_method(self) -> None:
        self.f = _CliAuditNoiseFilter()

    def test_drops_uvicorn_access_info(self) -> None:
        """uvicorn.access INFO must be dropped (per-request spam)."""
        assert self.f.filter(_make_record('uvicorn.access', level=logging.INFO)) is False

    def test_drops_httpx_info(self) -> None:
        """httpx INFO must be dropped (health poll spam)."""
        assert self.f.filter(_make_record('httpx', level=logging.INFO)) is False

    def test_drops_httpcore_info(self) -> None:
        """httpcore INFO must be dropped (health poll spam)."""
        assert self.f.filter(_make_record('httpcore', level=logging.INFO)) is False

    def test_allows_uvicorn_access_warning(self) -> None:
        """uvicorn.access WARNING must pass through (real error)."""
        assert self.f.filter(_make_record('uvicorn.access', level=logging.WARNING)) is True

    def test_allows_uvicorn_error_info(self) -> None:
        """uvicorn.error INFO (e.g. 'Uvicorn running on ...') must pass through."""
        assert self.f.filter(_make_record('uvicorn.error', level=logging.INFO)) is True

    def test_allows_app_main_info(self) -> None:
        """app.main INFO must pass through."""
        assert self.f.filter(_make_record('app.main', level=logging.INFO)) is True

    def test_allows_alembic_info(self) -> None:
        """alembic INFO must pass through (not in audit set)."""
        assert self.f.filter(_make_record('alembic.runtime.migration', level=logging.INFO)) is True

    def test_allows_sqlalchemy_info(self) -> None:
        """sqlalchemy INFO must pass through (not in audit set)."""
        assert self.f.filter(_make_record('sqlalchemy.engine', level=logging.INFO)) is True


# ---------------------------------------------------------------------------
# Handler filter wire-up assertions
# ---------------------------------------------------------------------------


def test_stdout_handler_uses_cli_audit_noise_filter_not_panel_allowlist(
    tmp_path: pathlib.Path,
) -> None:
    """stdout handler must use cli_audit_noise filter, NOT panel_allowlist."""
    paths = _make_minimal_paths(tmp_path)
    cfg = build_log_config(paths, save_logs=False, quantity_of_logs=7)  # type: ignore[arg-type]
    stdout_filters = cfg['handlers']['stdout']['filters']
    assert 'cli_audit_noise' in stdout_filters, (
        'stdout handler must include cli_audit_noise filter'
    )
    assert 'panel_allowlist' not in stdout_filters, (
        'stdout handler must NOT include panel_allowlist filter (too strict for CLI)'
    )


def test_ring_buffer_still_uses_panel_allowlist(
    tmp_path: pathlib.Path,
) -> None:
    """ring_buffer handler must use panel_allowlist and uvicorn_ws_noise filters."""
    paths = _make_minimal_paths(tmp_path)
    cfg = build_log_config(paths, save_logs=False, quantity_of_logs=7)  # type: ignore[arg-type]
    rb_filters = cfg['handlers']['ring_buffer']['filters']
    assert 'panel_allowlist' in rb_filters, (
        'ring_buffer handler must include panel_allowlist filter'
    )
    assert 'uvicorn_ws_noise' in rb_filters, (
        'ring_buffer handler must include uvicorn_ws_noise filter'
    )


def test_file_handler_has_no_allowlist_filters(
    tmp_path: pathlib.Path,
) -> None:
    """file handler must not include panel_allowlist or cli_audit_noise filters."""
    paths = _make_minimal_paths(tmp_path)
    cfg = build_log_config(paths, save_logs=True, quantity_of_logs=7)  # type: ignore[arg-type]
    file_filters = cfg['handlers']['file']['filters']
    assert 'panel_allowlist' not in file_filters, (
        'file handler must NOT include panel_allowlist (audit retention requires all records)'
    )
    assert 'cli_audit_noise' not in file_filters, (
        'file handler must NOT include cli_audit_noise (audit retention requires all records)'
    )


# ---------------------------------------------------------------------------
# Ring-buffer filter regression: display=False records must reach the panel
# ---------------------------------------------------------------------------


def _ring_buffer_only_cfg() -> dict:  # type: ignore[type-arg]
    """Return a minimal dictConfig dict wiring only the ring_buffer handler.

    Uses the same filter set that ``build_log_config`` produces for the
    ring_buffer handler (panel_allowlist + uvicorn_ws_noise; no stdout_display).
    """
    return {
        'version': 1,
        'disable_existing_loggers': False,
        'filters': {
            'panel_allowlist': {
                '()': f'{_lc.__name__}._PanelAllowlistFilter',
            },
            'uvicorn_ws_noise': {
                '()': f'{_lc.__name__}._UvicornWsNoiseFilter',
            },
        },
        'handlers': {
            'ring_buffer': {
                '()': f'{_lc.__name__}.get_ring_buffer_handler',
                'level': 'INFO',
                'filters': ['panel_allowlist', 'uvicorn_ws_noise'],
            },
        },
        'loggers': {},
    }


@pytest.fixture(autouse=False)
def _reset_ring_buffer_singleton():  # type: ignore[return]
    """Ensure each test starts with a fresh ring-buffer singleton."""
    _lc._ring_buffer_handler = None
    yield
    _lc._ring_buffer_handler = None


def test_ring_buffer_receives_display_false_records(
    _reset_ring_buffer_singleton: None,
) -> None:
    """Records with extra={'display': False} (lifecycle events like 自動掃描)
    must appear in the ring buffer — the stdout_display filter must NOT be
    wired to the ring_buffer handler."""
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'app.test_ring_display_false': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logger = logging.getLogger('app.test_ring_display_false')
    logger.info('自動掃描 lifecycle event', extra={'display': False})

    snap = _lc.get_ring_buffer_handler().snapshot()
    messages = [e['message'] for e in snap]
    assert '自動掃描 lifecycle event' in messages, (
        'display=False record must reach the ring buffer (live log panel)'
    )


def test_ring_buffer_still_filters_uvicorn_access(
    _reset_ring_buffer_singleton: None,
) -> None:
    """uvicorn.access records must NOT appear in the ring buffer (allowlist drops non-app INFO)."""
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'uvicorn.access': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logging.getLogger('uvicorn.access').info('GET /healthz HTTP/1.1 200')

    snap = _lc.get_ring_buffer_handler().snapshot()
    messages = [e['message'] for e in snap]
    assert 'GET /healthz HTTP/1.1 200' not in messages, (
        'uvicorn.access records must be blocked by the panel_allowlist filter'
    )


def test_stdout_still_filters_display_false() -> None:
    """_DisplayFilter(stdout=True) must block records with display=False."""
    f = _DisplayFilter(stdout=True)

    record_false = _make_record(msg='lifecycle noise')
    record_false.display = False  # type: ignore[attr-defined]

    record_true = _make_record(msg='user action')
    record_true.display = True  # type: ignore[attr-defined]

    record_none = _make_record(msg='third-party (no display attr)')

    assert f.filter(record_false) is False, (
        'display=False must be blocked from stdout'
    )
    assert f.filter(record_true) is True, (
        'display=True must pass through stdout'
    )
    assert f.filter(record_none) is True, (
        'records without display attr (uvicorn, alembic) must pass stdout'
    )


# ---------------------------------------------------------------------------
# Ring buffer: httpx / httpcore audit noise must not reach the panel
# ---------------------------------------------------------------------------


def test_audit_filter_drops_httpx_records(
    _reset_ring_buffer_singleton: None,
) -> None:
    """httpx INFO records must NOT appear in the ring buffer snapshot."""
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'httpx': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logging.getLogger('httpx').info(
        'HTTP Request: GET http://127.0.0.1:5001/internal/health "HTTP/1.1 200 OK"'
    )

    snap = _lc.get_ring_buffer_handler().snapshot()
    messages = [e['message'] for e in snap]
    assert not any('HTTP Request' in m for m in messages), (
        'httpx request lines must be blocked by the panel_allowlist filter'
    )


def test_audit_filter_drops_httpcore_records(
    _reset_ring_buffer_singleton: None,
) -> None:
    """httpcore INFO records must NOT appear in the ring buffer snapshot."""
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'httpcore': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logging.getLogger('httpcore').info('send_request_headers.started')

    snap = _lc.get_ring_buffer_handler().snapshot()
    messages = [e['message'] for e in snap]
    assert 'send_request_headers.started' not in messages, (
        'httpcore records must be blocked by the panel_allowlist filter'
    )


# ---------------------------------------------------------------------------
# Ring buffer integration: uvicorn.error WARNING must reach the panel
# ---------------------------------------------------------------------------


def test_uvicorn_error_warning_reaches_ring_buffer(
    _reset_ring_buffer_singleton: None,
) -> None:
    """uvicorn.error WARNING must appear in the ring buffer (WARN+ non-app rule)."""
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'uvicorn.error': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logging.getLogger('uvicorn.error').warning('something went wrong in uvicorn')

    snap = _lc.get_ring_buffer_handler().snapshot()
    messages = [e['message'] for e in snap]
    assert 'something went wrong in uvicorn' in messages, (
        'uvicorn.error WARNING must reach the ring buffer via WARN+ rule'
    )


def test_uvicorn_error_info_dropped_from_ring_buffer(
    _reset_ring_buffer_singleton: None,
) -> None:
    """uvicorn.error INFO (any message) must NOT appear in the ring buffer."""
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'uvicorn.error': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logging.getLogger('uvicorn.error').info('Started server process [42]')

    snap = _lc.get_ring_buffer_handler().snapshot()
    messages = [e['message'] for e in snap]
    assert 'Started server process [42]' not in messages, (
        'uvicorn.error INFO must be dropped by the panel_allowlist filter'
    )


# ---------------------------------------------------------------------------
# File handler still receives httpx records when save_logs=True
# ---------------------------------------------------------------------------


@pytest.fixture()
def _clean_test_loggers() -> None:  # type: ignore[return]
    """Reset loggers that earlier tests may have hijacked via dictConfig.

    Some tests call ``logging.config.dictConfig`` with an explicit ``loggers``
    section that sets ``propagate=False`` on ``httpx`` or ``uvicorn.error``.
    Because ``disable_existing_loggers=False`` those overrides persist for the
    rest of the process.  This fixture resets a small set of known-affected
    loggers back to their default state (no handlers, propagate=True) so that
    tests relying on root-logger propagation work correctly regardless of
    execution order.
    """
    _RESET = ('httpx', 'httpcore', 'uvicorn.error', 'uvicorn.access')
    for name in _RESET:
        lgr = logging.getLogger(name)
        lgr.handlers.clear()
        lgr.propagate = True
        lgr.setLevel(logging.NOTSET)


# ---------------------------------------------------------------------------
# push_parsed_entry allowlist filtering (cross-process tailer)
# ---------------------------------------------------------------------------


def _make_entry(
    name: str = 'app.main',
    level: str = 'INFO',
    message: str = 'hello',
) -> dict:  # type: ignore[type-arg]
    return {
        'timestamp': '2026-04-19T12:00:00+00:00',
        'level': level,
        'name': name,
        'message': message,
        'sn': None,
    }


def test_push_parsed_entry_drops_non_app_info(
    _reset_ring_buffer_singleton: None,
) -> None:
    """push_parsed_entry must drop non-app INFO entries (e.g. uvicorn.access)."""
    handler = _lc.get_ring_buffer_handler()
    handler.push_parsed_entry(_make_entry(name='uvicorn.access', level='INFO', message='GET /api/health'))
    snap = handler.snapshot()
    assert not any(e['name'] == 'uvicorn.access' for e in snap), (
        'uvicorn.access INFO entry must be dropped by push_parsed_entry allowlist'
    )


def test_push_parsed_entry_allows_app_info(
    _reset_ring_buffer_singleton: None,
) -> None:
    """push_parsed_entry must keep app.main INFO entries."""
    handler = _lc.get_ring_buffer_handler()
    handler.push_parsed_entry(_make_entry(name='app.main', level='INFO', message='app-lifecycle'))
    snap = handler.snapshot()
    assert any(e['message'] == 'app-lifecycle' for e in snap), (
        'app.main INFO entries must pass push_parsed_entry allowlist'
    )


def test_push_parsed_entry_allows_uvicorn_warning(
    _reset_ring_buffer_singleton: None,
) -> None:
    """push_parsed_entry must keep uvicorn.error WARNING entries."""
    handler = _lc.get_ring_buffer_handler()
    handler.push_parsed_entry(_make_entry(name='uvicorn.error', level='WARNING', message='something bad'))
    snap = handler.snapshot()
    assert any(e['message'] == 'something bad' for e in snap), (
        'uvicorn.error WARNING entries must pass push_parsed_entry allowlist'
    )


@pytest.mark.parametrize(
    'logger_name',
    ['uvicorn.access', 'httpx', 'httpcore', 'websockets.server', 'alembic.runtime.migration'],
)
def test_push_parsed_entry_drops_non_app_info_parametrized(
    _reset_ring_buffer_singleton: None,
    logger_name: str,
) -> None:
    """push_parsed_entry must silently drop INFO entries from non-app loggers."""
    handler = _lc.get_ring_buffer_handler()
    handler.push_parsed_entry(_make_entry(name=logger_name, message='audit-noise'))
    snap = handler.snapshot()
    assert not any(e['name'] == logger_name for e in snap), (
        f'{logger_name} INFO entry must be dropped by push_parsed_entry allowlist'
    )


def test_push_parsed_entry_allows_app_main(
    _reset_ring_buffer_singleton: None,
) -> None:
    """push_parsed_entry must keep normal app.main entries."""
    handler = _lc.get_ring_buffer_handler()
    handler.push_parsed_entry(_make_entry(name='app.main', message='test-tag'))
    snap = handler.snapshot()
    assert any(e['message'] == 'test-tag' for e in snap), (
        'app.main entries must pass push_parsed_entry allowlist'
    )


def test_push_parsed_entry_dedup_still_works(
    _reset_ring_buffer_singleton: None,
) -> None:
    """Pushing the same app.main entry twice must produce only one entry in the snapshot."""
    handler = _lc.get_ring_buffer_handler()
    entry = _make_entry(name='app.main', message='dedup-check')
    handler.push_parsed_entry(entry)
    handler.push_parsed_entry(entry)
    snap = handler.snapshot()
    assert sum(1 for e in snap if e['message'] == 'dedup-check') == 1, (
        'duplicate entry via push_parsed_entry must be deduplicated'
    )


def test_file_handler_still_sees_uvicorn_access(
    _reset_ring_buffer_singleton: None,
    _clean_test_loggers: None,
    tmp_path: pathlib.Path,
) -> None:
    """With save_logs=True, uvicorn.access INFO records must be written to the
    log file.  The panel_allowlist filter is NOT wired to the file handler,
    so all infrastructure records are still persisted to disk.
    """
    import datetime

    class _Paths:
        pass

    paths = _Paths()
    paths.logs_dir = tmp_path / 'logs'  # type: ignore[attr-defined]
    paths.logs_dir.mkdir()  # type: ignore[attr-defined]

    cfg = build_log_config(paths, save_logs=True, quantity_of_logs=1)  # type: ignore[arg-type]
    logging.config.dictConfig(cfg)

    access_msg = '127.0.0.1:1234 - "GET /api/health HTTP/1.1" 200'
    logging.getLogger('uvicorn.access').info(access_msg)

    today = datetime.datetime.now().strftime('%Y-%m-%d')
    log_file = paths.logs_dir / f'{today}.log'  # type: ignore[attr-defined]
    assert log_file.exists(), 'log file must be created by DailyLogFileHandler'

    content = log_file.read_text(encoding='utf-8')
    assert access_msg in content, (
        'uvicorn.access records must be written to the file handler even though '
        'they are filtered from the ring buffer and stdout'
    )


def test_file_handler_still_sees_httpx(
    _reset_ring_buffer_singleton: None,
    _clean_test_loggers: None,
    tmp_path: pathlib.Path,
) -> None:
    """With save_logs=True, httpx INFO records must be written to the log file."""
    import datetime

    class _Paths:
        pass

    paths = _Paths()
    paths.logs_dir = tmp_path / 'logs'  # type: ignore[attr-defined]
    paths.logs_dir.mkdir()  # type: ignore[attr-defined]

    cfg = build_log_config(paths, save_logs=True, quantity_of_logs=1)  # type: ignore[arg-type]
    logging.config.dictConfig(cfg)

    httpx_msg = 'HTTP Request: GET http://127.0.0.1:5001/internal/health "HTTP/1.1 200 OK"'
    logging.getLogger('httpx').info(httpx_msg)

    today = datetime.datetime.now().strftime('%Y-%m-%d')
    log_file = paths.logs_dir / f'{today}.log'  # type: ignore[attr-defined]
    assert log_file.exists(), 'log file must be created by DailyLogFileHandler'

    content = log_file.read_text(encoding='utf-8')
    assert httpx_msg in content, (
        'httpx records must be written to the file handler even though '
        'they are filtered from the ring buffer and stdout'
    )


# ---------------------------------------------------------------------------
# Smoke test: realistic mixed log stream
# ---------------------------------------------------------------------------


def test_realistic_mixed_stream_smoke(
    _reset_ring_buffer_singleton: None,
) -> None:
    """Emit 10 records representing a real session; assert exactly 2 appear in
    the ring buffer (both app.main records — one INFO lifecycle, one ERROR).

    Stream composition (10 records):
    1. uvicorn.access INFO  — GET /api/health
    2. uvicorn.access INFO  — GET /api/auth/me
    3. uvicorn.error  INFO  — connection open
    4. uvicorn.error  INFO  — connection closed
    5. httpx          INFO  — HTTP Request: GET /internal/health
    6. httpx          INFO  — HTTP Request: GET /internal/health
    7. app.main       INFO  — 自動掃描 偵測新集數
    8. app.main       ERROR — 下載失敗 sn=1234
    9. alembic.runtime.migration INFO — Running upgrade
    10. uvicorn.error  INFO  — Started server process [99]

    Expected in ring buffer: records 7 and 8 only (2 records).
    """
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'uvicorn.access': {'level': 'INFO', 'handlers': ['ring_buffer'], 'propagate': False},
        'uvicorn.error':  {'level': 'INFO', 'handlers': ['ring_buffer'], 'propagate': False},
        'httpx':          {'level': 'INFO', 'handlers': ['ring_buffer'], 'propagate': False},
        'app.main':       {'level': 'INFO', 'handlers': ['ring_buffer'], 'propagate': False},
        'alembic.runtime.migration': {'level': 'INFO', 'handlers': ['ring_buffer'], 'propagate': False},
    }
    logging.config.dictConfig(cfg)

    logging.getLogger('uvicorn.access').info('127.0.0.1:1 - "GET /api/health HTTP/1.1" 200')
    logging.getLogger('uvicorn.access').info('127.0.0.1:2 - "GET /api/auth/me HTTP/1.1" 200')
    logging.getLogger('uvicorn.error').info('127.0.0.1:3 - connection open')
    logging.getLogger('uvicorn.error').info('127.0.0.1:4 - connection closed')
    logging.getLogger('httpx').info('HTTP Request: GET http://127.0.0.1:5001/internal/health "HTTP/1.1 200 OK"')
    logging.getLogger('httpx').info('HTTP Request: GET http://127.0.0.1:5001/internal/health "HTTP/1.1 200 OK"')
    logging.getLogger('app.main').info('自動掃描 偵測新集數')
    logging.getLogger('app.main').error('下載失敗 sn=1234')
    logging.getLogger('alembic.runtime.migration').info('Running upgrade abc -> def')
    logging.getLogger('uvicorn.error').info('Started server process [99]')

    snap = _lc.get_ring_buffer_handler().snapshot()
    assert len(snap) == 2, (
        f'Expected exactly 2 entries in ring buffer, got {len(snap)}: '
        f'{[e["message"] for e in snap]}'
    )
    messages = [e['message'] for e in snap]
    assert '自動掃描 偵測新集數' in messages
    assert '下載失敗 sn=1234' in messages


# ---------------------------------------------------------------------------
# Cause B regression: build_log_config must set disable_existing_loggers=False
# ---------------------------------------------------------------------------


def _make_minimal_paths(tmp_path: pathlib.Path) -> object:
    """Return a minimal paths object with logs_dir pointing at tmp_path."""

    class _Paths:
        pass

    p = _Paths()
    p.logs_dir = tmp_path / 'logs'  # type: ignore[attr-defined]
    p.logs_dir.mkdir()  # type: ignore[attr-defined]
    return p


def test_build_log_config_sets_disable_existing_loggers_false(
    tmp_path: pathlib.Path,
) -> None:
    """build_log_config must always include disable_existing_loggers=False so
    uvicorn's second dictConfig call does not silence app.* loggers."""
    paths = _make_minimal_paths(tmp_path)
    cfg = build_log_config(paths, save_logs=False, quantity_of_logs=7)  # type: ignore[arg-type]
    assert cfg.get('disable_existing_loggers') is False, (
        'build_log_config must set disable_existing_loggers=False '
        'to survive uvicorn calling dictConfig a second time'
    )


def test_dictconfig_twice_keeps_app_logger_enabled(
    _reset_ring_buffer_singleton: None,
    tmp_path: pathlib.Path,
) -> None:
    """A second logging.config.dictConfig call (simulating uvicorn.run(log_config=...))
    must NOT silence existing app.* loggers."""
    paths = _make_minimal_paths(tmp_path)

    # First dictConfig — establishes the baseline (matches app startup).
    logging.config.dictConfig(build_log_config(paths, save_logs=False, quantity_of_logs=7))  # type: ignore[arg-type]
    logger = logging.getLogger('app.smoke_double_dictconfig')
    logger.info('first')

    # Second dictConfig — simulates uvicorn.run(app, log_config=...) internals.
    logging.config.dictConfig(build_log_config(paths, save_logs=False, quantity_of_logs=7))  # type: ignore[arg-type]
    logger.info('second')

    rb = _lc.get_ring_buffer_handler()
    snap = rb.snapshot()
    messages = [e['message'] for e in snap if e['name'] == 'app.smoke_double_dictconfig']
    assert len(messages) == 2, (
        f'Expected 2 records after double dictConfig, got {len(messages)}: {messages!r}. '
        'disable_existing_loggers=False must prevent the second dictConfig from '
        'silencing the app.* logger tree.'
    )
    assert 'first' in messages
    assert 'second' in messages


# ---------------------------------------------------------------------------
# Cause A regression: run_baseline_migrations must not wipe dictConfig
# ---------------------------------------------------------------------------


def test_alembic_migration_does_not_wipe_dict_config(
    _reset_ring_buffer_singleton: None,
    tmp_path: pathlib.Path,
) -> None:
    """run_baseline_migrations() must not call alembic's fileConfig which would
    overwrite our dictConfig and disable existing loggers (Cause A)."""
    from app.logging_ import Logger
    from app.persistence.db import Database

    # Apply our dictConfig first — establishes the ring_buffer handler chain.
    paths = _make_minimal_paths(tmp_path)
    logging.config.dictConfig(build_log_config(paths, save_logs=False, quantity_of_logs=7))  # type: ignore[arg-type]

    # Run migrations — this is the operation that previously called
    # alembic fileConfig(disable_existing_loggers=True).
    logger_obj = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    db_path = tmp_path / 'test.db'
    db = Database(f'sqlite:///{db_path}', logger_obj)
    db.run_baseline_migrations()
    db.dispose()

    # Emit via app.main AFTER migrations — must still reach the ring buffer.
    post_migration_logger = logging.getLogger('app.main.migration_test')
    post_migration_logger.info('Bootstrap from log file after migrations')

    rb = _lc.get_ring_buffer_handler()
    snap = rb.snapshot()
    app_messages = [e['message'] for e in snap if e['name'].startswith('app.')]
    assert any('Bootstrap from log file after migrations' in m for m in app_messages), (
        'app.main INFO record emitted AFTER run_baseline_migrations() must still '
        'reach the ring buffer. alembic fileConfig must not disable existing loggers.'
    )


# ---------------------------------------------------------------------------
# _UvicornWsNoiseFilter unit tests
# ---------------------------------------------------------------------------


class TestUvicornWsNoiseFilter:
    def setup_method(self) -> None:
        self.f = _UvicornWsNoiseFilter()

    def test_uvicorn_ws_noise_drops_keepalive_ping_timeout(self) -> None:
        """uvicorn.error record with 'keepalive ping timeout' must be dropped."""
        record = _make_record(
            name='uvicorn.error',
            msg='sent 1011 (internal error) keepalive ping timeout; no close frame received',
            level=logging.ERROR,
        )
        assert self.f.filter(record) is False

    def test_uvicorn_ws_noise_drops_1011(self) -> None:
        """uvicorn.error record with 'sent 1011' must be dropped."""
        record = _make_record(
            name='uvicorn.error',
            msg='sent 1011 (internal error) something',
            level=logging.ERROR,
        )
        assert self.f.filter(record) is False

    def test_uvicorn_ws_noise_drops_via_exc_info(self) -> None:
        """Record with bland message but exc_info pointing to ConnectionClosedError must be dropped."""

        class ConnectionClosedError(Exception):
            pass

        exc = ConnectionClosedError('keepalive ping timeout')
        record = _make_record(
            name='uvicorn.error',
            msg='WebSocket handler error',
            level=logging.ERROR,
        )
        record.exc_info = (type(exc), exc, None)  # type: ignore[assignment]
        assert self.f.filter(record) is False

    def test_uvicorn_ws_noise_allows_real_uvicorn_error(self) -> None:
        """uvicorn.error record with unrelated message must pass through."""
        record = _make_record(
            name='uvicorn.error',
            msg='Application error - 500',
            level=logging.ERROR,
        )
        assert self.f.filter(record) is True

    def test_uvicorn_ws_noise_allows_other_loggers(self) -> None:
        """app.main record that happens to contain a noise signature must NOT be dropped."""
        record = _make_record(
            name='app.main',
            msg='debug: keepalive ping timeout trace',
            level=logging.ERROR,
        )
        assert self.f.filter(record) is True

    def test_uvicorn_ws_noise_allows_uvicorn_protocols_websockets_real_error(self) -> None:
        """uvicorn.protocols.websockets non-noise error must pass."""
        record = _make_record(
            name='uvicorn.protocols.websockets',
            msg='Unhandled exception in ASGI application',
            level=logging.ERROR,
        )
        assert self.f.filter(record) is True

    def test_uvicorn_ws_noise_drops_websocket_is_closed(self) -> None:
        """uvicorn.error record with 'WebSocket is closed' must be dropped."""
        record = _make_record(
            name='uvicorn.error',
            msg='WebSocket is closed',
            level=logging.ERROR,
        )
        assert self.f.filter(record) is False


# ---------------------------------------------------------------------------
# _UvicornWsNoiseFilter integration: ring buffer drops WS noise end-to-end
# ---------------------------------------------------------------------------


def test_uvicorn_ws_noise_drops_keepalive_from_ring_buffer(
    _reset_ring_buffer_singleton: None,
) -> None:
    """uvicorn.error ERROR with keepalive ping timeout must not appear in ring buffer."""
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'uvicorn.error': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logging.getLogger('uvicorn.error').error(
        'sent 1011 (internal error) keepalive ping timeout; no close frame received'
    )

    snap = _lc.get_ring_buffer_handler().snapshot()
    assert not any('keepalive ping timeout' in e['message'] for e in snap), (
        'WS keepalive-timeout records must be dropped from the ring buffer'
    )


def test_uvicorn_ws_noise_real_error_reaches_ring_buffer(
    _reset_ring_buffer_singleton: None,
) -> None:
    """uvicorn.error ERROR with real application error must still appear in ring buffer."""
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'uvicorn.error': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logging.getLogger('uvicorn.error').error('Application error - 500')

    snap = _lc.get_ring_buffer_handler().snapshot()
    assert any('Application error - 500' in e['message'] for e in snap), (
        'real uvicorn.error ERROR records must still reach the ring buffer'
    )


# ---------------------------------------------------------------------------
# push_parsed_entry: WS noise filter via tailed log entries
# ---------------------------------------------------------------------------


def test_push_parsed_entry_drops_ws_noise_by_name_message(
    _reset_ring_buffer_singleton: None,
) -> None:
    """push_parsed_entry must drop uvicorn.error entries matching WS noise sigs."""
    handler = _lc.get_ring_buffer_handler()
    handler.push_parsed_entry(
        _make_entry(
            name='uvicorn.error',
            level='ERROR',
            message='sent 1011 (internal error) keepalive ping timeout; no close frame received',
        )
    )
    snap = handler.snapshot()
    assert not any('1011' in e['message'] for e in snap), (
        'push_parsed_entry must drop uvicorn.error WS noise entries'
    )

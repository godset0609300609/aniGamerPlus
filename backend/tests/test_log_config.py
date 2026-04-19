"""Tests for :mod:`app.log_config` — focuses on issues fixed in the
ring-buffer message deduplication and the audit-logger filter.

Specifically:
* ``RingBufferHandler._format()`` must return ``record.getMessage()`` as the
  ``message`` field — NOT the full pre-formatted line that embeds timestamp
  and level prefix (which would appear twice in the UI).
* ``_AuditLoggerFilter`` must drop ``uvicorn.access``, ``httpx``, and
  ``httpcore`` records but pass ``app.main`` and ``uvicorn.error``.
* ``_UvicornConnectionLifecycleFilter`` must drop uvicorn.error INFO lines
  with connection-lifecycle patterns but pass real errors and unrelated INFO.
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
    _AuditLoggerFilter,
    _DisplayFilter,
    _UvicornConnectionLifecycleFilter,
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
# Issue 3: _AuditLoggerFilter
# ---------------------------------------------------------------------------


class TestAuditLoggerFilter:
    def setup_method(self) -> None:
        self.f = _AuditLoggerFilter()

    def _record(self, name: str) -> logging.LogRecord:
        return _make_record(name=name)

    def test_drops_uvicorn_access(self) -> None:
        """uvicorn.access records must be blocked by the filter."""
        record = self._record('uvicorn.access')
        assert self.f.filter(record) is False

    def test_passes_app_main(self) -> None:
        """app.main records must pass through."""
        assert self.f.filter(self._record('app.main')) is True

    def test_passes_uvicorn_error(self) -> None:
        """uvicorn.error records (server startup / shutdown) must pass through."""
        assert self.f.filter(self._record('uvicorn.error')) is True

    def test_passes_uvicorn_root(self) -> None:
        """The bare 'uvicorn' logger must not be filtered."""
        assert self.f.filter(self._record('uvicorn')) is True

    def test_passes_app_api_scheduler_proxy(self) -> None:
        """A deeply nested app logger must pass through."""
        assert self.f.filter(self._record('app.api._scheduler_proxy')) is True

    def test_passes_alembic(self) -> None:
        assert self.f.filter(self._record('alembic')) is True

    def test_passes_root_logger(self) -> None:
        assert self.f.filter(self._record('root')) is True

    def test_drops_httpx(self) -> None:
        """httpx records (outbound request lines) must be blocked."""
        assert self.f.filter(self._record('httpx')) is False

    def test_drops_httpcore(self) -> None:
        """httpcore records must be blocked."""
        assert self.f.filter(self._record('httpcore')) is False

    def test_drops_websockets_server(self) -> None:
        """websockets.server records must be blocked."""
        assert self.f.filter(self._record('websockets.server')) is False

    def test_drops_websockets_client(self) -> None:
        """websockets.client records must be blocked."""
        assert self.f.filter(self._record('websockets.client')) is False


# ---------------------------------------------------------------------------
# Ring-buffer filter regression: display=False records must reach the panel
# ---------------------------------------------------------------------------


def _ring_buffer_only_cfg() -> dict:  # type: ignore[type-arg]
    """Return a minimal dictConfig dict wiring only the ring_buffer handler.

    Uses the same filter set that ``build_log_config`` produces for the
    ring_buffer handler (audit_logger + uvicorn_connection_noise, no
    stdout_display).
    """
    return {
        'version': 1,
        'disable_existing_loggers': False,
        'filters': {
            'audit_logger': {
                '()': f'{_lc.__name__}._AuditLoggerFilter',
            },
            'uvicorn_connection_noise': {
                '()': f'{_lc.__name__}._UvicornConnectionLifecycleFilter',
            },
        },
        'handlers': {
            'ring_buffer': {
                '()': f'{_lc.__name__}.get_ring_buffer_handler',
                'level': 'INFO',
                'filters': ['audit_logger', 'uvicorn_connection_noise'],
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
        'test_ring_display_false': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logger = logging.getLogger('test_ring_display_false')
    logger.info('自動掃描 lifecycle event', extra={'display': False})

    snap = _lc.get_ring_buffer_handler().snapshot()
    messages = [e['message'] for e in snap]
    assert '自動掃描 lifecycle event' in messages, (
        'display=False record must reach the ring buffer (live log panel)'
    )


def test_ring_buffer_still_filters_uvicorn_access(
    _reset_ring_buffer_singleton: None,
) -> None:
    """uvicorn.access records must NOT appear in the ring buffer (audit noise)."""
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
        'uvicorn.access records must be blocked by the audit_logger filter'
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
        'httpx request lines must be blocked by the audit_logger filter'
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
        'httpcore records must be blocked by the audit_logger filter'
    )


# ---------------------------------------------------------------------------
# _UvicornConnectionLifecycleFilter unit tests
# ---------------------------------------------------------------------------


class TestUvicornConnectionLifecycleFilter:
    def setup_method(self) -> None:
        self.f = _UvicornConnectionLifecycleFilter()

    def _uvicorn_error_record(self, msg: str, level: int = logging.INFO) -> logging.LogRecord:
        return logging.LogRecord(
            name='uvicorn.error',
            level=level,
            pathname='',
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def _other_record(self, msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            name='app.main',
            level=logging.INFO,
            pathname='',
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_drops_connection_open(self) -> None:
        record = self._uvicorn_error_record('127.0.0.1:12345 - connection open')
        assert self.f.filter(record) is False

    def test_drops_connection_closed(self) -> None:
        record = self._uvicorn_error_record('127.0.0.1:12345 - connection closed')
        assert self.f.filter(record) is False

    def test_drops_websocket_accepted(self) -> None:
        record = self._uvicorn_error_record(
            '127.0.0.1:12345 - "WebSocket /internal/progress [accepted]"'
        )
        assert self.f.filter(record) is False

    def test_drops_accepted_pattern(self) -> None:
        record = self._uvicorn_error_record('WebSocket /ws [accepted]')
        assert self.f.filter(record) is False

    def test_allows_real_errors(self) -> None:
        """ERROR-level records always pass regardless of message content."""
        record = self._uvicorn_error_record('connection open', level=logging.ERROR)
        assert self.f.filter(record) is True

    def test_allows_warning(self) -> None:
        """WARNING-level records always pass regardless of message content."""
        record = self._uvicorn_error_record('connection open', level=logging.WARNING)
        assert self.f.filter(record) is True

    def test_allows_unrelated_info(self) -> None:
        """An INFO message that does not match any noisy pattern must pass."""
        record = self._uvicorn_error_record('Started server process [12345]')
        assert self.f.filter(record) is True

    def test_allows_non_uvicorn_error_logger(self) -> None:
        """Records from other loggers must not be filtered."""
        record = self._other_record('connection open')
        assert self.f.filter(record) is True


# ---------------------------------------------------------------------------
# Ring buffer integration: connection-lifecycle noise must not reach the panel
# ---------------------------------------------------------------------------


def test_uvicorn_connection_noise_filter_drops_connection_open(
    _reset_ring_buffer_singleton: None,
) -> None:
    """uvicorn.error INFO 'connection open' must NOT appear in the ring buffer."""
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'uvicorn.error': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logging.getLogger('uvicorn.error').info('127.0.0.1:55123 - connection open')

    snap = _lc.get_ring_buffer_handler().snapshot()
    messages = [e['message'] for e in snap]
    assert not any('connection open' in m for m in messages), (
        'connection open noise must be blocked from the ring buffer'
    )


def test_uvicorn_connection_noise_filter_allows_real_errors(
    _reset_ring_buffer_singleton: None,
) -> None:
    """uvicorn.error ERROR records must always reach the ring buffer."""
    cfg = _ring_buffer_only_cfg()
    cfg['loggers'] = {
        'uvicorn.error': {
            'level': 'INFO',
            'handlers': ['ring_buffer'],
            'propagate': False,
        }
    }
    logging.config.dictConfig(cfg)

    logging.getLogger('uvicorn.error').error('connection open but something broke')

    snap = _lc.get_ring_buffer_handler().snapshot()
    messages = [e['message'] for e in snap]
    assert any('connection open but something broke' in m for m in messages), (
        'ERROR-level uvicorn.error records must not be suppressed'
    )


def test_uvicorn_connection_noise_filter_allows_unrelated_info(
    _reset_ring_buffer_singleton: None,
) -> None:
    """uvicorn.error INFO not matching noisy patterns must reach the ring buffer."""
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
    assert 'Started server process [42]' in messages, (
        'unrelated uvicorn.error INFO must reach the ring buffer'
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


def test_file_handler_still_sees_httpx(
    _reset_ring_buffer_singleton: None,
    _clean_test_loggers: None,
    tmp_path: pathlib.Path,
) -> None:
    """With save_logs=True, httpx INFO records must be written to the log file.

    The audit_logger / uvicorn_connection_noise filters are NOT wired to the
    file handler, so audit noise is retained on disk for forensic purposes.
    """
    import datetime

    # Build a minimal WorkspacePaths-like stub so build_log_config can proceed.
    class _Paths:
        pass

    paths = _Paths()
    paths.logs_dir = tmp_path / 'logs'  # type: ignore[attr-defined]
    paths.logs_dir.mkdir()  # type: ignore[attr-defined]

    cfg = build_log_config(paths, save_logs=True, quantity_of_logs=1)  # type: ignore[arg-type]
    logging.config.dictConfig(cfg)

    httpx_msg = 'HTTP Request: GET http://127.0.0.1:5001/internal/health "HTTP/1.1 200 OK"'
    logging.getLogger('httpx').info(httpx_msg)

    # Locate today's log file.
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    log_file = paths.logs_dir / f'{today}.log'  # type: ignore[attr-defined]
    assert log_file.exists(), 'log file must be created by DailyLogFileHandler'

    content = log_file.read_text(encoding='utf-8')
    assert httpx_msg in content, (
        'httpx records must be written to the file handler even though '
        'they are filtered from the ring buffer and stdout'
    )

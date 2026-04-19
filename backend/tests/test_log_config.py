"""Tests for :mod:`app.log_config` — focuses on issues fixed in the
ring-buffer message deduplication and the audit-logger filter.

Specifically:
* ``RingBufferHandler._format()`` must return ``record.getMessage()`` as the
  ``message`` field — NOT the full pre-formatted line that embeds timestamp
  and level prefix (which would appear twice in the UI).
* ``_AuditLoggerFilter`` must drop ``uvicorn.access`` records but pass
  ``app.main`` and ``uvicorn.error``.
"""

from __future__ import annotations

import logging

import pytest

from app.log_config import RingBufferHandler, _AuditLoggerFilter


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

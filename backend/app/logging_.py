"""Coloured, thread-safe logger for the rewritten downloader.

This module now delegates to :mod:`logging` underneath (see
:mod:`app.log_config`) but preserves the pre-existing public surface:
``Logger.info/error/success/prune_old_logs/get_logger``. Call sites keep
passing ``(sn, tag, detail, display=..., display_time=...)``; internally
we map that onto ``logging.Logger`` records carrying ``display`` /
``_success`` extras so the shared formatter produces uniform lines
whether the record originated here, in uvicorn, in alembic, or in
SQLAlchemy.
"""

from __future__ import annotations

import contextlib
import datetime
import enum
import logging
import logging.config
import pathlib
import re
import threading

from . import log_config as _log_config_mod


class LogLevel(enum.IntEnum):
    INFO = 0
    ERROR = 1
    SUCCESS = 2


# ``LogRecord`` is kept for backwards compatibility with callers that
# imported it for unit-test introspection. The live logger no longer
# funnels every call through an intermediate dataclass — it hands the
# work off to ``logging.Logger.log`` directly.
import dataclasses  # noqa: E402  — placed below LogLevel for grouping


@dataclasses.dataclass(slots=True)
class LogRecord:
    sn: int | str | None
    tag: str
    detail: str
    level: LogLevel
    display: bool
    display_time: bool = True


# ``YYYY-MM-DD.log`` exactly — the legacy pruner's filter was "anything with
# ``web`` in the name stays", but we tighten it to "looks like a dated log
# file" so stray artefacts aren't swept up either.
_DATED_LOG_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})\.log$')


def _format_message(record: LogRecord) -> str:
    """Render the ``{sn_prefix}{tag} {detail}`` body."""
    sn_prefix = ''
    if record.sn is not None and record.sn != '':
        sn_prefix = f'sn={record.sn} '
    body = record.tag
    if record.detail:
        body = f'{body} {record.detail}'
    return f'{sn_prefix}{body}'


def _legacy_line(record: LogRecord) -> str:
    """Render the legacy single-line form used when callers disable the timestamp.

    The shared logging formatter always prepends a timestamp + levelname; for
    ``display_time=False`` callers we need to fall back to the old "just the
    body" shape so CLI consumers (progress counters, separators) remain
    visually compact. We do this by routing the record straight to the
    underlying handlers' streams / files with a plain string, bypassing the
    stdlib formatter.
    """
    parts: list[str] = []
    if record.display_time:
        parts.append(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    if record.sn is not None and record.sn != '':
        parts.append(f'sn={record.sn}')
    parts.append(record.tag)
    if record.detail:
        parts.append(record.detail)
    return ' '.join(parts)


class Logger:
    """Thread-safe stdout+file logger.

    Parameters
    ----------
    logs_dir:
        Directory where dated log files are written. Created lazily on the
        first write.
    save_logs:
        When False, nothing is written to disk. Console output is unaffected.
    quantity_of_logs:
        How many calendar days of dated logs to keep when ``prune_old_logs``
        is called. Must be >= 1.
    section:
        Optional logical section name; becomes the ``app.<section>`` logger
        name. Defaults to ``app.main``.
    """

    _LEVEL_FUNCS = {
        LogLevel.INFO: 'info',
        LogLevel.ERROR: 'error',
        LogLevel.SUCCESS: 'info',
    }

    def __init__(
        self,
        logs_dir: pathlib.Path,
        *,
        save_logs: bool,
        quantity_of_logs: int,
        section: str | None = None,
    ) -> None:
        self._logs_dir = pathlib.Path(logs_dir)
        self._save_logs = save_logs
        self._quantity_of_logs = max(1, int(quantity_of_logs))
        self._lock = threading.Lock()
        self._section = section or 'main'
        self._stdlib_logger = logging.getLogger(f'app.{self._section}')
        # Ensure the instance has a dated file handler pointing at logs_dir.
        # dictConfig-driven handlers (production) normally cover this already,
        # but direct Logger(...) callers (tests, ad-hoc scripts) rely on us
        # auto-attaching so log files actually get written.
        if self._save_logs:
            self._attach_file_handler()

    def _attach_file_handler(self) -> None:
        """Attach a :class:`DailyLogFileHandler` scoped to ``self._logs_dir``.

        Idempotent: if this logger already has a handler pointing at the same
        directory, we don't add a second. Formatter matches the shared
        dictConfig shape so a line written here is indistinguishable from a
        dictConfig-emitted one.
        """
        for existing in self._stdlib_logger.handlers:
            owned_dir = getattr(existing, '_logs_dir', None)
            if owned_dir is not None and pathlib.Path(owned_dir) == self._logs_dir:
                return
        handler = _log_config_mod.DailyLogFileHandler(
            self._logs_dir,
            backupCount=self._quantity_of_logs,
            encoding='utf-8',
        )
        formatter = logging.Formatter(
            fmt='%(asctime)s  %(levelname)-5s  %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        self._stdlib_logger.addHandler(handler)
        # Always pin to INFO so previous test runs (or an overly strict
        # dictConfig) that bumped the level past INFO can't silently drop
        # our records on the floor.
        self._stdlib_logger.setLevel(logging.INFO)
        # ``dictConfig(disable_existing_loggers=True)`` marks every logger not
        # explicitly listed in ``loggers`` as ``disabled=True``, which silently
        # drops every record sent to them. Our default config lists ``app``
        # but not ``app.main`` so the child is at the mercy of whichever
        # config was last applied. Force-enable so our records always emit.
        self._stdlib_logger.disabled = False

    # ------------------------------------------------------------------ API

    def info(
        self,
        sn: int | str | None,
        tag: str,
        detail: str = '',
        *,
        display: bool = True,
        display_time: bool = True,
    ) -> None:
        self._write(LogRecord(sn, tag, detail, LogLevel.INFO, display, display_time))

    def error(
        self,
        sn: int | str | None,
        tag: str,
        detail: str = '',
        *,
        display: bool = True,
        display_time: bool = True,
    ) -> None:
        self._write(LogRecord(sn, tag, detail, LogLevel.ERROR, display, display_time))

    def success(
        self,
        sn: int | str | None,
        tag: str,
        detail: str = '',
        *,
        display: bool = True,
        display_time: bool = True,
    ) -> None:
        self._write(LogRecord(sn, tag, detail, LogLevel.SUCCESS, display, display_time))

    def prune_old_logs(self) -> None:
        """Delete dated ``.log`` files older than ``quantity_of_logs`` days.

        Non-dated files (``web.log``, ``README.md``, stray ``foo.txt`` …)
        are left alone, matching the legacy ``__remove_superfluous_logs``
        exclusion for ``web``.
        """
        if not self._logs_dir.exists():
            return

        cutoff = datetime.datetime.now().date() - datetime.timedelta(days=self._quantity_of_logs - 1)
        with self._lock:
            for entry in self._logs_dir.iterdir():
                if not entry.is_file():
                    continue
                match = _DATED_LOG_RE.match(entry.name)
                if not match:
                    continue
                try:
                    file_date = datetime.datetime.strptime(entry.name[:10], '%Y-%m-%d').date()
                except ValueError:
                    continue
                if file_date < cutoff:
                    with contextlib.suppress(OSError):  # best-effort; another worker may have raced us
                        entry.unlink()

    # -------------------------------------------------------------- internals

    def _write(self, record: LogRecord) -> None:
        # ``display_time=False`` preserves the legacy compact line — used by
        # progress counters and separators. We still duplicate to the file
        # (when save_logs is on) so log tailers see the same content.
        if not record.display_time:
            line = _legacy_line(record)
            if record.display:
                # Preserve the legacy ``print`` behaviour with no trailing ANSI.
                print(line)
            if self._save_logs:
                self._write_legacy_file(line)
            return

        extras = {
            'display': record.display,
            '_success': record.level == LogLevel.SUCCESS,
        }
        message = _format_message(record)
        method = getattr(self._stdlib_logger, self._LEVEL_FUNCS[record.level])
        method(message, extra=extras)

    def _write_legacy_file(self, line: str) -> None:
        """Append a pre-formatted line to today's dated log file.

        Used only for ``display_time=False`` calls; the handler-driven path
        goes through ``TimedRotatingFileHandler`` instead.
        """
        with self._lock:
            self._logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._logs_dir / (datetime.datetime.now().strftime('%Y-%m-%d') + '.log')
            with log_path.open('a', encoding='utf-8') as fh:
                fh.write(line + '\n')


# ---------------------------------------------------------------------------
# Transitional global
# ---------------------------------------------------------------------------


_default_logger: Logger | None = None
_default_lock = threading.Lock()
_dict_config_applied = False
_dict_config_lock = threading.Lock()


def _ensure_dict_config_applied(
    paths: object,
    *,
    save_logs: bool,
    quantity_of_logs: int,
) -> None:
    """Apply ``dictConfig`` once per process on the first ``get_logger`` call.

    Callers who drive logging themselves (``DashboardApp.run``) call
    ``dictConfig`` explicitly before we ever get here. The guard keeps us
    from overwriting their richer setup with the conservative defaults
    below.
    """
    global _dict_config_applied
    if _dict_config_applied:
        return
    with _dict_config_lock:
        if _dict_config_applied:
            return
        config = _log_config_mod.build_log_config(
            paths,  # type: ignore[arg-type]
            save_logs=save_logs,
            quantity_of_logs=quantity_of_logs,
        )
        logging.config.dictConfig(config)
        _dict_config_applied = True


def get_logger() -> Logger:
    """Return a process-wide ``Logger`` with defaults drawn from the
    workspace ``logs/`` directory.

    The logger is built lazily on first call; subsequent calls return the
    same instance. Tests that want a different logger should construct one
    explicitly and not rely on this global.
    """
    global _default_logger
    if _default_logger is not None:
        return _default_logger

    with _default_lock:
        if _default_logger is None:
            from .persistence.paths import WorkspacePaths  # local to break cycle

            paths = WorkspacePaths.detect()
            _ensure_dict_config_applied(paths, save_logs=True, quantity_of_logs=7)
            _default_logger = Logger(
                paths.logs_dir,
                save_logs=True,
                quantity_of_logs=7,
            )
    return _default_logger

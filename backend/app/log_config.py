"""Unified ``logging.config.dictConfig`` builder.

Shared between the FastAPI server (``app.main.DashboardApp.run``),
``uvicorn`` (passed through ``uvicorn.run(..., log_config=...)``), and the
transitional :class:`app.logging_.Logger` wrapper. All three routes share
the same formatter + handler tree so log lines look identical whether
they originate from our code, uvicorn's access log, Alembic, or
SQLAlchemy.

Why a single module
-------------------
Before this, the project printed logs via ``print()``, and uvicorn had its
own formatter. That meant stdout contained two distinct log styles side
by side. ``build_log_config`` centralises the shape so downstream
integrations (uvicorn's ``log_config`` kwarg, alembic's programmatic
``cfg.attributes``, and our own ``Logger``) can share one config dict.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import datetime
import logging
import pathlib
import re
import threading
import typing as T

if T.TYPE_CHECKING:
    from .persistence.paths import WorkspacePaths


#: Uniform log line format — timestamp, fixed-width level, dotted logger
#: name, message. Mirrored across stdout + file handlers so redirected
#: logs look exactly like the terminal output.
LOG_FORMAT = '%(asctime)s  %(levelname)-5s  %(name)s: %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


class _DisplayFilter:
    """Gate records based on their ``display`` extra.

    ``Logger.info(..., display=False)`` marks the record as file-only;
    the stdout handler attaches this filter with ``want_display=True``
    and the file handler with ``want_display=False`` inverted
    appropriately. Records without the ``display`` attribute (uvicorn,
    alembic, SQLAlchemy) always pass both handlers.
    """

    def __init__(self, *, stdout: bool) -> None:
        self._stdout = stdout

    def filter(self, record: T.Any) -> bool:  # noqa: A003 — stdlib API
        display = getattr(record, 'display', None)
        if display is None:
            return True
        # display=True  -> stdout yes, file yes
        # display=False -> stdout no,  file yes
        return not (self._stdout and display is False)


class _CliAuditNoiseFilter:
    """Drop audit-level repeat noise from CLI stdout.

    Intended for the RichHandler (stdout) only. The web log panel uses
    :class:`_PanelAllowlistFilter` for a stricter cut; the file handler
    keeps everything.

    Suppressed loggers: ``uvicorn.access`` (one line per HTTP request),
    ``httpx`` / ``httpcore`` (one line per outbound HTTP call from health
    polling, ~2 per 10s). These inflate CLI output without carrying
    actionable info — real errors at WARNING+ still pass through.
    """

    _AUDIT_LOGGERS = frozenset({'uvicorn.access', 'httpx', 'httpcore'})

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — stdlib API
        return not (record.name in self._AUDIT_LOGGERS and record.levelno < logging.WARNING)


#: Signature strings that identify benign uvicorn WebSocket client-disconnect
#: noise.  Extracted as a module-level constant so both :class:`_UvicornWsNoiseFilter`
#: and :meth:`RingBufferHandler.push_parsed_entry` reference the same source of truth.
_UVICORN_WS_NOISE_SIGS: tuple[str, ...] = (
    'keepalive ping timeout',
    'no close frame received',
    'sent 1011',
    'sent 1006',
    'ConnectionClosedError',
    'ConnectionClosedOK',
    'WebSocket is closed',
    # asyncio's default exception handler logs the websockets library's
    # keepalive-ping task (run under asyncio.shield) with this phrase when
    # the connection closes before the ping resolves, e.g.:
    #   ERROR asyncio: ConnectionClosedError exception in shielded future
    #   Close(code=<CloseCode.INTERNAL_ERROR: 1011>, reason='keepalive ping timeout')
    'exception in shielded future',
)


#: Logger names that can legitimately emit the benign WS-disconnect noise
#: this filter targets. ``uvicorn.error`` / ``uvicorn.protocols.websockets``
#: log the close from uvicorn's own protocol handler; ``asyncio`` logs it
#: separately when the ``websockets`` library's keepalive-ping task (run via
#: ``asyncio.shield``) has its ``ConnectionClosedError`` surfaced by
#: asyncio's default "exception in shielded future" handler instead of
#: uvicorn's own error path — e.g. ``ERROR asyncio: ConnectionClosedError
#: exception in shielded future``. Both are the same underlying event
#: (keepalive ping timeout / 1011 close) observed from two different log
#: sources.
_UVICORN_WS_NOISE_LOGGERS: tuple[str, ...] = (
    'uvicorn.error',
    'uvicorn.protocols.websockets',
    'asyncio',
)


class _UvicornWsNoiseFilter:
    """Drop uvicorn/asyncio records whose message or exception indicates a
    benign client-disconnect WebSocket close (ping timeout, no close frame).

    Browsers and mobile clients disconnect without clean close frames all
    the time — uvicorn (and, for the keepalive-ping task specifically,
    asyncio's own "exception in shielded future" handler) logs each as
    ERROR with a full traceback. The event is expected, non-actionable, and
    dwarfs real server errors in the panel. File handler still receives the
    record for audit.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — stdlib API
        if record.name not in _UVICORN_WS_NOISE_LOGGERS:
            return True
        msg = record.getMessage()
        if any(sig in msg for sig in _UVICORN_WS_NOISE_SIGS):
            return False
        # Also inspect chained exceptions if exc_info is present
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            exc_str = f'{type(exc).__name__}: {exc}'
            if any(sig in exc_str for sig in _UVICORN_WS_NOISE_SIGS):
                return False
        return True


class _PanelAllowlistFilter:
    """Allow only ``app.*`` records and real warnings/errors through.

    Used by both the stdout handler and the ring_buffer handler so the live
    log panel stays focused on application-level lifecycle events. The file
    handler does NOT use this filter — all records keep going to disk for
    audit / debug purposes.

    Rule
    ----
    * ``app`` and ``app.*`` loggers (our own code) → always pass.
    * Everything else (``uvicorn.*``, ``httpx``, ``alembic.*``,
      ``sqlalchemy``, etc.) → only WARNING or above passes; INFO-level
      infrastructure chatter is dropped.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — stdlib API
        # Always allow app.* records (our own code — lifecycle events).
        if record.name == 'app' or record.name.startswith('app.'):
            return True
        # For everything else, only allow WARNING+ through.
        return record.levelno >= logging.WARNING


def _safe_put_nowait(q: asyncio.Queue[T.Any], item: T.Any) -> None:
    """Put *item* on *q* without blocking; drop silently when full."""
    with contextlib.suppress(asyncio.QueueFull):
        q.put_nowait(item)


# ---------------------------------------------------------------------------
# Bootstrap helpers — parse existing log files into the ring buffer at startup
# ---------------------------------------------------------------------------

#: Matches a standard log line produced by LOG_FORMAT / DATE_FORMAT:
#:   ``YYYY-MM-DD HH:MM:SS  LEVEL  logger.name: message``
#: Two spaces separate each field in LOG_FORMAT.  ``%(levelname)-5s`` pads
#: short names (INFO → "INFO ") but not longer ones (WARNING → "WARNING").
#: The pattern uses ``\s{2,}`` between levelname and logger so it tolerates
#: both "INFO   name" (3 spaces) and "WARNING  name" (2 spaces).
_LOG_LINE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})  '
    r'(\w+)\s{2,}'
    r'([a-zA-Z0-9_.]+): '
    r'(.*)$'
)

#: Extract the sn field from a formatted message (e.g. "sn=42 Download…").
_SN_RE = re.compile(r'sn=(\d+)')


def _extract_sn(message: str) -> int | None:
    """Return the integer sn from *message* if present, else ``None``."""
    m = _SN_RE.search(message)
    return int(m.group(1)) if m else None


class RingBufferHandler(logging.Handler):
    """Thread-safe ring buffer + async subscribers fan-out.

    Buffers last N formatted records; every new record is pushed to any
    subscribed ``asyncio.Queue``s so WebSocket clients can stream logs.

    The handler is a singleton retrieved via :func:`get_ring_buffer_handler`
    so both the dictConfig machinery and the WS route share the same object.

    Deduplication
    -------------
    :class:`LogFileTailer` pushes entries parsed from the shared log file into
    this handler via :meth:`push_parsed_entry`.  Because the API process also
    emits its own records directly (uvicorn access, app.*), those records
    appear in the log file *and* arrive via ``emit()`` — creating potential
    duplicates.  To prevent that:

    * ``emit()`` records a compact ``(timestamp, name, message)`` key in
      ``_recent_keys`` (a ``deque`` with a fixed max-length acting as an
      approximate LRU set).
    * ``push_parsed_entry()`` checks the key before inserting — if the key
      is already in ``_recent_keys``, the entry is dropped as a duplicate.
    * A ``deque(maxlen=500)`` holds about 8+ minutes of typical log volume,
      which comfortably covers the 0.8-second tailer poll interval.
    """

    BUFFER_SIZE = 500

    #: Class-level flag — bootstrap is done at most once per process.
    _bootstrapped: bool = False

    #: Max number of recent-key slots used for dedup (approximate LRU set).
    _DEDUP_MAXLEN: int = 500

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._capacity = capacity
        self._buffer: collections.deque[dict[str, T.Any]] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._subscribers: set[asyncio.Queue[dict[str, T.Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None  # bound at subscribe
        # Dedup tracker: stores (timestamp, name, message) tuples for recently
        # emitted entries so push_parsed_entry can skip duplicates.
        self._recent_keys: collections.deque[tuple[str, str, str]] = collections.deque(maxlen=self._DEDUP_MAXLEN)

    @staticmethod
    def _key_of(entry: dict[str, T.Any]) -> tuple[str, str, str]:
        """Return a dedup key for *entry*: (timestamp_seconds, name, first-line-of-message).

        The timestamp from ``_format`` has microsecond precision (full UTC ISO
        string), whereas ``_parse_lines`` produces second-level precision from
        the on-disk ``%Y-%m-%d %H:%M:%S`` format.  To make the keys comparable
        across both sources we truncate to the first 19 characters
        (``YYYY-MM-DDTHH:MM:SS``), which is the common prefix.

        Only the first line of the message is used to avoid mismatches between
        multi-line traceback entries formatted by the logging system versus
        parsed back from the file.
        """
        ts_raw: str = str(entry.get('timestamp', ''))
        # Truncate to second precision: "YYYY-MM-DDTHH:MM:SS" (19 chars).
        ts: str = ts_raw[:19]
        name: str = str(entry.get('name', ''))
        message: str = str(entry.get('message', '')).split('\n', 1)[0]
        return (ts, name, message)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = self._format(record)
            with self._lock:
                self._buffer.append(entry)
                self._recent_keys.append(self._key_of(entry))
                for q in list(self._subscribers):
                    # Non-blocking put_nowait; schedule on the bound loop from
                    # the handler's thread (which may be a background thread).
                    if self._loop is not None and not self._loop.is_closed():
                        self._loop.call_soon_threadsafe(_safe_put_nowait, q, entry)
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def push_parsed_entry(self, entry: dict[str, T.Any]) -> None:
        """Inject an already-parsed entry (e.g. from :class:`LogFileTailer`).

        Apply the same panel allowlist that the ``emit()`` path enforces via
        the logging filter chain — tailed entries that originated in another
        process bypass the Python filter machinery, so we replicate the rules
        here.  Filtering is done before the dedup check so we do not waste
        ``_recent_keys`` slots on entries we are about to discard anyway.

        The entry is silently dropped when its key matches a recently emitted
        record (dedup against API-process-own logs).  Otherwise it is appended
        to the ring buffer and fan-out to all subscribers.
        """
        # --- WS noise filter (mirrors _UvicornWsNoiseFilter) ---
        name = str(entry.get('name', ''))
        if name in _UVICORN_WS_NOISE_LOGGERS:
            msg = str(entry.get('message', ''))
            if any(sig in msg for sig in _UVICORN_WS_NOISE_SIGS):
                return  # drop benign disconnect before dedup + append

        # --- Panel allowlist (mirrors _PanelAllowlistFilter) ---
        is_app = name == 'app' or name.startswith('app.')
        if not is_app:
            level = str(entry.get('level', '')).upper()
            _WARN_PLUS = {'WARNING', 'ERROR', 'CRITICAL', 'SUCCESS'}
            if level not in _WARN_PLUS:
                return

        key = self._key_of(entry)
        with self._lock:
            if key in self._recent_keys:
                return  # duplicate — the API process already emitted this
            self._buffer.append(entry)
            self._recent_keys.append(key)
            for q in list(self._subscribers):
                if self._loop is not None and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(_safe_put_nowait, q, entry)

    def _format(self, record: logging.LogRecord) -> dict[str, T.Any]:
        # Always use the raw message text — never the pre-formatted line that
        # embeds timestamp + level prefix.  Using self.format(record) when a
        # formatter is attached would produce a "2026-04-19 17:04:27  INFO
        # app.api._scheduler_proxy: …" string, and since the frontend renders
        # timestamp, level, and message fields independently that prefix would
        # appear twice in the UI.
        return {
            'timestamp': datetime.datetime.fromtimestamp(record.created, tz=datetime.UTC).isoformat(),
            'level': record.levelname,
            'name': record.name,
            'message': record.getMessage(),
            'sn': getattr(record, 'sn', None),
        }

    def snapshot(self) -> list[dict[str, T.Any]]:
        """Return a copy of the current ring buffer contents (oldest first)."""
        with self._lock:
            return list(self._buffer)

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue[dict[str, T.Any]]:
        """Register a subscriber queue and bind the event loop."""
        q: asyncio.Queue[dict[str, T.Any]] = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subscribers.add(q)
            self._loop = loop
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, T.Any]]) -> None:
        """Remove *q* from the subscriber set."""
        with self._lock:
            self._subscribers.discard(q)

    def bootstrap_from_file(self, logs_dir: pathlib.Path) -> int:
        """Populate the ring buffer from the most recent log file on disk.

        Reads the last :attr:`BUFFER_SIZE` lines from today's dated log file
        (``YYYY-MM-DD.log`` under *logs_dir*).  If today's file doesn't exist,
        falls back to yesterday's.  Lines are parsed with :data:`_LOG_LINE_RE`;
        unmatched lines (stack-trace continuations etc.) are appended to the
        previous entry's ``message``.

        The method is idempotent — a class-level flag ensures it runs **once
        per process** even if called from both the API lifespan and the
        scheduler lifespan (single-process deployment).

        Parameters
        ----------
        logs_dir:
            Directory that holds the dated ``.log`` files.

        Returns
        -------
        int
            Number of log entries loaded (0 when skipped or no file found).
        """
        # Guard: only bootstrap once per process.
        if RingBufferHandler._bootstrapped:
            return 0
        RingBufferHandler._bootstrapped = True

        log_path = self._find_log_file(logs_dir)
        if log_path is None:
            return 0

        try:
            raw_lines = self._tail_lines(log_path, self._capacity)
        except OSError:
            return 0

        entries = self._parse_lines(raw_lines)
        if not entries:
            return 0

        with self._lock:
            for entry in entries:
                self._buffer.append(entry)

        return len(entries)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _find_log_file(logs_dir: pathlib.Path) -> pathlib.Path | None:
        """Return today's log file, falling back to yesterday's."""
        today = datetime.datetime.now().date()
        for delta in (0, 1):
            day = today - datetime.timedelta(days=delta)
            path = logs_dir / f'{day.strftime("%Y-%m-%d")}.log'
            if path.is_file():
                return path
        return None

    @staticmethod
    def _tail_lines(path: pathlib.Path, n: int) -> list[str]:
        """Return the last *n* lines of *path* (UTF-8, best-effort)."""
        with open(path, encoding='utf-8', errors='replace') as fh:
            # For files that fit in memory this is fine; log files rarely
            # exceed a few MB so we read all at once rather than seeking.
            lines = fh.readlines()
        return [ln.rstrip('\n') for ln in lines[-n:]]

    @staticmethod
    def _parse_lines(lines: list[str]) -> list[dict[str, T.Any]]:
        """Parse *lines* into log-entry dicts matching :meth:`_format` shape."""
        entries: list[dict[str, T.Any]] = []
        for line in lines:
            m = _LOG_LINE_RE.match(line)
            if m:
                asctime, levelname, name, message = m.groups()
                try:
                    ts = (
                        datetime.datetime.strptime(asctime, '%Y-%m-%d %H:%M:%S')
                        .replace(tzinfo=datetime.UTC)
                        .isoformat()
                    )
                except ValueError:
                    ts = asctime
                entries.append(
                    {
                        'timestamp': ts,
                        'level': levelname.strip(),
                        'name': name,
                        'message': message,
                        'sn': _extract_sn(message),
                    }
                )
            else:
                # Continuation line (e.g. traceback) — attach to previous entry.
                if entries:
                    entries[-1]['message'] = entries[-1]['message'] + '\n' + line
        return entries


# Module-level singleton — created lazily so tests can import the module
# without triggering instantiation side-effects.
_ring_buffer_handler: RingBufferHandler | None = None


def get_ring_buffer_handler() -> RingBufferHandler:
    """Return (and lazily create) the module-level :class:`RingBufferHandler` singleton."""
    global _ring_buffer_handler
    if _ring_buffer_handler is None:
        _ring_buffer_handler = RingBufferHandler()
    return _ring_buffer_handler


class DailyLogFileHandler(logging.Handler):
    """Writes each record to ``{logs_dir}/{YYYY-MM-DD}.log`` as its own open/close.

    We deliberately open-and-close per emit rather than keeping a persistent
    stream (`TimedRotatingFileHandler` style) for two reasons:

    1. Rotation is implicit — the filename is recomputed every record, so no
       special-case ``doRollover`` logic is needed.
    2. On Windows, an always-open file handle prevents callers (notably pytest
       tmp_path cleanup and the test for ``FtpUploader.show_error_detail``)
       from ``unlink()``-ing today's log file. Per-emit open sidesteps the
       lock entirely.

    Size-based rollover
    --------------------
    In addition to the daily filename change, a single day's file is capped
    at :data:`_MAX_BYTES`. Once ``{date}.log`` reaches the cap, subsequent
    records for that day go to ``{date}.1.log``, then ``{date}.2.log``, and
    so on — this keeps any single file (and any single ``open(..., 'a')``
    call) bounded even on a very chatty day, and keeps ``tail``-ing /
    log-viewer tooling responsive. The active suffix is re-derived from disk
    on the first emit of each day (see :meth:`_resolve_suffix`) so a process
    restart mid-day resumes appending after the last rotated file instead of
    silently overwriting it.

    The ``_prune_old`` helper still trims files older than ``backupCount`` days
    whenever a record is emitted (cheap — the directory only holds a handful
    of dated files), and matches both the plain and size-rotated filenames.
    """

    #: Size cap per dated log file (including rotated suffixes). Once a file
    #: reaches this size, the next emit rolls over to the next suffix.
    _MAX_BYTES: T.ClassVar[int] = 100 * 1024 * 1024  # 100 MB

    #: Matches ``YYYY-MM-DD.log`` or a size-rotated ``YYYY-MM-DD.<N>.log``.
    _DATED_LOG_RE: T.ClassVar[re.Pattern[str]] = re.compile(r'^(\d{4}-\d{2}-\d{2})(?:\.\d+)?\.log$')

    def __init__(
        self,
        logs_dir: str | pathlib.Path,
        *,
        backupCount: int,  # noqa: N803 — match the stdlib kwarg casing
        encoding: str = 'utf-8',
    ) -> None:
        super().__init__()
        self._logs_dir = pathlib.Path(logs_dir)
        self.backupCount = max(0, int(backupCount))  # public for parity
        self._encoding = encoding
        self._io_lock = threading.Lock()
        self._last_pruned_date: datetime.date | None = None
        # Size-based rollover state: the suffix (0 = no suffix, i.e. plain
        # ``{date}.log``) currently being written to, and the date it
        # applies to. Re-derived from disk whenever the date changes —
        # including on the first emit of a freshly constructed handler.
        self._rollover_date: datetime.date | None = None
        self._rollover_suffix: int = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            today = datetime.datetime.now()
            today_date = today.date()
            with self._io_lock:
                if self._rollover_date != today_date:
                    self._rollover_date = today_date
                    self._rollover_suffix = self._resolve_suffix(today_date)

                path = self._file_path(today_date, self._rollover_suffix)
                self._logs_dir.mkdir(parents=True, exist_ok=True)
                with open(path, 'a', encoding=self._encoding) as fh:
                    fh.write(msg + '\n')

                if self._file_size(path) >= self._MAX_BYTES:
                    self._rollover_suffix += 1

                # Prune at most once per calendar day to keep emit cheap.
                if self._last_pruned_date != today_date:
                    self._prune_old(today_date)
                    self._last_pruned_date = today_date
        except Exception:  # noqa: BLE001 — logging must never crash callers
            self.handleError(record)

    def _file_path(self, day: datetime.date, suffix: int) -> pathlib.Path:
        """Return the log file path for *day* / *suffix* (0 = no suffix)."""
        stem = day.strftime('%Y-%m-%d')
        name = f'{stem}.log' if suffix == 0 else f'{stem}.{suffix}.log'
        return self._logs_dir / name

    @staticmethod
    def _file_size(path: pathlib.Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _resolve_suffix(self, day: datetime.date) -> int:
        """Return the suffix to (re)start writing to for *day*.

        Scans forward from suffix 0, skipping any file that already reached
        :data:`_MAX_BYTES`, so a freshly constructed handler (e.g. after a
        process restart) resumes appending to the correct rotated file
        instead of overwriting an already-full one.
        """
        suffix = 0
        while self._file_size(self._file_path(day, suffix)) >= self._MAX_BYTES:
            suffix += 1
        return suffix

    def _prune_old(self, today: datetime.date) -> None:
        """Delete dated ``.log`` files (including size-rotated suffixes) older than ``backupCount`` days."""
        if self.backupCount <= 0:
            return
        cutoff = today - datetime.timedelta(days=self.backupCount - 1)
        for entry in self._logs_dir.iterdir():
            if not entry.is_file():
                continue
            match = self._DATED_LOG_RE.match(entry.name)
            if not match:
                continue
            try:
                day = datetime.datetime.strptime(match.group(1), '%Y-%m-%d').date()
            except ValueError:
                continue
            if day < cutoff:
                with contextlib.suppress(OSError):
                    entry.unlink()


def build_log_config(
    paths: WorkspacePaths,
    *,
    save_logs: bool,
    quantity_of_logs: int,
) -> dict[str, T.Any]:
    """Return a ``logging.config.dictConfig`` dict.

    Parameters
    ----------
    paths:
        Workspace paths; ``paths.logs_dir`` hosts the rotating file handler.
    save_logs:
        When False, only the stdout handler is wired — no file handler is
        instantiated (so no empty ``logs/`` directory appears either).
    quantity_of_logs:
        Daily rotation retention (``TimedRotatingFileHandler.backupCount``).
        Clamped to >= 1 to match the legacy behaviour.
    """
    retention = max(1, int(quantity_of_logs))

    handlers: dict[str, dict[str, T.Any]] = {
        'stdout': {
            '()': 'rich.logging.RichHandler',
            'level': 'INFO',
            'formatter': 'rich',
            # Rich handles its own coloring; stdout_display honours
            # ``display=False``; cli_audit_noise drops only spam loggers.
            'filters': ['stdout_display', 'cli_audit_noise', 'uvicorn_ws_noise'],
            'rich_tracebacks': True,
            'show_path': False,
            'show_time': True,
            'markup': False,
            'log_time_format': '[%Y-%m-%d %H:%M:%S]',
            'omit_repeated_times': False,
        },
        # The ring buffer singleton is returned by the factory callable so
        # dictConfig reuses the module-level instance rather than constructing
        # a fresh one.  No formatter is attached: _format() always uses
        # record.getMessage() directly, so a formatter would only add noise.
        'ring_buffer': {
            '()': f'{__name__}.get_ring_buffer_handler',
            'level': 'INFO',
            'filters': ['panel_allowlist', 'uvicorn_ws_noise'],
        },
    }
    if save_logs:
        # Daily rotation at midnight; today's file lives at
        # ``logs_dir/YYYY-MM-DD.log`` to match the legacy pruner.
        handlers['file'] = {
            '()': f'{__name__}.DailyLogFileHandler',
            'level': 'INFO',
            'formatter': 'default',
            'logs_dir': str(paths.logs_dir),
            'backupCount': retention,
            'encoding': 'utf-8',
            'filters': ['file_display'],
        }
        # Ensure the logs directory exists before the handler opens the file.
        paths.logs_dir.mkdir(parents=True, exist_ok=True)

    handler_names = list(handlers.keys())

    config: dict[str, T.Any] = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            # Default format used for the file + ring buffer handlers. Keeps
            # the parseable shape that :class:`LogFileTailer` expects.
            'default': {
                'format': LOG_FORMAT,
                'datefmt': DATE_FORMAT,
            },
            # Rich handler adds its own time + level columns; we only need
            # ``name: message`` as the core payload.
            'rich': {
                'format': '%(name)s: %(message)s',
            },
        },
        'filters': {
            'stdout_display': {
                '()': f'{__name__}._DisplayFilter',
                'stdout': True,
            },
            'file_display': {
                '()': f'{__name__}._DisplayFilter',
                'stdout': False,
            },
            # Allowlist for the web log panel: pass app.* always; for
            # everything else (uvicorn.*, httpx, alembic, sqlalchemy, etc.)
            # only WARNING+ reaches the live panel.  NOT wired to stdout or
            # the file handler.
            'panel_allowlist': {
                '()': f'{__name__}._PanelAllowlistFilter',
            },
            # Lighter filter for CLI stdout: drops only high-frequency audit
            # noise (uvicorn.access, httpx, httpcore) at INFO; WARNING+ from
            # those loggers still passes.  uvicorn.error INFO passes so the
            # user sees startup/shutdown messages.
            'cli_audit_noise': {
                '()': f'{__name__}._CliAuditNoiseFilter',
            },
            # Drops benign uvicorn WS keepalive-timeout / no-close-frame
            # records from stdout and ring_buffer (non-actionable churn).
            # File handler is intentionally excluded for audit retention.
            'uvicorn_ws_noise': {
                '()': f'{__name__}._UvicornWsNoiseFilter',
            },
        },
        'handlers': handlers,
        'loggers': {
            'app': {
                'level': 'INFO',
                'handlers': handler_names,
                'propagate': False,
            },
            'uvicorn': {
                'level': 'INFO',
                'handlers': handler_names,
                'propagate': False,
            },
            'uvicorn.error': {
                'level': 'INFO',
                'handlers': handler_names,
                'propagate': False,
            },
            'uvicorn.access': {
                'level': 'INFO',
                'handlers': handler_names,
                'propagate': False,
            },
            'alembic': {
                'level': 'WARNING',
                'handlers': handler_names,
                'propagate': False,
            },
            'sqlalchemy': {
                'level': 'WARNING',
                'handlers': handler_names,
                'propagate': False,
            },
        },
        'root': {
            'level': 'INFO',
            'handlers': handler_names,
        },
    }
    return config


class LogFileTailer:
    """Background thread that tails today's log file and pushes new entries
    into the :class:`RingBufferHandler` fan-out.

    Polls file size every :attr:`POLL_INTERVAL_S` seconds; on growth, reads new
    bytes, parses complete lines, and injects entries via
    :meth:`RingBufferHandler.push_parsed_entry` (which performs dedup so records
    already emitted by this process are not delivered twice).

    Midnight rollover is handled transparently: when the date changes the tailer
    switches to the new day's file and reads it from position 0.

    Windows note
    ------------
    ``os.stat().st_ino`` is always 0 on Windows (NTFS inodes are not exposed
    via the CRT).  Rotation / truncation detection therefore uses a
    ``(st_size, st_mtime_ns)`` identity tuple instead of inode.
    """

    POLL_INTERVAL_S: float = 0.8

    def __init__(self, logs_dir: pathlib.Path, handler: RingBufferHandler) -> None:
        self._logs_dir = pathlib.Path(logs_dir)
        self._handler = handler
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current_path: pathlib.Path | None = None
        self._pos: int = 0
        self._line_buffer: str = ''  # partial-line carryover across reads

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background tail thread (idempotent)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name='log-file-tailer',
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the tail thread to stop and wait up to 2 seconds."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        # Seek to end-of-file on start so we only tail *new* content.
        # Historical content is already loaded by bootstrap_from_file.
        path = self._today_path()
        if path.exists():
            self._current_path = path
            self._pos = path.stat().st_size

        while not self._stop.wait(self.POLL_INTERVAL_S):
            with contextlib.suppress(Exception):  # noqa: BLE001 — tailer must never crash on transient I/O errors
                self._poll_once()

    def _poll_once(self) -> None:
        path = self._today_path()

        # Day rollover: new date → new file, read from beginning.
        if self._current_path != path:
            self._current_path = path
            self._pos = 0
            self._line_buffer = ''

        if not path.exists():
            return

        size = path.stat().st_size

        # File was truncated or rotated mid-day → reset to start.
        if size < self._pos:
            self._pos = 0
            self._line_buffer = ''

        if size <= self._pos:
            return  # nothing new

        with path.open('r', encoding='utf-8', errors='replace') as fh:
            fh.seek(self._pos)
            new_text = fh.read()
            self._pos = fh.tell()

        # Prepend any incomplete line from the previous read.
        text = self._line_buffer + new_text
        lines = text.split('\n')
        # The last element is either empty (text ended with \n) or a
        # partial line that hasn't been terminated yet — carry it over.
        self._line_buffer = lines[-1]
        complete = lines[:-1]

        if not complete:
            return

        entries = self._handler._parse_lines(complete)
        for entry in entries:
            self._handler.push_parsed_entry(entry)

    def _today_path(self) -> pathlib.Path:
        """Return the path for today's log file (recomputed each call for rollover detection)."""
        day = datetime.datetime.now().strftime('%Y-%m-%d')
        return self._logs_dir / f'{day}.log'


__all__ = [
    'LOG_FORMAT',
    'DATE_FORMAT',
    'RingBufferHandler',
    'get_ring_buffer_handler',
    'LogFileTailer',
    'build_log_config',
    'DailyLogFileHandler',
]

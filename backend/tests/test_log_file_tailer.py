"""Tests for :class:`app.log_config.LogFileTailer`.

Covers:
* New lines written to the file are pushed into the RingBufferHandler.
* Partial lines (no trailing newline yet) are held until completed.
* Midnight day rollover: new file path → reads from position 0.
* Dedup: a line already emitted via emit() is not pushed twice.
* Missing / deleted file does not crash the tailer.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import pathlib
import threading
import time
import typing as T

from app.log_config import LogFileTailer, RingBufferHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler() -> RingBufferHandler:
    h = RingBufferHandler(capacity=500)
    h.setFormatter(logging.Formatter('%(message)s'))
    return h


def _today_name() -> str:
    return datetime.datetime.now().strftime('%Y-%m-%d')


def _log_line(
    message: str,
    dt: str = '2026-04-18 10:00:00',
    level: str = 'INFO ',
    name: str = 'app.scheduler',
) -> str:
    """Build one formatted log line matching LOG_FORMAT."""
    return f'{dt}  {level}  {name}: {message}'


def _wait_for(condition: T.Callable[[], bool], *, timeout: float = 3.0, interval: float = 0.05) -> bool:
    """Spin-wait until *condition* is True or *timeout* is reached."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# test_tailer_reads_new_lines
# ---------------------------------------------------------------------------


def test_tailer_reads_new_lines(tmp_path: pathlib.Path) -> None:
    """Lines appended to today's log file reach the ring buffer."""
    handler = _make_handler()
    log_file = tmp_path / f'{_today_name()}.log'

    # Pre-create with one line; tailer will start positioned at EOF.
    log_file.write_text(_log_line('old line') + '\n', encoding='utf-8')

    tailer = LogFileTailer(tmp_path, handler)
    tailer.start()
    try:
        # Give the tailer time to open and seek to EOF.
        time.sleep(0.2)

        # Append a new line; should be picked up within the poll interval.
        with log_file.open('a', encoding='utf-8') as fh:
            fh.write(_log_line('new scheduler event') + '\n')

        assert _wait_for(lambda: any('new scheduler event' in e['message'] for e in handler.snapshot())), (
            'Tailer did not push the new line into the ring buffer within timeout'
        )

        # The pre-existing 'old line' must NOT be in the buffer (tailer
        # started at EOF, so historical content is excluded).
        messages = [e['message'] for e in handler.snapshot()]
        assert not any('old line' in m for m in messages)
    finally:
        tailer.stop()


# ---------------------------------------------------------------------------
# test_tailer_handles_partial_line
# ---------------------------------------------------------------------------


def test_tailer_handles_partial_line(tmp_path: pathlib.Path) -> None:
    """A partial line (no trailing newline) is held until the line is complete."""
    handler = _make_handler()
    log_file = tmp_path / f'{_today_name()}.log'
    log_file.write_text('', encoding='utf-8')

    tailer = LogFileTailer(tmp_path, handler)
    tailer.start()
    try:
        time.sleep(0.2)  # let tailer reach EOF (empty file)

        # Write the beginning of a log line without the trailing newline.
        partial = _log_line('download complete')
        with log_file.open('a', encoding='utf-8') as fh:
            fh.write(partial)  # no \n yet

        # Wait longer than two poll cycles; should NOT be pushed yet.
        time.sleep(LogFileTailer.POLL_INTERVAL_S * 2 + 0.2)
        assert handler.snapshot() == [], 'Partial line (no newline) must not be pushed to ring buffer'

        # Complete the line.
        with log_file.open('a', encoding='utf-8') as fh:
            fh.write('\n')

        assert _wait_for(lambda: any('download complete' in e['message'] for e in handler.snapshot())), (
            'Complete line was not pushed after newline was written'
        )
    finally:
        tailer.stop()


# ---------------------------------------------------------------------------
# test_tailer_handles_day_rollover
# ---------------------------------------------------------------------------


def test_tailer_handles_day_rollover(tmp_path: pathlib.Path) -> None:
    """When the tailer's _today_path switches, it reads the new file from position 0."""
    handler = _make_handler()

    # Create two fake "day" files.
    old_file = tmp_path / '2026-04-18.log'
    new_file = tmp_path / '2026-04-19.log'
    old_file.write_text(_log_line('old day line') + '\n', encoding='utf-8')
    new_file.write_text(_log_line('new day line') + '\n', encoding='utf-8')

    # Use a mutable container so the lambda closure can flip the flag.
    _state: dict[str, bool] = {'use_new_day': False}

    # Subclass to control which file the tailer considers "today".
    class _PatchedTailer(LogFileTailer):
        def _today_path(self) -> pathlib.Path:  # type: ignore[override]
            if _state['use_new_day']:
                return new_file
            return old_file

    tailer = _PatchedTailer(tmp_path, handler)
    tailer.start()
    try:
        # Let tailer initialise on old_file and seek to EOF.
        time.sleep(0.2)

        # Flip to new day — next _poll_once should reset pos and read new_file.
        _state['use_new_day'] = True

        assert _wait_for(lambda: any('new day line' in e['message'] for e in handler.snapshot())), (
            "Tailer did not read the new day's file after rollover"
        )
    finally:
        tailer.stop()


# ---------------------------------------------------------------------------
# test_tailer_dedup
# ---------------------------------------------------------------------------


def test_tailer_dedup(tmp_path: pathlib.Path) -> None:
    """A line emitted via emit() and also tailed from file appears only once.

    Dedup matches on (timestamp-truncated-to-seconds, name, first-line-of-message).
    We build a matching parsed entry by formatting the emitted entry's timestamp
    back to ``YYYY-MM-DD HH:MM:SS`` (the on-disk format) and re-parsing it.
    """
    handler = _make_handler()

    # Emit a record directly — registers its key in _recent_keys.
    record = logging.LogRecord(
        name='app.scheduler',
        level=logging.INFO,
        pathname='',
        lineno=0,
        msg='duplicate test message',
        args=(),
        exc_info=None,
    )
    handler.emit(record)
    assert len(handler.snapshot()) == 1

    # Reconstruct a log-file line that would produce the same dedup key.
    # The key uses timestamp[:19] = "YYYY-MM-DDTHH:MM:SS", so we need to
    # build a file line with the same wall-clock second.
    emitted = handler.snapshot()[0]
    # emitted["timestamp"] is like "2026-04-18T10:00:00.123456+00:00"
    # Convert back to the file format "YYYY-MM-DD HH:MM:SS".
    ts_iso: str = emitted['timestamp'][:19]  # "2026-04-18T10:00:00"
    ts_file = ts_iso.replace('T', ' ')  # "2026-04-18 10:00:00"
    line = f'{ts_file}  INFO   app.scheduler: duplicate test message'
    entries = handler._parse_lines([line])
    assert entries, 'Failed to parse constructed log line'
    for entry in entries:
        handler.push_parsed_entry(entry)

    # The buffer must still contain exactly one entry.
    assert len(handler.snapshot()) == 1, 'push_parsed_entry must deduplicate lines already emitted via emit()'


# ---------------------------------------------------------------------------
# test_tailer_survives_missing_file
# ---------------------------------------------------------------------------


def test_tailer_survives_missing_file(tmp_path: pathlib.Path) -> None:
    """poll_once must not raise when today's log file doesn't exist."""
    handler = _make_handler()

    # Point tailer at tmp_path/logs which has no log file.
    logs_dir = tmp_path / 'logs'
    logs_dir.mkdir()

    tailer = LogFileTailer(logs_dir, handler)
    tailer.start()
    try:
        # Let it poll a few times on a missing file without crashing.
        time.sleep(LogFileTailer.POLL_INTERVAL_S * 3 + 0.2)
        # No assertion needed — if the tailer thread died it would cause
        # spurious failures in subsequent assertions; confirm it's alive.
        assert tailer._thread is not None and tailer._thread.is_alive(), 'Tailer thread must survive missing log file'
    finally:
        tailer.stop()


# ---------------------------------------------------------------------------
# test_tailer_start_is_idempotent
# ---------------------------------------------------------------------------


def test_tailer_start_is_idempotent(tmp_path: pathlib.Path) -> None:
    """Calling start() twice must not create a second thread."""
    handler = _make_handler()
    tailer = LogFileTailer(tmp_path, handler)
    tailer.start()
    first_thread = tailer._thread
    tailer.start()  # second call — must be a no-op
    assert tailer._thread is first_thread, 'start() must be idempotent (no new thread)'
    tailer.stop()


# ---------------------------------------------------------------------------
# test_tailer_fan_out_to_ws_subscriber
# ---------------------------------------------------------------------------


def test_tailer_fan_out_to_ws_subscriber(tmp_path: pathlib.Path) -> None:
    """Lines injected by the tailer reach asyncio subscriber queues."""
    handler = _make_handler()
    log_file = tmp_path / f'{_today_name()}.log'
    log_file.write_text('', encoding='utf-8')

    tailer = LogFileTailer(tmp_path, handler)
    tailer.start()

    received: list[dict[str, T.Any]] = []

    async def _collect() -> None:
        loop = asyncio.get_running_loop()
        q = handler.subscribe(loop)

        # Append a new line from a thread after the subscriber is set up.
        def _write() -> None:
            time.sleep(0.2)
            with log_file.open('a', encoding='utf-8') as fh:
                fh.write(_log_line('fan-out test') + '\n')

        threading.Thread(target=_write, daemon=True).start()

        try:
            item = await asyncio.wait_for(q.get(), timeout=3.0)
            received.append(item)  # type: ignore[arg-type]
        except asyncio.TimeoutError:
            pass
        finally:
            handler.unsubscribe(q)

    try:
        asyncio.run(_collect())
    finally:
        tailer.stop()

    assert received, 'Tailer-injected entry must reach asyncio subscriber queue'
    assert 'fan-out test' in received[0]['message']

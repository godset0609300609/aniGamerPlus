"""Tests for :class:`app.log_config.RingBufferHandler`.

Covers the ring buffer logic, thread-safety, and subscribe/unsubscribe
mechanics.  The asyncio fan-out path is tested in test_logs_ws.py via the
FastAPI test client.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import pathlib
import threading
import time

import pytest

from app.log_config import RingBufferHandler, _safe_put_nowait

_TODAY = datetime.datetime.now().date().strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler(capacity: int = 500) -> RingBufferHandler:
    """Return a fresh handler with a plain message formatter."""
    handler = RingBufferHandler(capacity=capacity)
    handler.setFormatter(logging.Formatter('%(message)s'))
    return handler


def _emit(handler: RingBufferHandler, msg: str, level: int = logging.INFO) -> None:
    record = logging.LogRecord(
        name='test',
        level=level,
        pathname='',
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    handler.emit(record)


def _emit_with_sn(handler: RingBufferHandler, msg: str, sn: int) -> None:
    record = logging.LogRecord(
        name='test',
        level=logging.INFO,
        pathname='',
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    record.sn = sn  # type: ignore[attr-defined]
    handler.emit(record)


# ---------------------------------------------------------------------------
# snapshot() — basic buffering
# ---------------------------------------------------------------------------


def test_snapshot_empty_initially() -> None:
    handler = _make_handler()
    assert handler.snapshot() == []


def test_snapshot_returns_emitted_records() -> None:
    handler = _make_handler()
    _emit(handler, 'hello')
    _emit(handler, 'world')

    snap = handler.snapshot()
    assert len(snap) == 2
    assert snap[0]['message'] == 'hello'
    assert snap[1]['message'] == 'world'


def test_snapshot_returns_copy() -> None:
    """Mutating the returned list must not affect the internal buffer."""
    handler = _make_handler()
    _emit(handler, 'a')
    snap = handler.snapshot()
    snap.clear()
    assert len(handler.snapshot()) == 1


# ---------------------------------------------------------------------------
# Ring-buffer capacity — old records are dropped
# ---------------------------------------------------------------------------


def test_buffer_drops_oldest_when_full() -> None:
    capacity = 5
    handler = _make_handler(capacity=capacity)
    for i in range(capacity + 2):
        _emit(handler, f'msg-{i}')

    snap = handler.snapshot()
    assert len(snap) == capacity
    # Oldest two messages (msg-0, msg-1) should have been evicted.
    messages = [e['message'] for e in snap]
    assert 'msg-0' not in messages
    assert 'msg-1' not in messages
    assert 'msg-6' in messages


def test_buffer_size_constant_matches_default() -> None:
    handler = RingBufferHandler()
    assert handler._capacity == RingBufferHandler.BUFFER_SIZE


# ---------------------------------------------------------------------------
# Entry shape
# ---------------------------------------------------------------------------


def test_entry_has_required_fields() -> None:
    handler = _make_handler()
    _emit(handler, 'check fields')
    entry = handler.snapshot()[0]
    assert 'timestamp' in entry
    assert 'level' in entry
    assert 'name' in entry
    assert 'message' in entry
    assert 'sn' in entry


def test_entry_level_name() -> None:
    handler = _make_handler()
    _emit(handler, 'warn me', level=logging.WARNING)
    entry = handler.snapshot()[0]
    assert entry['level'] == 'WARNING'


def test_entry_sn_none_when_not_set() -> None:
    handler = _make_handler()
    _emit(handler, 'no sn')
    assert handler.snapshot()[0]['sn'] is None


def test_entry_sn_present_when_set() -> None:
    handler = _make_handler()
    _emit_with_sn(handler, 'with sn', sn=12345)
    assert handler.snapshot()[0]['sn'] == 12345


def test_entry_timestamp_is_utc_iso() -> None:
    import datetime

    handler = _make_handler()
    _emit(handler, 'ts test')
    ts = handler.snapshot()[0]['timestamp']
    # Must parse as a UTC-aware datetime.
    parsed = datetime.datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------


def test_subscribe_returns_asyncio_queue() -> None:
    handler = _make_handler()
    loop = asyncio.new_event_loop()
    try:
        q = handler.subscribe(loop)
        assert isinstance(q, asyncio.Queue)
    finally:
        loop.close()


def test_unsubscribe_removes_queue() -> None:
    handler = _make_handler()
    loop = asyncio.new_event_loop()
    try:
        q = handler.subscribe(loop)
        assert q in handler._subscribers
        handler.unsubscribe(q)
        assert q not in handler._subscribers
    finally:
        loop.close()


def test_unsubscribe_nonexistent_is_noop() -> None:
    handler = _make_handler()
    loop = asyncio.new_event_loop()
    try:
        q: asyncio.Queue[object] = asyncio.Queue()
        # Should not raise.
        handler.unsubscribe(q)  # type: ignore[arg-type]
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fan-out to subscribers — asyncio path
# ---------------------------------------------------------------------------


def test_emit_delivers_to_subscriber_via_event_loop() -> None:
    """Records emitted from a background thread reach the subscriber queue."""
    handler = _make_handler()

    async def _run() -> list[dict[str, object]]:
        loop = asyncio.get_running_loop()
        q = handler.subscribe(loop)
        # Emit from a background thread (simulating the logging system).
        threading.Thread(target=_emit, args=(handler, 'async test')).start()
        # Give the call_soon_threadsafe a moment to fire.
        await asyncio.sleep(0.05)
        results = []
        while not q.empty():
            results.append(q.get_nowait())
        handler.unsubscribe(q)
        return results

    results = asyncio.run(_run())
    assert len(results) == 1
    assert results[0]['message'] == 'async test'


def test_emit_delivers_to_multiple_subscribers() -> None:
    """All subscribed queues receive each record."""
    handler = _make_handler()

    async def _run() -> tuple[int, int]:
        loop = asyncio.get_running_loop()
        q1 = handler.subscribe(loop)
        q2 = handler.subscribe(loop)
        threading.Thread(target=_emit, args=(handler, 'broadcast')).start()
        await asyncio.sleep(0.05)
        count1 = q1.qsize()
        count2 = q2.qsize()
        handler.unsubscribe(q1)
        handler.unsubscribe(q2)
        return count1, count2

    c1, c2 = asyncio.run(_run())
    assert c1 == 1
    assert c2 == 1


# ---------------------------------------------------------------------------
# _safe_put_nowait
# ---------------------------------------------------------------------------


def test_safe_put_nowait_drops_when_full() -> None:
    async def _run() -> None:
        q: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
        q.put_nowait(1)  # fill it
        # Second put on a full queue must not raise.
        _safe_put_nowait(q, 2)
        assert q.qsize() == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Thread-safety: concurrent emits from multiple threads
# ---------------------------------------------------------------------------


def test_concurrent_emits_are_thread_safe() -> None:
    """Multiple threads emitting simultaneously must not corrupt the buffer."""
    handler = _make_handler(capacity=1000)
    n_threads = 10
    n_per_thread = 50

    def worker(idx: int) -> None:
        for j in range(n_per_thread):
            _emit(handler, f't{idx}-{j}')

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = handler.snapshot()
    # Should have exactly min(n_threads * n_per_thread, 1000) entries.
    expected = min(n_threads * n_per_thread, 1000)
    assert len(snap) == expected


# ---------------------------------------------------------------------------
# RingBufferHandler via dictConfig — singleton reuse
# ---------------------------------------------------------------------------


def test_get_ring_buffer_handler_returns_singleton() -> None:
    """get_ring_buffer_handler() must return the same instance on repeated calls."""
    import app.log_config as _lc

    # Reset to force fresh creation.
    _lc._ring_buffer_handler = None

    h1 = _lc.get_ring_buffer_handler()
    h2 = _lc.get_ring_buffer_handler()
    assert h1 is h2


def test_dictconfig_ring_buffer_factory_reuses_singleton() -> None:
    """dictConfig with '()': get_ring_buffer_handler must wire the singleton."""
    import logging.config

    import app.log_config as _lc

    # Reset to ensure a clean singleton.
    _lc._ring_buffer_handler = None
    singleton_before = _lc.get_ring_buffer_handler()

    cfg: dict[str, object] = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {'plain': {'format': '%(message)s'}},
        'handlers': {
            'ring_buffer': {
                '()': f'{_lc.__name__}.get_ring_buffer_handler',
                'level': 'INFO',
                'formatter': 'plain',
            }
        },
        'loggers': {
            'test_dictconfig': {
                'level': 'INFO',
                'handlers': ['ring_buffer'],
                'propagate': False,
            }
        },
    }
    logging.config.dictConfig(cfg)

    # Emit via the configured logger.
    test_logger = logging.getLogger('test_dictconfig')
    test_logger.info('via dictconfig')

    # The singleton should have the record.
    snap = singleton_before.snapshot()
    messages = [e['message'] for e in snap]
    assert 'via dictconfig' in messages


# ---------------------------------------------------------------------------
# subscribe → emit → queue.get() round-trip
# ---------------------------------------------------------------------------


def test_subscribe_emit_queue_roundtrip() -> None:
    """subscribe() → emit (from thread) → queue.get() must deliver the record."""
    handler = _make_handler()

    async def _run() -> dict[str, object]:
        loop = asyncio.get_running_loop()
        q = handler.subscribe(loop)
        # Emit from a background thread.
        threading.Thread(target=_emit, args=(handler, 'roundtrip msg')).start()
        # Wait for the item to arrive.
        item: dict[str, object] = await asyncio.wait_for(q.get(), timeout=1.0)
        handler.unsubscribe(q)
        return item

    result = asyncio.run(_run())
    assert result['message'] == 'roundtrip msg'


# ---------------------------------------------------------------------------
# bootstrap_from_file — helpers
# ---------------------------------------------------------------------------


def _make_log_line(
    dt: str = '2026-04-18 22:01:23',
    level: str = 'INFO ',
    name: str = 'app.main',
    message: str = 'hello',
) -> str:
    """Return one formatted log line matching LOG_FORMAT / DATE_FORMAT."""
    # LOG_FORMAT = "%(asctime)s  %(levelname)-5s  %(name)s: %(message)s"
    # %(levelname)-5s pads to 5 chars with spaces on the right.
    return f'{dt}  {level}  {name}: {message}'


def _reset_bootstrapped(handler: RingBufferHandler) -> None:
    """Reset the class-level flag so tests can call bootstrap_from_file again."""
    RingBufferHandler._bootstrapped = False


# ---------------------------------------------------------------------------
# bootstrap_from_file tests
# ---------------------------------------------------------------------------


def test_bootstrap_from_file_reads_last_n_lines(tmp_path: pathlib.Path) -> None:
    """Writing 1 000 lines → bootstrap reads at most BUFFER_SIZE (500) entries."""
    log_file = tmp_path / f'{_TODAY}.log'
    lines = [_make_log_line(message=f'msg-{i}') for i in range(1000)]
    log_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    handler = _make_handler(capacity=500)
    _reset_bootstrapped(handler)

    n = handler.bootstrap_from_file(tmp_path)
    assert n == 500
    snap = handler.snapshot()
    assert len(snap) == 500
    # Only the last 500 messages should be present.
    messages = [e['message'] for e in snap]
    assert 'msg-0' not in messages
    assert 'msg-999' in messages


def test_bootstrap_parses_log_line_correctly(tmp_path: pathlib.Path) -> None:
    """A single log line is parsed into the correct entry shape."""
    log_file = tmp_path / f'{_TODAY}.log'
    log_file.write_text(
        _make_log_line(
            dt='2026-04-18 22:01:23',
            level='INFO ',
            name='uvicorn.error',
            message='Started server process [12345]',
        )
        + '\n',
        encoding='utf-8',
    )

    handler = _make_handler()
    _reset_bootstrapped(handler)

    n = handler.bootstrap_from_file(tmp_path)
    assert n == 1
    entry = handler.snapshot()[0]
    assert entry['level'] == 'INFO'
    assert entry['name'] == 'uvicorn.error'
    assert 'Started server process' in entry['message']
    # Timestamp should be an ISO-8601 string with UTC offset.
    assert entry['timestamp'].startswith('2026-04-18T22:01:23')
    assert entry['sn'] is None


def test_bootstrap_attaches_multiline_to_previous(tmp_path: pathlib.Path) -> None:
    """Stack-trace continuation lines are appended to the previous entry."""
    log_file = tmp_path / f'{_TODAY}.log'
    log_file.write_text(
        '\n'.join(
            [
                _make_log_line(level='ERROR', name='app.main', message='Oops'),
                'Traceback (most recent call last):',
                '  File "foo.py", line 1, in <module>',
                'ValueError: bad value',
            ]
        )
        + '\n',
        encoding='utf-8',
    )

    handler = _make_handler()
    _reset_bootstrapped(handler)

    n = handler.bootstrap_from_file(tmp_path)
    assert n == 1
    entry = handler.snapshot()[0]
    assert 'Oops' in entry['message']
    assert 'Traceback' in entry['message']
    assert 'ValueError' in entry['message']


def test_bootstrap_skips_when_no_log_files(tmp_path: pathlib.Path) -> None:
    """Empty logs directory returns 0 entries loaded."""
    handler = _make_handler()
    _reset_bootstrapped(handler)

    n = handler.bootstrap_from_file(tmp_path)
    assert n == 0
    assert handler.snapshot() == []


def test_bootstrap_only_runs_once(tmp_path: pathlib.Path) -> None:
    """Second call to bootstrap_from_file returns 0 (already bootstrapped)."""
    log_file = tmp_path / f'{_TODAY}.log'
    log_file.write_text(
        _make_log_line(message='first') + '\n',
        encoding='utf-8',
    )

    handler = _make_handler()
    _reset_bootstrapped(handler)

    n1 = handler.bootstrap_from_file(tmp_path)
    assert n1 == 1

    # Second call — even with the same handler — must be a no-op.
    n2 = handler.bootstrap_from_file(tmp_path)
    assert n2 == 0

    # Buffer should still only have the one entry from the first call.
    assert len(handler.snapshot()) == 1


def test_bootstrap_extracts_sn_field(tmp_path: pathlib.Path) -> None:
    """A message containing 'sn=42' yields entry['sn'] == 42."""
    log_file = tmp_path / f'{_TODAY}.log'
    log_file.write_text(
        _make_log_line(message='sn=42 下載完成') + '\n',
        encoding='utf-8',
    )

    handler = _make_handler()
    _reset_bootstrapped(handler)

    handler.bootstrap_from_file(tmp_path)
    entry = handler.snapshot()[0]
    assert entry['sn'] == 42


def test_bootstrap_falls_back_to_yesterday(tmp_path: pathlib.Path) -> None:
    """When today's file is absent, yesterday's log file is used."""
    yesterday = (datetime.datetime.now().date() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    log_file = tmp_path / f'{yesterday}.log'
    log_file.write_text(
        _make_log_line(message='yesterday msg') + '\n',
        encoding='utf-8',
    )

    handler = _make_handler()
    _reset_bootstrapped(handler)

    n = handler.bootstrap_from_file(tmp_path)
    assert n == 1
    assert handler.snapshot()[0]['message'] == 'yesterday msg'


# ---------------------------------------------------------------------------
# push_parsed_entry — fan-out and dedup
# ---------------------------------------------------------------------------


def test_push_parsed_entry_fans_out_to_subscribers() -> None:
    """push_parsed_entry must deliver the entry to asyncio subscriber queues."""
    handler = _make_handler()

    async def _run() -> dict[str, object]:
        loop = asyncio.get_running_loop()
        q = handler.subscribe(loop)
        entry: dict[str, object] = {
            'timestamp': '2026-04-18T10:00:00+00:00',
            'level': 'INFO',
            'name': 'app.scheduler',
            'message': 'download complete',
            'sn': 42,
        }
        # push_parsed_entry is called from a background thread in production;
        # call it directly here for simplicity (it's thread-safe).
        handler.push_parsed_entry(entry)
        item = await asyncio.wait_for(q.get(), timeout=1.0)
        handler.unsubscribe(q)
        return item  # type: ignore[return-value]

    result = asyncio.run(_run())
    assert result['message'] == 'download complete'
    assert result['sn'] == 42


def test_push_parsed_entry_dedupes_recent_emit() -> None:
    """push_parsed_entry must drop entries whose key was already seen via emit()."""
    handler = _make_handler()

    # Emit a record directly — registers its key in _recent_keys.
    record = logging.LogRecord(
        name='app.main',
        level=logging.INFO,
        pathname='',
        lineno=0,
        msg='duplicated entry',
        args=(),
        exc_info=None,
    )
    handler.emit(record)
    assert len(handler.snapshot()) == 1

    # Build an entry that matches the same (timestamp, name, message) key.
    # We use _key_of on the emitted entry to get the exact key.
    emitted = handler.snapshot()[0]
    dup_entry: dict[str, object] = dict(emitted)

    handler.push_parsed_entry(dup_entry)

    # Buffer must still contain exactly one record.
    assert len(handler.snapshot()) == 1, 'push_parsed_entry must deduplicate entries already seen via emit()'


def test_push_parsed_entry_inserts_novel_entry() -> None:
    """push_parsed_entry must accept entries not seen via emit()."""
    handler = _make_handler()

    entry: dict[str, object] = {
        'timestamp': '2026-04-18T11:00:00+00:00',
        'level': 'INFO',
        'name': 'app.scheduler.update_loop',
        'message': '新任務已加入隊列',
        'sn': None,
    }
    handler.push_parsed_entry(entry)

    snap = handler.snapshot()
    assert len(snap) == 1
    assert snap[0]['message'] == '新任務已加入隊列'

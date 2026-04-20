"""Tests for ``app.logging_.Logger``."""

from __future__ import annotations

import datetime
import pathlib
import re
import threading

import pytest

from app.logging_ import Logger, LogLevel, LogRecord

_LINE_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} .*$')


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=True, quantity_of_logs=7)


def _today_log(logs_dir: pathlib.Path) -> pathlib.Path:
    return logs_dir / (datetime.datetime.now().strftime('%Y-%m-%d') + '.log')


def test_info_error_success_all_write_to_todays_file(
    tmp_path: pathlib.Path, logger: Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    logger.info(12345, 'tag-info', 'detail one')
    logger.error(None, 'tag-error', 'detail two')
    logger.success(42, 'tag-success', '')

    log_path = _today_log(tmp_path / 'logs')
    assert log_path.exists(), "today's log file should be created on first write"

    lines = log_path.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 3
    for line in lines:
        assert _LINE_RE.match(line), f'line did not match format: {line!r}'


def test_sn_none_omits_sn_prefix(tmp_path: pathlib.Path, logger: Logger) -> None:
    logger.info(None, 'noun', 'body', display=False)
    log_path = _today_log(tmp_path / 'logs')
    line = log_path.read_text(encoding='utf-8').splitlines()[0]
    assert 'sn=' not in line
    assert line.endswith('noun body')


def test_integer_sn_renders_as_sn_equals(tmp_path: pathlib.Path, logger: Logger) -> None:
    logger.error(12345, 'oops', 'something broke', display=False)
    log_path = _today_log(tmp_path / 'logs')
    line = log_path.read_text(encoding='utf-8').splitlines()[0]
    assert ' sn=12345 ' in line
    assert line.endswith('oops something broke')


def test_display_time_false_omits_timestamp(tmp_path: pathlib.Path, logger: Logger) -> None:
    logger.info(None, 'bare', display=False, display_time=False)
    log_path = _today_log(tmp_path / 'logs')
    line = log_path.read_text(encoding='utf-8').splitlines()[0]
    assert line == 'bare'


def test_prune_old_logs_removes_dated_files_and_keeps_web_log(
    tmp_path: pathlib.Path,
) -> None:
    logs_dir = tmp_path / 'logs'
    logs_dir.mkdir()

    today = datetime.datetime.now().date()
    old_day = today - datetime.timedelta(days=30)
    recent_day = today - datetime.timedelta(days=1)

    old_log = logs_dir / f'{old_day.strftime("%Y-%m-%d")}.log'
    recent_log = logs_dir / f'{recent_day.strftime("%Y-%m-%d")}.log'
    web_log = logs_dir / 'web.log'
    stray = logs_dir / 'notes.txt'
    for p in (old_log, recent_log, web_log, stray):
        p.write_text('x', encoding='utf-8')

    Logger(logs_dir, save_logs=True, quantity_of_logs=7).prune_old_logs()

    assert not old_log.exists(), 'file older than quantity_of_logs should be removed'
    assert recent_log.exists(), 'recent dated log should survive'
    assert web_log.exists(), 'web.log is explicitly excluded'
    assert stray.exists(), 'non-dated files must never be touched'


def test_thread_safety_20_threads_writing_info(tmp_path: pathlib.Path) -> None:
    lg = Logger(tmp_path / 'logs', save_logs=True, quantity_of_logs=7)

    def do_work(i: int) -> None:
        lg.info(i, 'thread', f'message {i}', display=False)

    threads = [threading.Thread(target=do_work, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    log_path = _today_log(tmp_path / 'logs')
    lines = log_path.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 20, f'expected 20 lines, got {len(lines)}'
    # No interleaving: every line is a complete, well-formed record.
    for line in lines:
        assert _LINE_RE.match(line), f'interleaving corrupted line: {line!r}'


def test_logrecord_dataclass_fields() -> None:
    # Sanity: the dataclass exposes the 6 expected fields with the right
    # defaults. Exercised indirectly everywhere but asserted here so the
    # public surface is pinned.
    record = LogRecord(sn=1, tag='t', detail='d', level=LogLevel.INFO, display=True)
    assert record.display_time is True
    assert record.level is LogLevel.INFO

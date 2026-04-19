"""Tests that ``anigamerplus --help`` does not boot the container.

Legacy behaviour: ``build_container()`` ran Alembic migrations eagerly, so
``anigamerplus --help`` leaked ``INFO  [alembic.runtime.migration] …`` lines
to stderr before argparse's usage block printed to stdout. The pre-parse
guard in :func:`app.cli.main` short-circuits on ``-h``/``--help`` so no DB
work happens.
"""

from __future__ import annotations

import subprocess
import sys
from unittest import mock

import pytest

from app import cli


def test_help_short_circuits_without_building_container(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``main(['--help'])`` must not import or call ``build_container``."""
    called: list[bool] = []

    def _explode() -> object:  # pragma: no cover — must NOT be reached
        called.append(True)
        raise AssertionError('build_container should not be called for --help')

    monkeypatch.setattr(cli, 'build_container', _explode)
    monkeypatch.setattr(sys, 'argv', ['anigamerplus', '--help'])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    # argparse exits 0 on --help
    assert excinfo.value.code == 0
    assert called == []

    captured = capsys.readouterr()
    # argparse prints usage to stdout.
    assert 'usage: anigamerplus' in captured.out
    # stderr must NOT contain an Alembic INFO prefix.
    assert 'INFO' not in captured.err
    assert 'alembic' not in captured.err.lower()


def test_help_short_form_also_skips_container(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``-h`` behaves the same as ``--help``."""
    monkeypatch.setattr(cli, 'build_container', mock.Mock(side_effect=AssertionError))
    monkeypatch.setattr(sys, 'argv', ['anigamerplus', '-h'])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert 'usage:' in captured.out


def test_help_subprocess_emits_no_alembic_info_on_stderr() -> None:
    """End-to-end: the installed ``anigamerplus`` console script's help
    output does not pollute stderr with Alembic/logging prefixes.

    This guards against regressions where a top-level import of the app
    package reintroduces eager Alembic configuration (e.g. via a module
    that calls ``build_container()`` at import time).
    """
    result = subprocess.run(
        ['uv', 'run', 'anigamerplus', '--help'],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert 'usage: anigamerplus' in result.stdout
    # No Alembic or Python-logging-style prefix should appear.
    assert 'INFO' not in result.stderr
    assert 'alembic' not in result.stderr.lower()

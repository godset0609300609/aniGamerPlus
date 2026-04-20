"""Tests that ``anigamerplus --help`` does not boot the container.

With Typer, ``--help`` is handled by Click before the callback body runs,
so ``build_container`` is never called.  This replaces the old argparse
short-circuit guard.
"""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from app import cli
from app.cli import app as cli_app

_runner = CliRunner()


def test_help_short_circuits_without_building_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--help`` must not call ``build_container``."""
    called: list[bool] = []

    def _explode() -> object:  # pragma: no cover — must NOT be reached
        called.append(True)
        raise AssertionError('build_container should not be called for --help')

    monkeypatch.setattr(cli, 'build_container', _explode)

    result = _runner.invoke(cli_app, ['--help'])
    assert result.exit_code == 0
    assert called == []
    # Typer prints the app help text to stdout
    assert 'anigamerplus' in result.output.lower()
    # stderr must not contain Alembic INFO lines
    assert 'alembic' not in result.output.lower() or 'INFO' not in result.output


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
    # Typer uses the app name / help text
    assert 'anigamerplus' in result.stdout.lower()
    # No Alembic or Python-logging-style prefix should appear.
    assert 'INFO' not in result.stderr
    assert 'alembic' not in result.stderr.lower()

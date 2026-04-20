"""Tests for :class:`app.cli.Cli` dispatch behaviour.

Does not exercise the full :func:`app.cli.main` bootstrapping (that's
covered by :mod:`test_cli_help`); here we assert the flag-to-runner
plumbing by handing ``Cli`` a hand-rolled container proxy whose fields
are the minimum the dispatcher touches.
"""

from __future__ import annotations

import pathlib
import types
from typing import TYPE_CHECKING, Any

import pytest

from app.cli import Cli

if TYPE_CHECKING:
    from .conftest import FakeContainer, FakeManualRunner


def _cli_with(fake_container: FakeContainer) -> Cli:
    """Build a :class:`Cli` around a container-shaped namespace.

    :class:`Cli._run_manual` only reads ``settings_repo``, ``manual_runner``,
    and ``logger``; a ``SimpleNamespace`` with those attributes is enough.
    Signal installation is suppressed via a minimal ``signals`` stub with
    ``on_exit`` / ``install`` no-ops.
    """

    class _Signals:
        def on_exit(self, _callback: Any) -> None:
            return None

        def install(self) -> None:
            return None

    class _Database:
        def dispose(self) -> None:
            return None

    proxy = types.SimpleNamespace(
        paths=fake_container.paths,
        logger=fake_container.logger,
        settings_repo=fake_container.settings_repo,
        manual_runner=fake_container.manual_runner,
        signals=_Signals(),
        database=_Database(),
    )
    return Cli(proxy)  # type: ignore[arg-type]


def test_cli_current_path_flag_passes_cwd_as_save_dir(
    fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``-c`` / ``--current_path`` must forward ``pathlib.Path.cwd()`` to
    :meth:`ManualRunner.run` as ``save_dir``.

    Legacy behaviour was ``config['bangumi_dir'] = os.getcwd()``; the
    rewrite threads the same value through the ``save_dir`` kwarg the
    downloader orchestrator understands.
    """
    target_cwd = fake_container.paths.working_dir / 'custom-cwd'
    target_cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(target_cwd)

    cli = _cli_with(fake_container)
    rc = cli.run(['--sn', '12345', '--current_path'])
    assert rc == 0

    runner: FakeManualRunner = fake_container.manual_runner
    assert len(runner.run_calls) == 1
    call = runner.run_calls[0]
    assert call['sn'] == 12345
    save_dir = call['save_dir']
    assert isinstance(save_dir, pathlib.Path)
    assert save_dir.resolve() == target_cwd.resolve()


def test_cli_without_current_path_flag_leaves_save_dir_none(
    fake_container: FakeContainer,
) -> None:
    """Omitting ``-c`` must pass ``save_dir=None`` so the orchestrator
    falls back to ``settings.bangumi_dir``."""
    cli = _cli_with(fake_container)
    rc = cli.run(['--sn', '99'])
    assert rc == 0

    runner: FakeManualRunner = fake_container.manual_runner
    assert len(runner.run_calls) == 1
    assert runner.run_calls[0]['save_dir'] is None


def test_cli_current_path_short_form_also_wired(fake_container: FakeContainer, monkeypatch: pytest.MonkeyPatch) -> None:
    """``-c`` short form is equivalent to ``--current_path``."""
    target_cwd = fake_container.paths.working_dir / 'short-form-cwd'
    target_cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(target_cwd)

    cli = _cli_with(fake_container)
    rc = cli.run(['-s', '1', '-c'])
    assert rc == 0

    runner: FakeManualRunner = fake_container.manual_runner
    save_dir = runner.run_calls[0]['save_dir']
    assert isinstance(save_dir, pathlib.Path)
    assert save_dir.resolve() == target_cwd.resolve()

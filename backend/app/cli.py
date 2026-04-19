"""Command-line entry point.

Replaces the ``if __name__ == '__main__':`` block at the bottom of the
legacy ``aniGamerPlus.py`` with a Typer-based CLI.

The flags mirror legacy 1:1 so existing user scripts and Dockerfile
invocations keep working after the binary rename.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import typing as T

import click
import typer

from .core import Container, build_container

app = typer.Typer(
    name='anigamerplus',
    help='aniGamerPlus — 動畫瘋下載器 (refactored CLI)',
    no_args_is_help=False,
    add_completion=False,
)

_RESOLUTION_CHOICES = click.Choice(['360', '480', '540', '576', '720', '1080'])
_DOWNLOAD_MODE_CHOICES = click.Choice(
    [
        'single',
        'latest',
        'largest-sn',
        'multi',
        'all',
        'range',
        'list',
        'sn-list',
        'sn-range',
        'db',
    ]
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    sn: T.Annotated[
        int | None,
        typer.Option('--sn', '-s', help='影片sn碼(數字)'),
    ] = None,
    resolution: T.Annotated[
        str | None,
        typer.Option('--resolution', '-r', help='指定下載清晰度(數字)', click_type=_RESOLUTION_CHOICES),
    ] = None,
    download_mode: T.Annotated[
        str,
        typer.Option('--download_mode', '-m', help='下載模式', click_type=_DOWNLOAD_MODE_CHOICES),
    ] = 'single',
    thread_limit: T.Annotated[
        int | None,
        typer.Option('--thread_limit', '-t', help='最高并發下載數(數字)'),
    ] = None,
    current_path: T.Annotated[
        bool,
        typer.Option('--current_path/--no-current_path', '-c/-C', help='下載到當前工作目錄'),
    ] = False,
    episodes: T.Annotated[
        str | None,
        typer.Option('--episodes', '-e', help='僅下載指定劇集'),
    ] = None,
    no_classify: T.Annotated[
        bool,
        typer.Option('--no_classify/--no-no_classify', '-n/-N', help='不建立番劇資料夾'),
    ] = False,
    user_command: T.Annotated[
        bool,
        typer.Option('--user_command/--no-user_command', '-u/-U', help='所有下載完成后執行用戶命令'),
    ] = False,
    information_only: T.Annotated[
        bool,
        typer.Option('--information_only/--no-information_only', '-i/-I', help='僅查詢資訊，可搭配 -d 更新彈幕'),
    ] = False,
    danmu: T.Annotated[
        bool,
        typer.Option('--danmu/--no-danmu', '-d/-D', help='以 .ass 下載彈幕'),
    ] = False,
    my_anime: T.Annotated[
        bool,
        typer.Option('--my_anime/--no-my_anime', help='匯出「我的動畫」至my_anime.txt'),
    ] = False,
) -> None:
    """aniGamerPlus — 動畫瘋下載器."""
    # Build container here (deferred from module load) so ``--help`` does not
    # trigger Alembic migrations.
    container = build_container()
    cli = Cli(container)

    if my_anime:
        sys.exit(cli._run_export_my_anime())

    args = argparse.Namespace(
        sn=sn,
        resolution=int(resolution) if resolution is not None else None,
        download_mode=download_mode,
        thread_limit=thread_limit,
        current_path=current_path,
        episodes=episodes,
        no_classify=no_classify,
        user_command=user_command,
        information_only=information_only,
        danmu=danmu,
    )

    # Detect "no meaningful args" → auto mode.
    _manual_flags_set = any(
        [
            sn is not None,
            resolution is not None,
            download_mode != 'single',
            thread_limit is not None,
            current_path,
            episodes is not None,
            no_classify,
            user_command,
            information_only,
            danmu,
        ]
    )

    if not _manual_flags_set:
        cli._install_signals()
        sys.exit(cli._run_auto())

    cli._install_signals()
    sys.exit(cli._run_manual(args))


class Cli:
    """Small command dispatcher. Kept for backwards compatibility with tests."""

    def __init__(self, container: Container) -> None:
        self._container = container

    # ------------------------------------------------------------------ modes

    def _run_auto(self) -> int:
        """Enter the periodic update loop. Blocks until SIGINT/SIGTERM."""
        self._container.logger.info(
            None,
            'CLI Mode',
            'CLI mode is not production-ready; use docker-compose for multi-process',
            display=True,
        )
        loop = self._container.build_update_loop()
        loop.run_forever()
        return 0

    def _run_export_my_anime(self) -> int:
        """Write ``my_anime.txt`` to the workspace and exit."""
        output = self._container.paths.working_dir / 'my_anime.txt'
        written = self._container.my_anime_exporter.export(output)
        self._container.logger.info(
            None,
            '匯出我的動畫',
            f'wrote {written} entries to {output}',
            display=True,
        )
        return 0

    def _run_manual(self, args: object) -> int:
        """Manual / information-only download via :class:`ManualRunner`."""
        ns: argparse.Namespace = args  # type: ignore[assignment]

        if ns.sn is None and ns.download_mode not in (
            'list',
            'multi',
            'sn-list',
            'db',
        ):
            self._container.logger.error(
                None,
                '參數錯誤',
                '非 list/multi 模式需要提供 sn',
                display=True,
            )
            return 1

        settings = self._container.settings_repo.load()
        resolution = str(ns.resolution) if ns.resolution else str(settings.download_resolution)

        thread_limit = int(ns.thread_limit or settings.multi_thread)

        ep_range: list[str] = []
        if ns.episodes:
            ep_range = [piece for piece in ns.episodes.split(',') if piece]

        classify = not ns.no_classify

        save_dir: pathlib.Path | None = None
        if ns.current_path:
            save_dir = pathlib.Path.cwd()

        self._container.manual_runner.run(
            sn=ns.sn,
            resolution=resolution,
            mode=ns.download_mode,
            thread_limit=thread_limit,
            ep_range=ep_range,
            save_dir=save_dir,
            classify=classify,
            get_info=ns.information_only,
            user_cmd=ns.user_command,
            cui_danmu=ns.danmu,
        )
        return 0

    # ------------------------------------------------------------------ helpers

    def _install_signals(self) -> None:
        """Install signal handlers + register cleanup callbacks."""
        signals = self._container.signals
        db = self._container.database

        signals.on_exit(db.dispose)
        signals.install()

    # ------------------------------------------------------------------ legacy run() for tests

    def run(self, argv: T.Sequence[str] | None = None) -> int:
        """Parse ``argv`` (or ``sys.argv[1:]``) and dispatch.

        Kept for backwards compatibility with existing tests.
        """
        self._install_signals()

        argv_list = list(argv) if argv is not None else sys.argv[1:]

        if not argv_list:
            return self._run_auto()

        parser = _build_legacy_parser()
        args = parser.parse_args(argv_list)

        if args.my_anime:
            return self._run_export_my_anime()

        return self._run_manual(args)


def _build_legacy_parser() -> argparse.ArgumentParser:
    """Build an argparse parser for the legacy ``Cli.run()`` method used in tests."""
    parser = argparse.ArgumentParser(prog='anigamerplus')
    parser.add_argument('--sn', '-s', type=int)
    parser.add_argument('--resolution', '-r', type=int, choices=[360, 480, 540, 576, 720, 1080])
    parser.add_argument('--download_mode', '-m', type=str, default='single')
    parser.add_argument('--thread_limit', '-t', type=int)
    parser.add_argument('--current_path', '-c', action='store_true')
    parser.add_argument('--episodes', '-e', type=str)
    parser.add_argument('--no_classify', '-n', action='store_true')
    parser.add_argument('--user_command', '-u', action='store_true')
    parser.add_argument('--information_only', '-i', action='store_true')
    parser.add_argument('--danmu', '-d', action='store_true')
    parser.add_argument('--my_anime', action='store_true')
    return parser

"""Command-line entry point.

Replaces the ``if __name__ == '__main__':`` block at the bottom of the
legacy ``aniGamerPlus.py`` with a small :class:`Cli` class built on top
of the :class:`~app.core.Container`.

The argparse flags mirror legacy 1:1 so existing user scripts and
Dockerfile invocations keep working after the binary rename.
"""

from __future__ import annotations

import argparse
import collections.abc
import pathlib
import sys

from .core import Container, build_container


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser. Flags mirror legacy ``aniGamerPlus.py``."""
    parser = argparse.ArgumentParser(
        prog='anigamerplus',
        description='aniGamerPlus — 動畫瘋下載器 (refactored CLI)',
    )
    parser.add_argument('--sn', '-s', type=int, help='視頻sn碼(數字)')
    parser.add_argument(
        '--resolution',
        '-r',
        type=int,
        choices=[360, 480, 540, 576, 720, 1080],
        help='指定下載清晰度(數字)',
    )
    parser.add_argument(
        '--download_mode',
        '-m',
        type=str,
        default='single',
        choices=[
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
        ],
        help='下載模式',
    )
    parser.add_argument('--thread_limit', '-t', type=int, help='最高并發下載數(數字)')
    parser.add_argument(
        '--current_path',
        '-c',
        action='store_true',
        help='下載到當前工作目錄',
    )
    parser.add_argument('--episodes', '-e', type=str, help='僅下載指定劇集')
    parser.add_argument(
        '--no_classify',
        '-n',
        action='store_true',
        help='不建立番劇資料夾',
    )
    parser.add_argument(
        '--user_command',
        '-u',
        action='store_true',
        help='所有下載完成后執行用戶命令',
    )
    parser.add_argument(
        '--information_only',
        '-i',
        action='store_true',
        help='僅查詢資訊，可搭配 -d 更新彈幕',
    )
    parser.add_argument('--danmu', '-d', action='store_true', help='以 .ass 下載彈幕')
    parser.add_argument(
        '--my_anime',
        action='store_true',
        help='匯出「我的動畫」至my_anime.txt',
    )
    return parser


class Cli:
    """Small command dispatcher driven by :meth:`build_parser`."""

    def __init__(self, container: Container) -> None:
        self._container = container

    # ------------------------------------------------------------------ entry

    def run(self, argv: collections.abc.Sequence[str] | None = None) -> int:
        """Parse ``argv`` (or ``sys.argv[1:]``) and dispatch.

        - No args → auto mode (``UpdateLoop.run_forever``).
        - ``--my_anime`` → export and exit.
        - ``-s <sn>`` → manual mode via :meth:`ManualRunner.run`.
        - ``-i`` → info-only variant of manual mode.
        """
        self._install_signals()

        parser = build_parser()
        argv_list = list(argv) if argv is not None else sys.argv[1:]

        if not argv_list:
            return self._run_auto()

        args = parser.parse_args(argv_list)

        if args.my_anime:
            return self._run_export_my_anime()

        return self._run_manual(args)

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

    def _run_manual(self, args: argparse.Namespace) -> int:
        """Manual / information-only download via :class:`ManualRunner`."""
        if args.sn is None and args.download_mode not in (
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
        resolution = str(args.resolution) if args.resolution else str(settings.download_resolution)

        thread_limit = int(args.thread_limit or settings.multi_thread)

        ep_range: list[str] = []
        if args.episodes:
            ep_range = [piece for piece in args.episodes.split(',') if piece]

        classify = not args.no_classify

        save_dir: pathlib.Path | None = None
        if args.current_path:
            # ``-c / --current_path``: download into the process's cwd rather
            # than ``settings.bangumi_dir``. Mirrors legacy behaviour where
            # ``-c`` set ``config['bangumi_dir'] = os.getcwd()``.
            save_dir = pathlib.Path.cwd()

        self._container.manual_runner.run(
            sn=args.sn,
            resolution=resolution,
            mode=args.download_mode,
            thread_limit=thread_limit,
            ep_range=ep_range,
            save_dir=save_dir,
            classify=classify,
            get_info=args.information_only,
            user_cmd=args.user_command,
            cui_danmu=args.danmu,
        )
        return 0

    # ------------------------------------------------------------------ helpers

    def _install_signals(self) -> None:
        """Install signal handlers + register cleanup callbacks."""
        signals = self._container.signals
        db = self._container.database

        # Cookie file is refreshed in-place during operation; there's no
        # in-memory buffer to flush, but disposing the DB engine is a
        # required teardown.
        signals.on_exit(db.dispose)
        signals.install()


def main() -> None:
    argv = sys.argv[1:]
    # Short-circuit help before constructing the container. ``build_container``
    # runs Alembic migrations and opens the DB; neither should happen for a
    # bare ``--help`` invocation (it also adds an unwanted Alembic INFO line
    # to stderr before argparse prints to stdout).
    if any(flag in argv for flag in ('--help', '-h')):
        build_parser().parse_args(argv)  # prints help and sys-exits
        return
    container = build_container()
    sys.exit(Cli(container).run(argv))

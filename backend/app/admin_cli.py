"""Admin CLI for managing Discord-OAuth users.

Entry point: ``anigamerplus-admin`` (see ``pyproject.toml`` scripts).

Commands::

    anigamerplus-admin list
    anigamerplus-admin promote <discord_id>
    anigamerplus-admin demote  <discord_id>

Uses the same :class:`~app.core.Container` as the server so it operates on
the real database in the workspace.  Error cases (user not found, etc.) are
printed to stderr and exit with code 1.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='anigamerplus-admin',
        description='aniGamerPlus admin tool — manage Discord OAuth user roles',
    )
    sub = parser.add_subparsers(dest='command', metavar='command')
    sub.required = True

    # list
    sub.add_parser('list', help='List all registered users')

    # promote
    promote_p = sub.add_parser('promote', help='Promote a user to admin role')
    promote_p.add_argument('discord_id', help='Discord snowflake ID of the user')

    # demote
    demote_p = sub.add_parser('demote', help='Demote a user to downloader role')
    demote_p.add_argument('discord_id', help='Discord snowflake ID of the user')

    return parser


def cmd_list(user_repo: object) -> int:
    """Print every user as a tab-separated table."""
    from .persistence.user_repo import UserRepository

    repo: UserRepository = user_repo  # type: ignore[assignment]
    rows = repo.list_all()
    if not rows:
        print('No users registered yet.')
        return 0
    print(f'{"ID":<22} {"USERNAME":<30} {"ROLE":<12} {"CREATED_AT"}')
    print('-' * 80)
    for r in rows:
        created = r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else ''
        print(f'{r.id:<22} {r.username:<30} {r.role:<12} {created}')
    return 0


def cmd_promote(user_repo: object, discord_id: str) -> int:
    """Promote ``discord_id`` to ``admin``."""
    from .persistence.user_repo import UserRepository

    repo: UserRepository = user_repo  # type: ignore[assignment]
    existing = repo.get(discord_id)
    if existing is None:
        print(
            f"Error: user '{discord_id}' not found in the database.\n"
            'The user must log in at least once before they can be promoted.',
            file=sys.stderr,
        )
        return 1
    repo.set_role(discord_id, 'admin')
    print(f"Promoted user '{discord_id}' ({existing.username}) to admin.")
    return 0


def cmd_demote(user_repo: object, discord_id: str) -> int:
    """Demote ``discord_id`` to ``downloader``."""
    from .persistence.user_repo import UserRepository

    repo: UserRepository = user_repo  # type: ignore[assignment]
    existing = repo.get(discord_id)
    if existing is None:
        print(
            f"Error: user '{discord_id}' not found in the database.",
            file=sys.stderr,
        )
        return 1
    repo.set_role(discord_id, 'downloader')
    print(f"Demoted user '{discord_id}' ({existing.username}) to downloader.")
    return 0


def main() -> None:
    """Entry point for the ``anigamerplus-admin`` script."""
    parser = build_parser()
    args = parser.parse_args()

    from .core import build_container

    container = build_container()
    user_repo = container.user_repo

    if args.command == 'list':
        sys.exit(cmd_list(user_repo))
    elif args.command == 'promote':
        sys.exit(cmd_promote(user_repo, args.discord_id))
    elif args.command == 'demote':
        sys.exit(cmd_demote(user_repo, args.discord_id))
    else:
        parser.print_help()
        sys.exit(1)

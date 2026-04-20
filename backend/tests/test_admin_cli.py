"""Tests for :mod:`app.admin_cli` commands.

We call the internal command functions directly (``cmd_list``, ``cmd_promote``,
``cmd_demote``) rather than going through argparse so the tests stay fast and
don't need a real process spawn.
"""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from app.admin_cli import cmd_demote, cmd_list, cmd_promote
from app.persistence.user_repo import UserRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(
    id: str = '42',  # noqa: A002
    username: str = 'alice',
    role: str = 'downloader',
) -> UserRow:
    return UserRow(
        id=id,
        username=username,
        avatar_url=None,
        role=role,
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        last_login_at=None,
    )


class FakeUserRepo:
    """In-memory UserRepository stand-in for CLI tests."""

    def __init__(self, users: list[UserRow] | None = None) -> None:
        self._store: dict[str, UserRow] = {u.id: u for u in (users or [])}
        self.set_role_calls: list[tuple[str, str]] = []

    def get(self, id: str) -> UserRow | None:  # noqa: A002
        return self._store.get(id)

    def set_role(self, id: str, role: str) -> None:  # noqa: A002
        self.set_role_calls.append((id, role))
        if id in self._store:
            self._store[id] = dataclasses.replace(self._store[id], role=role)

    def list_all(self) -> list[UserRow]:
        return sorted(self._store.values(), key=lambda r: r.created_at)


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------


def test_list_empty_repo(capsys: pytest.CaptureFixture[str]) -> None:
    repo = FakeUserRepo()
    rc = cmd_list(repo)
    assert rc == 0
    out = capsys.readouterr().out
    assert 'No users registered' in out


def test_list_shows_all_users(capsys: pytest.CaptureFixture[str]) -> None:
    users = [
        _make_user(id='10', username='alice', role='admin'),
        _make_user(id='20', username='bob', role='downloader'),
    ]
    repo = FakeUserRepo(users)
    rc = cmd_list(repo)
    assert rc == 0
    out = capsys.readouterr().out
    assert 'alice' in out
    assert 'admin' in out
    assert 'bob' in out
    assert 'downloader' in out


# ---------------------------------------------------------------------------
# cmd_promote
# ---------------------------------------------------------------------------


def test_promote_existing_user_to_admin(capsys: pytest.CaptureFixture[str]) -> None:
    user = _make_user(id='42', role='downloader')
    repo = FakeUserRepo([user])
    rc = cmd_promote(repo, '42')
    assert rc == 0
    assert ('42', 'admin') in repo.set_role_calls
    out = capsys.readouterr().out
    assert 'Promoted' in out


def test_promote_nonexistent_user_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    repo = FakeUserRepo()
    rc = cmd_promote(repo, 'nonexistent')
    assert rc == 1
    err = capsys.readouterr().err
    assert 'not found' in err


# ---------------------------------------------------------------------------
# cmd_demote
# ---------------------------------------------------------------------------


def test_demote_existing_user_to_downloader(capsys: pytest.CaptureFixture[str]) -> None:
    user = _make_user(id='99', role='admin')
    repo = FakeUserRepo([user])
    rc = cmd_demote(repo, '99')
    assert rc == 0
    assert ('99', 'downloader') in repo.set_role_calls
    out = capsys.readouterr().out
    assert 'Demoted' in out


def test_demote_nonexistent_user_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    repo = FakeUserRepo()
    rc = cmd_demote(repo, 'missing')
    assert rc == 1
    err = capsys.readouterr().err
    assert 'not found' in err

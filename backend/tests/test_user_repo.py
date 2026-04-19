"""Tests for :class:`UserRepository`."""

from __future__ import annotations

import pathlib

import pytest

from app.logging_ import Logger
from app.persistence.db import Database
from app.persistence.user_repo import UserRepository


def _make_db(tmp_path: pathlib.Path, name: str = 'test.db') -> Database:
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    db = Database(f'sqlite:///{(tmp_path / name).as_posix()}', logger)
    db.run_baseline_migrations()
    return db


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


def test_upsert_inserts_new_user(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        row = repo.upsert(id='123', username='Alice', avatar_url=None)
        assert row.id == '123'
        assert row.username == 'Alice'
        assert row.role == 'downloader'  # default on insert
        assert row.avatar_url is None
    finally:
        db.dispose()


def test_upsert_with_explicit_role_on_insert(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        row = repo.upsert(id='777', username='Admin', avatar_url=None, role='admin')
        assert row.role == 'admin'
    finally:
        db.dispose()


def test_upsert_updates_existing_user(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='123', username='Alice', avatar_url=None)
        updated = repo.upsert(id='123', username='Alice Updated', avatar_url='http://img')
        assert updated.username == 'Alice Updated'
        assert updated.avatar_url == 'http://img'
    finally:
        db.dispose()


def test_upsert_without_role_on_existing_user_preserves_role(
    tmp_path: pathlib.Path,
) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        # Insert with admin role.
        repo.upsert(id='100', username='Bob', avatar_url=None, role='admin')
        # Update without specifying role — should stay admin.
        updated = repo.upsert(id='100', username='Bob V2', avatar_url=None)
        assert updated.role == 'admin'
    finally:
        db.dispose()


def test_upsert_with_role_on_existing_user_overrides_role(
    tmp_path: pathlib.Path,
) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='200', username='Carol', avatar_url=None, role='downloader')
        updated = repo.upsert(id='200', username='Carol', avatar_url=None, role='admin')
        assert updated.role == 'admin'
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_returns_none_for_missing_id(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        assert repo.get('nonexistent') is None
    finally:
        db.dispose()


def test_get_returns_row_after_upsert(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='42', username='Dave', avatar_url='http://avatar')
        row = repo.get('42')
        assert row is not None
        assert row.username == 'Dave'
        assert row.avatar_url == 'http://avatar'
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# set_role
# ---------------------------------------------------------------------------


def test_set_role_changes_role(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='50', username='Eve', avatar_url=None, role='downloader')
        repo.set_role('50', 'admin')
        row = repo.get('50')
        assert row is not None
        assert row.role == 'admin'
    finally:
        db.dispose()


def test_set_role_preserves_username(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='60', username='Frank', avatar_url=None, role='downloader')
        repo.set_role('60', 'admin')
        row = repo.get('60')
        assert row is not None
        assert row.username == 'Frank'
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# count_admins / first_admin
# ---------------------------------------------------------------------------


def test_count_admins_returns_zero_initially(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        assert repo.count_admins() == 0
    finally:
        db.dispose()


def test_count_admins_counts_only_admins(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='a1', username='Admin1', avatar_url=None, role='admin')
        repo.upsert(id='a2', username='Admin2', avatar_url=None, role='admin')
        repo.upsert(id='d1', username='Down1', avatar_url=None, role='downloader')
        assert repo.count_admins() == 2
    finally:
        db.dispose()


def test_first_admin_returns_none_when_empty(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        assert repo.first_admin() is None
    finally:
        db.dispose()


def test_first_admin_returns_earliest_admin(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        # Insert in order; first_admin should return the first inserted admin.
        repo.upsert(id='z', username='ZAdmin', avatar_url=None, role='admin')
        repo.upsert(id='a', username='AAdmin', avatar_url=None, role='admin')
        first = repo.first_admin()
        assert first is not None
        # The first inserted admin is "z" (earlier created_at).
        assert first.id == 'z'
    finally:
        db.dispose()


def test_first_admin_skips_non_admins(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='d1', username='Downloader', avatar_url=None, role='downloader')
        repo.upsert(id='adm', username='Admin', avatar_url=None, role='admin')
        first = repo.first_admin()
        assert first is not None
        assert first.id == 'adm'
    finally:
        db.dispose()


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


def test_list_all_returns_all_users(tmp_path: pathlib.Path) -> None:
    db = _make_db(tmp_path)
    try:
        repo = UserRepository(db)
        repo.upsert(id='u1', username='User1', avatar_url=None)
        repo.upsert(id='u2', username='User2', avatar_url=None)
        rows = repo.list_all()
        assert len(rows) == 2
        ids = {r.id for r in rows}
        assert ids == {'u1', 'u2'}
    finally:
        db.dispose()

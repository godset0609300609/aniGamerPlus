"""Tests for BtFeedRepository."""

from __future__ import annotations

import collections.abc
import pathlib

import pytest

from app.logging_ import Logger
from app.models import BtFeedCreate, BtFeedUpdate
from app.persistence.bt_feed_repo import BtFeedRepository, DuplicateFeedError
from app.persistence.db import Database
from app.persistence.paths import WorkspacePaths


@pytest.fixture
def db(tmp_path: pathlib.Path) -> collections.abc.Iterator[Database]:
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(paths.logs_dir, save_logs=False, quantity_of_logs=7)
    database = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    database.run_baseline_migrations()
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def repo(db: Database) -> BtFeedRepository:
    return BtFeedRepository(db)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_returns_persisted_feed_with_defaults(repo: BtFeedRepository) -> None:
    feed = repo.create(BtFeedCreate(name='dmhy', url='https://dmhy.org/rss'))
    assert feed.id is not None
    assert feed.name == 'dmhy'
    assert feed.url == 'https://dmhy.org/rss'
    assert feed.title_key == 'title'
    assert feed.link_key == 'link'
    assert feed.guid_key is None
    assert feed.author_key is None
    assert feed.enabled is True
    assert feed.created_at
    assert feed.updated_at


def test_create_persists_custom_key_mapping(repo: BtFeedRepository) -> None:
    feed = repo.create(
        BtFeedCreate(
            name='dmhy',
            url='https://dmhy.org/rss',
            title_key='title',
            link_key='enclosure.url',
            guid_key='guid',
            author_key='author',
            enabled=False,
        )
    )
    assert feed.link_key == 'enclosure.url'
    assert feed.guid_key == 'guid'
    assert feed.author_key == 'author'
    assert feed.enabled is False


def test_create_duplicate_url_raises(repo: BtFeedRepository) -> None:
    repo.create(BtFeedCreate(name='a', url='https://dup.example/rss'))
    with pytest.raises(DuplicateFeedError):
        repo.create(BtFeedCreate(name='b', url='https://dup.example/rss'))


def test_create_duplicate_url_does_not_leave_partial_row(repo: BtFeedRepository) -> None:
    repo.create(BtFeedCreate(name='a', url='https://dup2.example/rss'))
    with pytest.raises(DuplicateFeedError):
        repo.create(BtFeedCreate(name='b', url='https://dup2.example/rss'))
    assert len(repo.list_all()) == 1


def test_create_rejects_private_ip_url(repo: BtFeedRepository) -> None:
    with pytest.raises(ValueError, match='SSRF guard'):
        repo.create(BtFeedCreate(name='ssrf', url='http://169.254.169.254/latest/meta-data/'))


def test_create_rejects_container_hostname_url(repo: BtFeedRepository) -> None:
    with pytest.raises(ValueError, match='SSRF guard'):
        repo.create(BtFeedCreate(name='ssrf', url='http://redis:6379/'))


def test_create_rejected_url_does_not_persist_a_row(repo: BtFeedRepository) -> None:
    with pytest.raises(ValueError):
        repo.create(BtFeedCreate(name='ssrf', url='http://127.0.0.1/rss'))
    assert repo.list_all() == []


# ---------------------------------------------------------------------------
# list_all / list_enabled / get
# ---------------------------------------------------------------------------


def test_list_all_returns_every_feed_ordered_by_id(repo: BtFeedRepository) -> None:
    f1 = repo.create(BtFeedCreate(name='a', url='https://a.example/rss'))
    f2 = repo.create(BtFeedCreate(name='b', url='https://b.example/rss'))
    result = repo.list_all()
    assert [f.id for f in result] == [f1.id, f2.id]


def test_list_enabled_excludes_disabled_feeds(repo: BtFeedRepository) -> None:
    repo.create(BtFeedCreate(name='on', url='https://on.example/rss', enabled=True))
    repo.create(BtFeedCreate(name='off', url='https://off.example/rss', enabled=False))
    result = repo.list_enabled()
    assert [f.name for f in result] == ['on']


def test_get_returns_none_for_missing_id(repo: BtFeedRepository) -> None:
    assert repo.get(999) is None


def test_get_returns_the_matching_feed(repo: BtFeedRepository) -> None:
    created = repo.create(BtFeedCreate(name='a', url='https://a.example/rss'))
    fetched = repo.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == 'a'


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_applies_only_provided_fields(repo: BtFeedRepository) -> None:
    created = repo.create(BtFeedCreate(name='a', url='https://a.example/rss', author_key='author'))
    updated = repo.update(created.id, BtFeedUpdate(name='renamed'))
    assert updated is not None
    assert updated.name == 'renamed'
    assert updated.url == 'https://a.example/rss'
    assert updated.author_key == 'author'  # untouched


def test_update_can_explicitly_clear_a_nullable_field(repo: BtFeedRepository) -> None:
    created = repo.create(BtFeedCreate(name='a', url='https://a.example/rss', guid_key='guid'))
    updated = repo.update(created.id, BtFeedUpdate(guid_key=None))
    assert updated is not None
    assert updated.guid_key is None


def test_update_bumps_updated_at(repo: BtFeedRepository) -> None:
    created = repo.create(BtFeedCreate(name='a', url='https://a.example/rss'))
    updated = repo.update(created.id, BtFeedUpdate(name='renamed'))
    assert updated is not None
    assert updated.updated_at >= created.updated_at


def test_update_to_existing_url_raises_duplicate(repo: BtFeedRepository) -> None:
    repo.create(BtFeedCreate(name='a', url='https://a.example/rss'))
    other = repo.create(BtFeedCreate(name='b', url='https://b.example/rss'))
    with pytest.raises(DuplicateFeedError):
        repo.update(other.id, BtFeedUpdate(url='https://a.example/rss'))


def test_update_missing_id_returns_none(repo: BtFeedRepository) -> None:
    assert repo.update(999, BtFeedUpdate(name='x')) is None


def test_update_with_no_fields_set_is_a_noop(repo: BtFeedRepository) -> None:
    created = repo.create(BtFeedCreate(name='a', url='https://a.example/rss'))
    result = repo.update(created.id, BtFeedUpdate())
    assert result is not None
    assert result.updated_at == created.updated_at


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_removes_the_feed(repo: BtFeedRepository) -> None:
    created = repo.create(BtFeedCreate(name='a', url='https://a.example/rss'))
    repo.delete(created.id)
    assert repo.get(created.id) is None


def test_delete_missing_id_is_a_noop(repo: BtFeedRepository) -> None:
    repo.delete(999)  # must not raise

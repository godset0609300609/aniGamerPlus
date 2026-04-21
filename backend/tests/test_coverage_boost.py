"""Coverage-boost tests: targeted branches not hit by existing test suite.

Covers:
- app/integrations/my_anime_export.py  (HTTP error page, non-canonical href,
  resolve_sn failure, 100-page safety limit)
- app/api/deps.py  (_get_settings caching, require_admin_user 403)
- app/main.py  (run() SSL missing path, CORS env var, lifespan with proxy)
- app/downloader/filename.py  (plex extra/movie branches, decimal episodes,
  _season_root_and_sub movie/specials/season1)
- app/services/auth.py  (verify_http missing credentials, bad credentials)
- app/persistence/cookie_repo.py  (invalidate OSError, write, modified_at,
  exists_and_nonempty, BOM, parse_cookie_line no-equals)
- app/scheduler/worker.py  (sn missing from queue, load TryTooManyTimeError,
  download TaskCancelledError, download NoAvailableStreamError,
  small file result, upload failure)
- app/scheduler/manual_runner.py  (list mode, mode='multi' sn already in list,
  unknown mode, _pre_parse error branch, _expand_range reversed)
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import pathlib
from typing import Any
from unittest.mock import patch

import pytest

from app.downloader import exceptions as _exc
from app.downloader.anime import DownloadResult
from app.downloader.filename import FilenameBuilder
from app.downloader.metadata import AnimeMetadata
from app.downloader.progress import ProgressBus
from app.integrations.my_anime_export import MyAnimeExporter
from app.logging_ import Logger
from app.models import AppSettings
from app.persistence.cookie_repo import CookieRepository, _parse_cookie_line
from app.persistence.paths import WorkspacePaths
from app.scheduler.manual_runner import ManualRunner
from app.scheduler.queue_ import TaskInfo, TaskQueue
from app.scheduler.worker import DownloadWorker

# ---------------------------------------------------------------------------
# my_anime_export.py
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeResponse:
    status_code: int
    text: str
    url: str = ''


class _FakeClient:
    def __init__(self, *, pages: list[_FakeResponse] | None = None) -> None:
        self._pages = list(pages or [])
        self.get_calls: list[str] = []
        self.side_effects: dict[str, BaseException] = {}

    def get(
        self,
        url: str,
        *,
        extra_headers: Any = None,
        **_kwargs: Any,
    ) -> _FakeResponse:
        self.get_calls.append(url)
        if url in self.side_effects:
            raise self.side_effects[url]
        if self._pages:
            return self._pages.pop(0)
        return _FakeResponse(status_code=200, text='目前沒有訂閱內容')


def _logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


def _page_html(items: list[tuple[str, str]]) -> str:
    anchors = ''.join(f'<a href="?sn={sn}"><p class="theme-name">{name}</p></a>' for sn, name in items)
    return f'<html><body><div class="theme-list-block">{anchors}</div></body></html>'


def test_my_anime_http_error_page_breaks_loop(tmp_path: pathlib.Path) -> None:
    """When a page returns non-200 the loop breaks and 0 entries are returned."""
    client = _FakeClient(pages=[_FakeResponse(status_code=503, text='')])
    exporter = MyAnimeExporter(client, _logger(tmp_path))  # type: ignore[arg-type]
    out = tmp_path / 'out.txt'
    count = exporter.export(out)
    assert count == 0
    assert out.exists()


def test_my_anime_non_canonical_href_resolved_via_second_get(
    tmp_path: pathlib.Path,
) -> None:
    """Non-canonical ``href`` (no 'sn=' substring) triggers a second GET."""
    # href must not contain 'sn=' at all so _resolve_sn falls into the non-canonical path
    anchors = '<a href="animeRef.php?ref=abc"><p class="theme-name">番劇A</p></a>'
    page_html = f'<html><body><div class="theme-list-block">{anchors}</div></body></html>'

    pages = [
        _FakeResponse(200, page_html),
        # sentinel to stop pagination
        _FakeResponse(200, '目前沒有訂閱內容'),
    ]
    client = _FakeClient(pages=pages)

    # Override get() so the animeRef lookup returns a URL with sn=9999
    original_get = client.get

    def patched_get(url: str, *, extra_headers: Any = None, **kw: Any) -> _FakeResponse:
        if 'animeRef.php' in url:
            return _FakeResponse(200, '', url='https://ani.gamer.com.tw/animeVideo.php?sn=9999')
        return original_get(url, extra_headers=extra_headers, **kw)

    client.get = patched_get  # type: ignore[method-assign]

    exporter = MyAnimeExporter(client, _logger(tmp_path))  # type: ignore[arg-type]
    out = tmp_path / 'out.txt'
    count = exporter.export(out)
    assert count == 1
    assert '9999 all <番劇A>' in out.read_text(encoding='utf-8')


def test_my_anime_resolve_sn_exception_skips_entry(tmp_path: pathlib.Path) -> None:
    """If the sn-resolution GET raises, the anchor is skipped silently."""
    # href must not contain 'sn=' so it falls into the non-canonical branch
    anchors = '<a href="animeRef.php?ref=bad"><p class="theme-name">番劇B</p></a>'
    page_html = f'<html><body><div class="theme-list-block">{anchors}</div></body></html>'
    client = _FakeClient(pages=[_FakeResponse(200, page_html), _FakeResponse(200, '目前沒有訂閱內容')])
    # The second GET (href resolution) raises
    client.side_effects['https://ani.gamer.com.tw/animeRef.php?ref=bad'] = RuntimeError('network fail')

    exporter = MyAnimeExporter(client, _logger(tmp_path))  # type: ignore[arg-type]
    out = tmp_path / 'out.txt'
    count = exporter.export(out)
    # Entry is silently skipped
    assert count == 0


def test_my_anime_empty_theme_list_block(tmp_path: pathlib.Path) -> None:
    """Page with theme-list-block but no anchors returns 0 and breaks pagination."""
    page_html = '<html><body><div class="theme-list-block"></div></body></html>'
    client = _FakeClient(pages=[_FakeResponse(200, page_html)])
    exporter = MyAnimeExporter(client, _logger(tmp_path))  # type: ignore[arg-type]
    out = tmp_path / 'out.txt'
    count = exporter.export(out)
    assert count == 0


def test_my_anime_resolve_sn_final_url_no_sn(tmp_path: pathlib.Path) -> None:
    """If resolved URL has no sn=, the entry is skipped."""
    # href must not contain 'sn=' so it falls into the non-canonical branch
    anchors = '<a href="animeRef.php?ref=1"><p class="theme-name">番劇C</p></a>'
    page_html = f'<html><body><div class="theme-list-block">{anchors}</div></body></html>'
    client = _FakeClient(pages=[_FakeResponse(200, page_html), _FakeResponse(200, '目前沒有訂閱內容')])

    original_get = client.get

    def patched_get(url: str, *, extra_headers: Any = None, **kw: Any) -> _FakeResponse:
        if 'animeRef.php' in url:
            # Final URL has no sn= param
            return _FakeResponse(200, '', url='https://ani.gamer.com.tw/someotherpage.php')
        return original_get(url, extra_headers=extra_headers, **kw)

    client.get = patched_get  # type: ignore[method-assign]
    exporter = MyAnimeExporter(client, _logger(tmp_path))  # type: ignore[arg-type]
    out = tmp_path / 'out.txt'
    count = exporter.export(out)
    assert count == 0


def test_my_anime_name_holder_none_skips_anchor(tmp_path: pathlib.Path) -> None:
    """Anchor without .theme-name child is silently skipped."""
    # Anchor has no theme-name element
    anchors = '<a href="?sn=111"><span>no theme-name here</span></a>'
    page_html = f'<html><body><div class="theme-list-block">{anchors}</div></body></html>'
    client = _FakeClient(pages=[_FakeResponse(200, page_html), _FakeResponse(200, '目前沒有訂閱內容')])
    exporter = MyAnimeExporter(client, _logger(tmp_path))  # type: ignore[arg-type]
    out = tmp_path / 'out.txt'
    count = exporter.export(out)
    assert count == 0


def test_my_anime_sn_digits_empty_returns_none(tmp_path: pathlib.Path) -> None:
    """Canonical href with sn= but no digits after it returns None → skip entry."""
    anchors = '<a href="?sn="><p class="theme-name">番劇D</p></a>'
    page_html = f'<html><body><div class="theme-list-block">{anchors}</div></body></html>'
    client = _FakeClient(pages=[_FakeResponse(200, page_html), _FakeResponse(200, '目前沒有訂閱內容')])
    exporter = MyAnimeExporter(client, _logger(tmp_path))  # type: ignore[arg-type]
    out = tmp_path / 'out.txt'
    count = exporter.export(out)
    assert count == 0


# ---------------------------------------------------------------------------
# app/api/deps.py — _get_settings caching + require_admin_user 403
# ---------------------------------------------------------------------------


def test_deps_get_settings_caches_callable(tmp_path: pathlib.Path) -> None:
    """_get_settings fills _get_settings_cached on first call; subsequent calls reuse it."""
    import json

    from app.api import deps as deps_module
    from app.models import AppSettings
    from app.persistence.paths import WorkspacePaths
    from app.persistence.settings_repo import SettingsRepository

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    (tmp_path / 'bangumi').mkdir()
    (tmp_path / 'temp').mkdir()
    defaults = AppSettings().model_dump(by_alias=True, exclude_none=False)
    paths.config_path.write_text(json.dumps(defaults, ensure_ascii=False), encoding='utf-8')

    repo = SettingsRepository(paths, _logger(tmp_path))

    # Temporarily patch build_container in app.core (which is imported inside _get_settings)
    class _FakeContainer:
        settings_repo = repo

    original_cache = list(deps_module._get_settings_cached)
    deps_module._get_settings_cached.clear()
    try:
        with patch('app.core.build_container', return_value=_FakeContainer()):
            result1 = deps_module._get_settings()
            # Second call should NOT call build_container again (uses cache)
            result2 = deps_module._get_settings()
        assert result1 is not None
        assert result2 is not None
        # Cache now holds exactly one entry
        assert len(deps_module._get_settings_cached) == 1
    finally:
        deps_module._get_settings_cached.clear()
        deps_module._get_settings_cached.extend(original_cache)


@pytest.mark.anyio
async def test_require_admin_user_raises_403_for_downloader() -> None:
    """require_admin_user must raise HTTP 403 when user has role='downloader'."""
    import fastapi

    from app.api.deps import require_admin_user
    from app.persistence.user_repo import UserRow

    downloader = UserRow(
        id='dl-1',
        username='dl_user',
        avatar_url=None,
        role='downloader',
        created_at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC),
        last_login_at=None,
    )
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await require_admin_user(downloader)
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_current_user_opt_returns_base_when_auth_enabled(
    fake_container: Any,
) -> None:
    """When auth.enabled=True, current_user_opt returns the base_user directly."""
    import starlette.requests

    from app.api.deps import current_user_opt

    # Save + patch auth.enabled=True
    settings_with_auth = fake_container.settings_repo.load().model_copy(
        update={'auth': fake_container.settings_repo.load().auth.model_copy(update={'enabled': True})}
    )

    from app.persistence.user_repo import UserRow

    real_user = UserRow(
        id='real-1',
        username='realuser',
        avatar_url=None,
        role='admin',
        created_at=datetime.datetime.now(datetime.UTC),
        last_login_at=None,
    )

    # Fake HTTP connection (unused by the function but required by the signature)
    scope = {'type': 'http', 'method': 'GET', 'path': '/', 'headers': []}
    conn = starlette.requests.HTTPConnection(scope)

    result = await current_user_opt(conn, settings_with_auth, real_user)
    assert result is real_user


# ---------------------------------------------------------------------------
# app/main.py — run() SSL missing certs, CORS env var
# ---------------------------------------------------------------------------


def test_dashboard_run_raises_when_ssl_cert_missing(fake_container: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """run() must raise FileNotFoundError when SSL=True and cert/key are absent."""
    import types as _types

    from app.main import DashboardApp

    # Enable SSL
    settings = fake_container.settings_repo.load()
    new_dashboard = settings.dashboard.model_copy(update={'SSL': True})
    fake_container.settings_repo.save(settings.model_copy(update={'dashboard': new_dashboard}))

    proxy_container = _types.SimpleNamespace(
        paths=fake_container.paths,
        logger=fake_container.logger,
        settings_repo=fake_container.settings_repo,
        sn_list_repo=fake_container.sn_list_repo,
        cookie_repo=fake_container.cookie_repo,
        database=fake_container.database,
        anime_repo=fake_container.anime_repo,
        progress_bus=fake_container.progress_bus,
        manual_runner=fake_container.manual_runner,
        scheduler_proxy=None,
    )

    dashboard = DashboardApp(proxy_container)
    # Cert/key files don't exist under tmp_path → should raise
    with pytest.raises(FileNotFoundError, match='SSL'):
        dashboard.run()


def test_env_cors_origins_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_env_cors_origins() returns origins split by comma from env var."""
    from app.main import DashboardApp

    monkeypatch.setenv('ANIGAMERPLUS_CORS_ORIGINS', 'http://a.example,http://b.example')
    origins = DashboardApp._env_cors_origins()
    assert origins == ['http://a.example', 'http://b.example']


def test_env_cors_origins_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """_env_cors_origins() returns the default list when env var is unset."""
    from app.main import DashboardApp

    monkeypatch.delenv('ANIGAMERPLUS_CORS_ORIGINS', raising=False)
    origins = DashboardApp._env_cors_origins()
    assert origins == list(DashboardApp.DEFAULT_ALLOWED_ORIGINS)


def test_lifespan_with_proxy_starts_subscription(
    fake_container: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a scheduler_proxy is wired the lifespan starts a WS subscription task."""
    import asyncio
    import types as _types

    import fastapi.testclient

    from app.main import DashboardApp

    monkeypatch.setenv('ANIGAMERPLUS_DISABLE_SCHEDULER', '1')

    # A fake proxy that records run_progress_subscription calls
    subscription_started = [False]

    class _FakeProxy:
        async def run_progress_subscription(self) -> None:
            subscription_started[0] = True
            await asyncio.sleep(9999)

        async def close(self) -> None:
            pass

    fake_proxy = _FakeProxy()

    proxy_container = _types.SimpleNamespace(
        paths=fake_container.paths,
        logger=fake_container.logger,
        settings_repo=fake_container.settings_repo,
        sn_list_repo=fake_container.sn_list_repo,
        cookie_repo=fake_container.cookie_repo,
        database=fake_container.database,
        anime_repo=fake_container.anime_repo,
        progress_bus=fake_container.progress_bus,
        manual_runner=fake_container.manual_runner,
        scheduler_proxy=fake_proxy,
    )

    dashboard = DashboardApp(proxy_container)
    app = dashboard.app

    with fastapi.testclient.TestClient(app):
        # subscription was started
        assert subscription_started[0] is True


# ---------------------------------------------------------------------------
# app/downloader/filename.py — additional branches
# ---------------------------------------------------------------------------


def _meta(
    *,
    bangumi_name: str = '番劇',
    bangumi_name_orig: str | None = None,
    episode: str = '01',
    sn: int = 1,
) -> AnimeMetadata:
    return AnimeMetadata(
        sn=sn,
        title=f'{bangumi_name_orig or bangumi_name}[{episode}]',
        bangumi_name=bangumi_name,
        bangumi_name_orig=bangumi_name_orig or bangumi_name,
        episode=episode,
        episode_list={episode: sn},
    )


def test_plex_naming_extra_title_returns_e_prefix() -> None:
    """Plex mode with [特別篇] in orig name → [E{ep}] token."""
    settings = AppSettings(plex_naming=True, zerofill=2, add_bangumi_name_to_video_filename=True)
    fb = FilenameBuilder(settings)
    meta = _meta(bangumi_name='番劇', bangumi_name_orig='番劇 [特別篇]', episode='1')
    name = fb.build(meta, resolution='1080')
    assert '[E1]' in name or '[E01]' in name


def test_plex_naming_movie_episode() -> None:
    """Plex mode with episode == '電影' → [{電影}] token."""
    settings = AppSettings(plex_naming=True, zerofill=2, add_bangumi_name_to_video_filename=True)
    fb = FilenameBuilder(settings)
    meta = _meta(bangumi_name='番劇電影', bangumi_name_orig='番劇電影', episode='電影')
    name = fb.build(meta, resolution='1080')
    assert '[電影]' in name


def test_plex_naming_decimal_episode_zero_padded() -> None:
    """Plex mode with decimal episode like '1.5' and zerofill=3 → '001.5'."""
    settings = AppSettings(plex_naming=True, zerofill=3, add_bangumi_name_to_video_filename=True)
    fb = FilenameBuilder(settings)
    meta = _meta(episode='1.5')
    name = fb.build(meta, resolution='1080')
    # zerofill=3 means int part is padded to 3 chars: '001.5'
    assert '001.5' in name


def test_standard_mode_decimal_episode() -> None:
    """Standard mode with decimal episode '3.5' → S01E03.5."""
    settings = AppSettings(add_bangumi_name_to_video_filename=False, video_filename_extension='mp4')
    fb = FilenameBuilder(settings)
    meta = _meta(episode='3.5')
    name = fb.build(meta, resolution='1080', season=1)
    assert 'S01E03.5' in name


def test_season_root_and_sub_extra_title() -> None:
    """_season_root_and_sub with [特別篇] in orig name → 'Specials'."""
    settings = AppSettings(classify_season=True)
    fb = FilenameBuilder(settings)
    meta = _meta(bangumi_name='番劇', bangumi_name_orig='番劇 [特別篇]', episode='SP1')
    import pathlib as _pathlib

    out = fb.classify_dir(
        meta,
        bangumi_dir=_pathlib.Path('/tmp'),
        bangumi_tag='',
        season=0,
        classify=True,
    )
    assert 'Specials' in str(out)


def test_season_root_and_sub_movie() -> None:
    """_season_root_and_sub with episode == '電影' → 'Movie'."""
    settings = AppSettings(classify_season=True)
    fb = FilenameBuilder(settings)
    meta = _meta(bangumi_name='番劇電影', bangumi_name_orig='番劇電影', episode='電影')
    import pathlib as _pathlib

    out = fb.classify_dir(
        meta,
        bangumi_dir=_pathlib.Path('/tmp'),
        bangumi_tag='',
        season=0,
        classify=True,
    )
    assert 'Movie' in str(out)


def test_season_root_and_sub_season1_fallback() -> None:
    """_season_root_and_sub with no season/extra/movie marker → 'Season 1'."""
    settings = AppSettings(classify_season=True)
    fb = FilenameBuilder(settings)
    meta = _meta(bangumi_name='普通番劇', bangumi_name_orig='普通番劇', episode='01')
    import pathlib as _pathlib

    out = fb.classify_dir(
        meta,
        bangumi_dir=_pathlib.Path('/tmp'),
        bangumi_tag='',
        season=1,
        classify=True,
    )
    assert 'Season 1' in str(out)


# ---------------------------------------------------------------------------
# app/services/auth.py — verify_http error paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_auth_service_verify_http_missing_credentials_raises_401(
    fake_container: Any,
) -> None:
    """verify_http with BasicAuth enabled and credentials=None → 401."""
    import fastapi

    from app.services.auth import AuthService

    current = fake_container.settings_repo.load()
    fake_container.settings_repo.save(
        current.model_copy(
            update={
                'dashboard': current.dashboard.model_copy(update={'BasicAuth': True, 'username': 'u', 'password': 'p'})
            }
        )
    )
    auth = AuthService(fake_container.settings_repo)
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await auth.verify_http(None)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_auth_service_verify_http_bad_credentials_raises_401(
    fake_container: Any,
) -> None:
    """verify_http with wrong password → 401."""
    import fastapi
    import fastapi.security

    from app.services.auth import AuthService

    current = fake_container.settings_repo.load()
    fake_container.settings_repo.save(
        current.model_copy(
            update={
                'dashboard': current.dashboard.model_copy(
                    update={'BasicAuth': True, 'username': 'u', 'password': 'correct'}
                )
            }
        )
    )
    auth = AuthService(fake_container.settings_repo)
    creds = fastapi.security.HTTPBasicCredentials(username='u', password='wrong')
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await auth.verify_http(creds)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_auth_service_verify_ws_no_colon_in_decoded(
    fake_container: Any,
) -> None:
    """verify_ws with valid base64 but no ':' in decoded string → False."""
    import base64

    from app.services.auth import AuthService

    current = fake_container.settings_repo.load()
    fake_container.settings_repo.save(
        current.model_copy(
            update={
                'dashboard': current.dashboard.model_copy(update={'BasicAuth': True, 'username': 'u', 'password': 'p'})
            }
        )
    )
    auth = AuthService(fake_container.settings_repo)
    # No colon in the decoded value
    header = 'Basic ' + base64.b64encode(b'usernameonly').decode()
    assert await auth.verify_ws(header) is False


# ---------------------------------------------------------------------------
# app/persistence/cookie_repo.py — uncovered branches
# ---------------------------------------------------------------------------


def test_cookie_write_stores_verbatim(tmp_path: pathlib.Path) -> None:
    """write() stores the exact string (stripped) to cookie.txt."""

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = CookieRepository(paths, logger)

    repo.write('  BAHARUNE=abc123  ')
    content = paths.cookie_path.read_text(encoding='utf-8')
    assert content == 'BAHARUNE=abc123'


def test_cookie_modified_at_returns_datetime(tmp_path: pathlib.Path) -> None:
    """modified_at() returns a datetime after writing a cookie."""
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = CookieRepository(paths, logger)

    repo.write('a=1')
    mtime = repo.modified_at()
    assert isinstance(mtime, datetime.datetime)


def test_cookie_exists_and_nonempty_false_when_absent(tmp_path: pathlib.Path) -> None:
    """exists_and_nonempty() → False when cookie.txt is absent."""
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = CookieRepository(paths, logger)
    assert repo.exists_and_nonempty() is False


def test_cookie_exists_and_nonempty_true_when_present(tmp_path: pathlib.Path) -> None:
    """exists_and_nonempty() → True when cookie.txt has content."""
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = CookieRepository(paths, logger)
    repo.write('a=1')
    assert repo.exists_and_nonempty() is True


def test_cookie_load_strips_utf8_bom(tmp_path: pathlib.Path) -> None:
    """load() tolerates a UTF-8 BOM at the start of cookie.txt."""
    import codecs

    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = CookieRepository(paths, logger)

    bom_content = codecs.BOM_UTF8 + b'k=v'
    paths.cookie_path.parent.mkdir(parents=True, exist_ok=True)
    paths.cookie_path.write_bytes(bom_content)
    result = repo.load()
    assert result == {'k': 'v'}


def test_parse_cookie_line_no_equals_stores_empty_value() -> None:
    """A key with no '=' separator gets an empty-string value."""
    result = _parse_cookie_line('barekey; a=1')
    assert result['barekey'] == ''
    assert result['a'] == '1'


def test_cookie_invalidate_oserror_raises(tmp_path: pathlib.Path) -> None:
    """invalidate() re-raises OSError from os.replace."""
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = CookieRepository(paths, logger)
    repo.write('a=1')

    with (
        patch('app.persistence.cookie_repo.os.replace', side_effect=OSError('disk full')),
        pytest.raises(OSError, match='disk full'),
    ):
        repo.invalidate()


def test_cookie_invalidate_noop_when_missing(tmp_path: pathlib.Path) -> None:
    """invalidate() is a no-op when cookie.txt doesn't exist."""
    paths = WorkspacePaths.detect(working_dir=tmp_path)
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    repo = CookieRepository(paths, logger)
    # Should not raise
    repo.invalidate()


# ---------------------------------------------------------------------------
# app/scheduler/worker.py — uncovered branches
# ---------------------------------------------------------------------------


class _FakeAnime:
    def __init__(
        self,
        sn: int,
        *,
        download_result: DownloadResult | None = None,
        download_raises: BaseException | None = None,
        load_raises: BaseException | None = None,
    ) -> None:
        self.sn = sn
        self._dr = download_result
        self._dl_raises = download_raises
        self._load_raises = load_raises
        self.calls: list[str] = []

    def load(self) -> None:
        self.calls.append('load')
        if self._load_raises is not None:
            raise self._load_raises

    def get_title(self) -> str:
        return 'T'

    def get_bangumi_name(self) -> str:
        return 'B'

    def get_episode(self) -> str:
        return '01'

    def get_resolution(self) -> int:
        return 1080

    def download(self, **_kw: Any) -> DownloadResult:
        self.calls.append('download')
        if self._dl_raises is not None:
            raise self._dl_raises
        assert self._dr is not None
        return self._dr


@dataclasses.dataclass
class _FakeAnimeRepo:
    _rows: dict = dataclasses.field(default_factory=dict)
    calls: list = dataclasses.field(default_factory=list)

    def read(self, sn: int) -> None:
        return self._rows.get(sn)

    def insert(self, **kw: Any) -> None:
        self.calls.append(('insert', kw))

    def update(self, sn: int, **kw: Any) -> None:
        self.calls.append(('update', {'sn': sn, **kw}))


def _make_worker(
    tmp_path: pathlib.Path,
    fake_anime: _FakeAnime,
    *,
    settings_overrides: dict[str, Any] | None = None,
) -> tuple[DownloadWorker, TaskQueue, ProgressBus, _FakeAnimeRepo]:
    from app.models import AppSettings

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(**(settings_overrides or {'download_resolution': '1080'}))
    queue = TaskQueue(max_download=2, max_upload=1)
    progress = ProgressBus()
    repo = _FakeAnimeRepo()
    worker = DownloadWorker(
        queue=queue,
        anime_factory=lambda sn: fake_anime,  # type: ignore[arg-type]
        anime_repo=repo,  # type: ignore[arg-type]
        progress=progress,
        settings_provider=lambda: settings,
        logger=logger,
    )
    return worker, queue, progress, repo


def test_worker_sn_missing_from_queue(tmp_path: pathlib.Path) -> None:
    """run() with sn not in queue logs and returns without calling download."""
    fa = _FakeAnime(1, download_result=DownloadResult(True, tmp_path / 'a.mp4', 500))
    worker, queue, progress, repo = _make_worker(tmp_path, fa)
    # sn is NOT added to the queue
    worker.run(1)
    assert 'download' not in fa.calls


def test_worker_load_try_too_many_time_error(tmp_path: pathlib.Path) -> None:
    """load() raising TryTooManyTimeError marks retry and returns."""
    fa = _FakeAnime(2, load_raises=_exc.TryTooManyTimeError('too many'))
    worker, queue, progress, repo = _make_worker(tmp_path, fa)
    queue.add(2, TaskInfo(sn=2, tag='', mode='single'))
    queue.mark_processing(2)
    progress.start(2, 'f', status='等待下載')

    worker.run(2)
    # Entry should have mark_retry state ('失敗! 重啓中')
    snap = progress.snapshot()
    assert 2 in snap


def test_worker_download_task_cancelled(tmp_path: pathlib.Path) -> None:
    """download() raising TaskCancelledError pops queue and returns False."""
    fa = _FakeAnime(3, download_raises=_exc.TaskCancelledError('cancelled'))
    worker, queue, progress, repo = _make_worker(tmp_path, fa)
    queue.add(3, TaskInfo(sn=3, tag='', mode='single'))
    queue.mark_processing(3)
    progress.start(3, 'f', status='等待下載')

    worker.run(3)
    # sn should have been popped from queue
    assert not queue.contains(3)


def test_worker_download_no_available_stream(tmp_path: pathlib.Path) -> None:
    """download() raising NoAvailableStreamError marks failure and returns."""
    fa = _FakeAnime(4, download_raises=_exc.NoAvailableStreamError('no stream'))
    worker, queue, progress, repo = _make_worker(tmp_path, fa)
    queue.add(4, TaskInfo(sn=4, tag='', mode='single'))
    queue.mark_processing(4)
    progress.start(4, 'f', status='等待下載')

    worker.run(4)
    snap = progress.snapshot()
    assert 4 in snap
    assert snap[4].status == '失敗'


def test_worker_small_file_result_marks_retry(tmp_path: pathlib.Path) -> None:
    """A DownloadResult with size_mb < 5 marks retry — sn stays in queue."""
    # size_mb=1 → should go through the small-result branch (mark_retry)
    fa = _FakeAnime(
        5,
        download_result=DownloadResult(success=True, file_path=tmp_path / 'tiny.mp4', size_mb=1),
    )
    worker, queue, progress, repo = _make_worker(tmp_path, fa)
    queue.add(5, TaskInfo(sn=5, tag='', mode='single'))
    queue.mark_processing(5)
    progress.start(5, 'f', status='等待下載')

    worker.run(5)
    # mark_retry is the small-file path — sn stays in queue for retry
    assert queue.contains(5)


def test_worker_download_try_too_many_time_during_download(tmp_path: pathlib.Path) -> None:
    """download() raising TryTooManyTimeError keeps sn in queue (mark_retry path)."""
    fa = _FakeAnime(6, download_raises=_exc.TryTooManyTimeError('retries exhausted'))
    worker, queue, progress, repo = _make_worker(tmp_path, fa)
    queue.add(6, TaskInfo(sn=6, tag='', mode='single'))
    queue.mark_processing(6)
    progress.start(6, 'f', status='等待下載')

    worker.run(6)
    # sn should remain in queue for re-try
    assert queue.contains(6)


# ---------------------------------------------------------------------------
# app/scheduler/manual_runner.py — list mode + unknown mode + _expand_range reversed
# ---------------------------------------------------------------------------


class _FakeAnime2:
    def __init__(self, sn: int) -> None:
        self.sn = sn
        self.calls: list[tuple[str, Any]] = []
        self._episode_list: dict[str, int] = {'01': sn}

    def load(self) -> None:
        self.calls.append(('load', {}))

    def get_episode_list(self) -> dict[str, int]:
        return dict(self._episode_list)

    def enable_danmu(self) -> None:
        self.calls.append(('enable_danmu', {}))

    def get_info(self) -> None:
        self.calls.append(('get_info', {}))

    def download(self, **kwargs: Any) -> DownloadResult:
        self.calls.append(('download', kwargs))
        return DownloadResult(True, pathlib.Path(f'/tmp/{self.sn}.mp4'), 500)


class _FakeRepo2:
    def read(self, sn: int) -> None:
        return None


def _make_runner(tmp_path: pathlib.Path, anime_map: dict[int, _FakeAnime2]) -> ManualRunner:
    from app.models import AppSettings

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    settings = AppSettings(download_resolution='1080')
    return ManualRunner(
        anime_factory=lambda sn: anime_map[int(sn)],  # type: ignore[arg-type]
        anime_repo=_FakeRepo2(),  # type: ignore[arg-type]
        settings=settings,
        logger=logger,
    )


def test_manual_runner_list_mode(tmp_path: pathlib.Path) -> None:
    """mode='list' downloads each sn from ep_range."""
    fakes = {100: _FakeAnime2(100), 101: _FakeAnime2(101)}
    runner = _make_runner(tmp_path, fakes)
    runner.run(None, mode='list', ep_range=['100', '101'], thread_limit=1)
    assert any(c[0] == 'download' for c in fakes[100].calls)
    assert any(c[0] == 'download' for c in fakes[101].calls)


def test_manual_runner_unknown_mode_raises(tmp_path: pathlib.Path) -> None:
    """mode='bogus' must raise ValueError."""
    fakes: dict[int, _FakeAnime2] = {}
    runner = _make_runner(tmp_path, fakes)
    with pytest.raises(ValueError, match='unknown mode'):
        runner.run(None, mode='bogus')


def test_manual_runner_expand_range_reversed_range(tmp_path: pathlib.Path) -> None:
    """_expand_range with '4-2' (end < start) swaps and expands to {2,3,4}."""
    from app.scheduler.manual_runner import ManualRunner as MR

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    from app.models import AppSettings

    runner = MR(
        anime_factory=lambda sn: None,  # type: ignore[arg-type]
        anime_repo=_FakeRepo2(),  # type: ignore[arg-type]
        settings=AppSettings(),
        logger=logger,
    )
    result = runner._expand_range(['4-2'])  # type: ignore[attr-defined]
    assert result == {'2', '3', '4'}


def test_manual_runner_expand_range_non_numeric_passthrough(tmp_path: pathlib.Path) -> None:
    """_expand_range with non-numeric range item stores it as-is."""
    from app.scheduler.manual_runner import ManualRunner as MR

    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    from app.models import AppSettings

    runner = MR(
        anime_factory=lambda sn: None,  # type: ignore[arg-type]
        anime_repo=_FakeRepo2(),  # type: ignore[arg-type]
        settings=AppSettings(),
        logger=logger,
    )
    result = runner._expand_range(['abc-def'])  # type: ignore[attr-defined]
    assert 'abc-def' in result


def test_manual_runner_pre_parse_error_branch(tmp_path: pathlib.Path) -> None:
    """_pre_parse logs a warning but does not raise when extractor.fetch raises."""

    class _BrokenExtractor:
        def fetch(self, sn: int) -> Any:
            raise RuntimeError('extractor broken')

    bus = ProgressBus()
    bus.start(999, '《999》', status='等待下載')
    logger = Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)
    from app.models import AppSettings

    runner = ManualRunner(
        anime_factory=lambda sn: _FakeAnime2(int(sn)),  # type: ignore[arg-type]
        anime_repo=_FakeRepo2(),  # type: ignore[arg-type]
        settings=AppSettings(),
        logger=logger,
        progress_bus=bus,
        metadata_extractor=_BrokenExtractor(),  # type: ignore[arg-type]
    )
    # Should not raise
    runner._pre_parse(999)  # type: ignore[attr-defined]


def test_manual_runner_multi_mode_with_sn_not_in_list(tmp_path: pathlib.Path) -> None:
    """mode='multi' with sn not already in ep_range appends it to the list."""
    fakes = {10: _FakeAnime2(10), 20: _FakeAnime2(20)}
    runner = _make_runner(tmp_path, fakes)
    # sn=10 is NOT in ep_range, so it should be appended
    runner.run(10, mode='multi', ep_range=['20'], thread_limit=1)
    assert any(c[0] == 'download' for c in fakes[10].calls)
    assert any(c[0] == 'download' for c in fakes[20].calls)


# ---------------------------------------------------------------------------
# app/api/_scheduler_proxy.py — fetch_health + run_progress_subscription backoff
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_proxy_fetch_health_returns_json() -> None:
    """fetch_health() should return parsed JSON from the scheduler."""
    import httpx

    from app.api._scheduler_proxy import SchedulerProxy

    proxy = SchedulerProxy(base_url='http://127.0.0.1:9999', secret='s')

    async def _mock_get(url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json={'status': 'ok', 'tasks': 0},
            request=httpx.Request('GET', 'http://127.0.0.1:9999/internal/health'),
        )

    with patch.object(proxy._client, 'get', side_effect=_mock_get):
        result = await proxy.fetch_health()

    assert result['status'] == 'ok'


@pytest.mark.anyio
@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_proxy_run_subscription_cancelled_during_backoff(
    anyio_backend: str,
) -> None:
    """CancelledError raised during asyncio.sleep in back-off returns cleanly."""
    import asyncio

    from app.api._scheduler_proxy import SchedulerProxy

    proxy = SchedulerProxy(base_url='http://127.0.0.1:9999', secret='s')

    async def _always_fail() -> None:
        raise ConnectionRefusedError('no server')

    sleep_started = asyncio.Event()
    original_sleep = asyncio.sleep

    async def _fake_sleep(delay: float) -> None:
        sleep_started.set()
        await original_sleep(9999)  # will be cancelled

    with (
        patch.object(proxy, '_subscribe_once', side_effect=_always_fail),
        patch('app.api._scheduler_proxy.asyncio.sleep', side_effect=_fake_sleep),
    ):
        task = asyncio.create_task(proxy.run_progress_subscription())
        await sleep_started.wait()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert not proxy.is_scheduler_up()

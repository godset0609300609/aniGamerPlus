"""Tests for ``M3u8Client``.

The HTTP client is stubbed with a ``_FakeClient`` that maps URL patterns
to canned JSON / text bodies. ``time.sleep`` is monkey-patched away so
the ad-wait logic doesn't slow the suite.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
from collections.abc import Mapping
from typing import Any

import pytest

from app.downloader.m3u8_client import M3u8Client
from app.logging_ import Logger
from app.models import AppSettings
from app.persistence.paths import WorkspacePaths
from app.persistence.settings_repo import SettingsRepository


@dataclasses.dataclass
class _FakeResponse:
    status_code: int = 200
    text: str = ''
    content: bytes = b''
    cookies: dict[str, str] = dataclasses.field(default_factory=dict)
    headers: dict[str, str] = dataclasses.field(default_factory=dict)

    def json(self) -> Any:
        import json

        return json.loads(self.text or 'null')


_PLAYLIST_BODY = b"""#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
chunklist_1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720
chunklist_720.m3u8
"""


class _FakeClient:
    """Route URLs to canned responses; captures call order and counts."""

    def __init__(self, vip: bool = True) -> None:
        self.vip = vip
        self.calls: list[str] = []
        self.counts: dict[str, int] = {}

    def _bump(self, tag: str) -> None:
        self.counts[tag] = self.counts.get(tag, 0) + 1

    def get(
        self,
        url: str,
        *,
        no_cookies: bool = False,
        max_retry: int = 3,
        extra_headers: Mapping[str, str] | None = None,
        use_pyhttpx: bool = False,
    ) -> _FakeResponse:
        self.calls.append(url)
        if 'playlist' in url or re.search(r'\.m3u8', url):
            return _FakeResponse(content=_PLAYLIST_BODY, text=_PLAYLIST_BODY.decode())
        # default empty OK
        return _FakeResponse()

    def get_json(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(url)
        if 'getdeviceid.php' in url:
            self._bump('deviceid')
            return {'deviceid': 'FAKE-DEVICE'}
        if 'token.php' in url:
            return {'vip': self.vip, 'time': 1}
        if 'm3u8.php' in url:
            return {'src': 'https://cdn.example.com/playlist.m3u8'}
        return {}


@pytest.fixture
def paths(tmp_path: pathlib.Path) -> WorkspacePaths:
    return WorkspacePaths.detect(working_dir=tmp_path)


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture
def settings_repo(paths: WorkspacePaths, logger: Logger) -> SettingsRepository:
    return SettingsRepository(paths, logger)


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('app.downloader.m3u8_client.time.sleep', lambda _s: None)


def _make(
    client: _FakeClient,
    logger: Logger,
    settings_repo: SettingsRepository,
    *,
    settings: AppSettings | None = None,
) -> M3u8Client:
    s = settings or AppSettings(ua='Mozilla/5.0', parse_sn_cd=0)
    return M3u8Client(client, s, settings_repo, logger)


def test_device_id_cached_after_first_call(logger: Logger, settings_repo: SettingsRepository) -> None:
    fake = _FakeClient()
    m3u8 = _make(fake, logger, settings_repo)

    m3u8.fetch(100)
    m3u8.fetch(101)
    assert fake.counts['deviceid'] == 1


def test_ad_wait_respected_for_non_vip(
    logger: Logger,
    settings_repo: SettingsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient(vip=False)
    sleep_args: list[float] = []
    monkeypatch.setattr(
        'app.downloader.m3u8_client.time.sleep',
        lambda s: sleep_args.append(s),
    )
    settings = AppSettings(ua='Mozilla/5.0', parse_sn_cd=0, ads_time=25)
    m3u8 = _make(fake, logger, settings_repo, settings=settings)

    m3u8.fetch(200)
    assert 25 in sleep_args


def test_playlist_parse_returns_resolution_map(logger: Logger, settings_repo: SettingsRepository) -> None:
    fake = _FakeClient()
    m3u8 = _make(fake, logger, settings_repo)

    result = m3u8.fetch(300)
    assert set(result.keys()) == {'1080', '720'}
    assert result['1080'].endswith('chunklist_1080.m3u8')
    assert result['1080'].startswith('https://cdn.example.com/')


def test_parse_sn_cd_serialises_concurrent_calls(
    logger: Logger,
    settings_repo: SettingsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_args: list[float] = []
    monkeypatch.setattr(
        'app.downloader.m3u8_client.time.sleep',
        lambda s: sleep_args.append(s),
    )

    # Advance the monotonic clock manually so the cooldown logic can see
    # "almost no time has passed" without real sleeping.
    now = [1000.0]
    monkeypatch.setattr('app.downloader.m3u8_client.time.monotonic', lambda: now[0])

    fake = _FakeClient()
    settings = AppSettings(ua='Mozilla/5.0', parse_sn_cd=5)
    m3u8 = _make(fake, logger, settings_repo, settings=settings)

    m3u8.fetch(400)
    # Second call arrives one second later; should wait the remaining 4s.
    now[0] += 1.0
    m3u8.fetch(400)

    assert any(abs(s - 4.0) < 0.01 for s in sleep_args), sleep_args


def test_error_payload_raises_no_available_stream(logger: Logger, settings_repo: SettingsRepository) -> None:
    fake = _FakeClient()

    # Override token.php to return an error
    def custom_json(url: str, **kw: Any) -> Any:
        fake.calls.append(url)
        if 'getdeviceid.php' in url:
            return {'deviceid': 'X'}
        if 'token.php' in url:
            return {'error': {'code': 1012, 'message': 'geo-blocked'}}
        return {}

    fake.get_json = custom_json  # type: ignore[method-assign]
    m3u8 = _make(fake, logger, settings_repo)

    with pytest.raises(Exception) as excinfo:
        m3u8.fetch(500)
    assert 'geo-blocked' in str(excinfo.value) or '1012' in str(excinfo.value)

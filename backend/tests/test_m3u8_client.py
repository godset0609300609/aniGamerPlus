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

from app.downloader import exceptions
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


_PLAYLIST_URL = 'https://cdn.example.com/playlist.m3u8'

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
        if 'video_src.php' in url:  # web branch, post-Aug-2026
            return {'data': {'srcUseCases': [{'src': {'playlist': _PLAYLIST_URL}}]}}
        if 'm3u8.php' in url:  # mobile branch
            return {'data': {'src': _PLAYLIST_URL}}
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


def test_web_branch_uses_video_src_endpoint(logger: Logger, settings_repo: SettingsRepository) -> None:
    """The retired ``ajax/m3u8.php`` must not be called any more.

    Bahamut answers it with ``404 File not found.`` since Aug 2026; the web
    branch moved to ``video_src.php`` with renamed query params.
    """
    fake = _FakeClient()
    m3u8 = _make(fake, logger, settings_repo)

    m3u8.fetch(51139)

    assert not any('ajax/m3u8.php' in c for c in fake.calls)
    hits = [c for c in fake.calls if 'video_src.php' in c]
    assert len(hits) == 1
    url = hits[0]
    assert url.startswith('https://api.gamer.com.tw/anime/v1/video_src.php')
    assert 'videoSn=51139' in url
    assert 'deviceid=FAKE-DEVICE' in url
    assert 'deviceTypeUseCases=1' in url


def test_web_branch_reads_playlist_from_use_cases(logger: Logger, settings_repo: SettingsRepository) -> None:
    """``data.srcUseCases[0].src.playlist`` replaces the flat ``src``."""
    fake = _FakeClient()
    m3u8 = _make(fake, logger, settings_repo)

    result = m3u8.fetch(300)

    assert set(result.keys()) == {'1080', '720'}
    assert any(_PLAYLIST_URL.rsplit('/', 1)[0] in u for u in result.values())


@pytest.mark.parametrize(
    'payload',
    [
        {},
        {'data': {}},
        {'data': {'srcUseCases': []}},
        {'data': {'srcUseCases': [{}]}},
        {'data': {'srcUseCases': [{'src': {}}]}},
        {'data': {'srcUseCases': ['not-a-dict']}},
        {'data': {'srcUseCases': [{'src': {'playlist': None}}]}},
    ],
)
def test_malformed_use_cases_raise_no_available_stream(
    payload: dict[str, Any],
    logger: Logger,
    settings_repo: SettingsRepository,
) -> None:
    """A shape change must surface as NoAvailableStreamError, not IndexError.

    Upstream indexes straight into ``srcUseCases[0]``; here the miss has to
    land on the same no-stream path as every other unplayable sn.
    """
    fake = _FakeClient()
    fake.get_json = lambda url, **kw: (  # type: ignore[method-assign]
        {'deviceid': 'FAKE-DEVICE'}
        if 'getdeviceid.php' in url
        else {'vip': True, 'time': 1}
        if 'token.php' in url
        else payload
    )
    m3u8 = _make(fake, logger, settings_repo)

    with pytest.raises(exceptions.NoAvailableStreamError):
        m3u8.fetch(300)


def test_playlist_error_payload_surfaces_code(logger: Logger, settings_repo: SettingsRepository) -> None:
    """video_src.php reports 1007 etc. in-band with a 2xx status."""
    fake = _FakeClient()
    fake.get_json = lambda url, **kw: (  # type: ignore[method-assign]
        {'deviceid': 'FAKE-DEVICE'}
        if 'getdeviceid.php' in url
        else {'vip': True, 'time': 1}
        if 'token.php' in url
        else {'error': {'code': 1007, 'message': '裝置驗證異常！'}}
    )
    m3u8 = _make(fake, logger, settings_repo)

    with pytest.raises(exceptions.NoAvailableStreamError) as excinfo:
        m3u8.fetch(300)

    assert '1007' in str(excinfo.value)
    assert '裝置驗證異常' in str(excinfo.value)


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


# ---------------------------------------------------------------------------
# feat(downloader): VIP detection log emission
# ---------------------------------------------------------------------------


def test_vip_cookie_logs_vip_info(
    logger: Logger,
    settings_repo: SettingsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When token.php returns ``vip=True``, the VIP info log must be emitted
    with the expected tag and message substring."""
    fake = _FakeClient(vip=True)

    info_calls: list[dict[str, str]] = []
    orig_info = logger.info

    def _capture(sn: int, tag: str, msg: str, **kwargs: object) -> None:
        info_calls.append({'tag': tag, 'msg': msg})
        orig_info(sn, tag, msg, **kwargs)

    monkeypatch.setattr(logger, 'info', _capture)

    m3u8 = _make(fake, logger, settings_repo)
    m3u8.fetch(600)

    vip_logs = [c for c in info_calls if c['tag'] == 'VIP']
    assert vip_logs, f'Expected VIP info log; got {info_calls}'
    assert any('跳過廣告' in c['msg'] for c in vip_logs), vip_logs


def test_non_vip_cookie_logs_ad_wait_info(
    logger: Logger,
    settings_repo: SettingsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When token.php returns ``vip=False``, the ad-wait info log must be
    emitted with the expected tag and message containing the ad duration."""
    fake = _FakeClient(vip=False)

    info_calls: list[dict[str, str]] = []
    orig_info = logger.info

    def _capture(sn: int, tag: str, msg: str, **kwargs: object) -> None:
        info_calls.append({'tag': tag, 'msg': msg})
        orig_info(sn, tag, msg, **kwargs)

    monkeypatch.setattr(logger, 'info', _capture)

    settings = AppSettings(ua='Mozilla/5.0', parse_sn_cd=0, ads_time=30)
    m3u8 = _make(fake, logger, settings_repo, settings=settings)
    m3u8.fetch(601)

    ad_logs = [c for c in info_calls if c['tag'] == '廣告等待']
    assert ad_logs, f'Expected 廣告等待 info log; got {info_calls}'
    assert any('30' in c['msg'] for c in ad_logs), ad_logs


def test_vip_cookie_no_ad_wait_log(
    logger: Logger,
    settings_repo: SettingsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VIP users must NOT trigger the 廣告等待 log."""
    fake = _FakeClient(vip=True)

    info_calls: list[dict[str, str]] = []
    orig_info = logger.info

    def _capture(sn: int, tag: str, msg: str, **kwargs: object) -> None:
        info_calls.append({'tag': tag, 'msg': msg})
        orig_info(sn, tag, msg, **kwargs)

    monkeypatch.setattr(logger, 'info', _capture)

    m3u8 = _make(fake, logger, settings_repo)
    m3u8.fetch(602)

    ad_logs = [c for c in info_calls if c['tag'] == '廣告等待']
    assert not ad_logs, f'VIP user should not trigger 廣告等待 log; got {ad_logs}'


def test_non_vip_cookie_no_vip_log(
    logger: Logger,
    settings_repo: SettingsRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-VIP users must NOT trigger the VIP log."""
    fake = _FakeClient(vip=False)

    info_calls: list[dict[str, str]] = []
    orig_info = logger.info

    def _capture(sn: int, tag: str, msg: str, **kwargs: object) -> None:
        info_calls.append({'tag': tag, 'msg': msg})
        orig_info(sn, tag, msg, **kwargs)

    monkeypatch.setattr(logger, 'info', _capture)

    m3u8 = _make(fake, logger, settings_repo)
    m3u8.fetch(603)

    vip_logs = [c for c in info_calls if c['tag'] == 'VIP']
    assert not vip_logs, f'Non-VIP user should not trigger VIP log; got {vip_logs}'

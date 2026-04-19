"""Tests for ``AniGamerHttpClient``."""

from __future__ import annotations

import dataclasses
import pathlib
import socket
import threading
import time
from typing import Any
from unittest import mock

import pytest
import requests

from app.downloader import exceptions
from app.downloader.http_client import AniGamerHttpClient
from app.logging_ import Logger
from app.models import AppSettings
from app.persistence.cookie_repo import CookieRepository
from app.persistence.paths import WorkspacePaths


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


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


class _FakeSession:
    def __init__(self, response: _FakeResponse | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response or _FakeResponse()

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({'url': url, **kwargs})
        return self.response


class _RetryingSession:
    """Session that fails ``fail_n`` times before returning a good response."""

    def __init__(self, fail_n: int) -> None:
        self.fail_n = fail_n
        self.attempts = 0

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.attempts += 1
        if self.attempts <= self.fail_n:
            raise requests.ConnectionError('boom')
        return _FakeResponse()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def paths(tmp_path: pathlib.Path) -> WorkspacePaths:
    return WorkspacePaths.detect(working_dir=tmp_path)


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


@pytest.fixture
def cookies(paths: WorkspacePaths, logger: Logger) -> CookieRepository:
    return CookieRepository(paths, logger)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip ``time.sleep`` so retry tests run fast."""
    monkeypatch.setattr('app.downloader.http_client.time.sleep', lambda _s: None)


def _client(
    cookies: CookieRepository,
    logger: Logger,
    *,
    ua: str = 'Mozilla/5.0',
    use_proxy: bool = False,
    proxy: str = '',
) -> AniGamerHttpClient:
    settings = AppSettings(ua=ua, use_proxy=use_proxy, proxy=proxy)
    return AniGamerHttpClient(settings, cookies, logger)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_firefox_ua_selects_firefox_browser_type(
    cookies: CookieRepository, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _Spy:
        def __init__(self, *, browser_type: str) -> None:
            captured['browser_type'] = browser_type

    monkeypatch.setattr('app.downloader.http_client.pyhttpx.HttpSession', _Spy)
    AniGamerHttpClient(
        AppSettings(ua='Mozilla/5.0 (Firefox/110.0) Gecko'),
        cookies,
        logger,
    )
    assert captured['browser_type'] == 'firefox'


def test_non_firefox_ua_selects_chrome_browser_type(
    cookies: CookieRepository, logger: Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _Spy:
        def __init__(self, *, browser_type: str) -> None:
            captured['browser_type'] = browser_type

    monkeypatch.setattr('app.downloader.http_client.pyhttpx.HttpSession', _Spy)
    AniGamerHttpClient(
        AppSettings(ua='Mozilla/5.0 (Chrome/107) AppleWebKit'),
        cookies,
        logger,
    )
    assert captured['browser_type'] == 'chrome'


def test_get_applies_cookies_from_repository(
    cookies: CookieRepository,
    logger: Logger,
    paths: WorkspacePaths,
) -> None:
    paths.cookie_path.write_text('BAHARUNE=abc; BAHAID=42', encoding='utf-8')
    client = _client(cookies, logger)
    fake = _FakeSession()
    client._session = fake  # type: ignore[assignment]

    client.get('https://example.com/')

    assert fake.calls[0]['cookies'] == {'BAHARUNE': 'abc', 'BAHAID': '42'}


def test_get_with_no_cookies_skips_cookies(
    cookies: CookieRepository,
    logger: Logger,
    paths: WorkspacePaths,
) -> None:
    paths.cookie_path.write_text('BAHARUNE=abc', encoding='utf-8')
    client = _client(cookies, logger)
    fake = _FakeSession()
    client._session = fake  # type: ignore[assignment]

    client.get('https://example.com/', no_cookies=True)

    assert fake.calls[0]['cookies'] == {}


def test_set_cookie_deleted_does_not_renew(
    cookies: CookieRepository,
    logger: Logger,
    paths: WorkspacePaths,
) -> None:
    paths.cookie_path.write_text('BAHARUNE=oldvalue', encoding='utf-8')
    client = _client(cookies, logger)
    fake = _FakeSession(
        _FakeResponse(
            headers={'Set-Cookie': 'BAHARUNE=deleted; Path=/'},
            cookies={'BAHARUNE': 'deleted'},
        )
    )
    client._session = fake  # type: ignore[assignment]

    with mock.patch.object(cookies, 'renew') as renew_mock:
        client.get('https://example.com/')
        assert not renew_mock.called

    # cookie.txt not overwritten — still the original value
    assert paths.cookie_path.read_text(encoding='utf-8') == 'BAHARUNE=oldvalue'


def test_set_cookie_new_value_triggers_renew(
    cookies: CookieRepository,
    logger: Logger,
    paths: WorkspacePaths,
) -> None:
    paths.cookie_path.write_text('BAHARUNE=oldvalue', encoding='utf-8')
    client = _client(cookies, logger)
    fake = _FakeSession(
        _FakeResponse(
            headers={'Set-Cookie': 'BAHARUNE=newvalue; Path=/; Expires=...'},
            cookies={'BAHARUNE': 'newvalue', 'BAHAID': '77'},
        )
    )
    client._session = fake  # type: ignore[assignment]

    client.get('https://example.com/')

    stored = cookies.load()
    assert stored['BAHARUNE'] == 'newvalue'
    assert stored['BAHAID'] == '77'


def test_unsupported_proxy_scheme_logs_warning_and_falls_back(cookies: CookieRepository, logger: Logger) -> None:
    with mock.patch.object(logger, 'error') as error_mock:
        client = _client(cookies, logger, use_proxy=True, proxy='quic://proxy.example:443')
        assert client._proxies == {}
    assert error_mock.called
    first_call = error_mock.call_args_list[0]
    # tag is "proxy", message mentions unsupported scheme
    assert first_call.args[1] == 'proxy'


def test_get_retries_up_to_max_retry_on_connection_error(cookies: CookieRepository, logger: Logger) -> None:
    client = _client(cookies, logger)
    retrying = _RetryingSession(fail_n=2)
    client._session = retrying  # type: ignore[assignment]

    response = client.get('https://example.com/', max_retry=3)
    assert response.status_code == 200
    assert retrying.attempts == 3  # 2 failures + 1 success


def test_get_raises_after_max_retry_exhausted(cookies: CookieRepository, logger: Logger) -> None:
    client = _client(cookies, logger)
    retrying = _RetryingSession(fail_n=999)
    client._session = retrying  # type: ignore[assignment]

    with pytest.raises(exceptions.TryTooManyTimeError):
        client.get('https://example.com/', max_retry=2)


def test_apply_proxies_does_not_touch_socket_default_timeout(
    cookies: CookieRepository,
    logger: Logger,
) -> None:
    before = socket.getdefaulttimeout()
    _client(cookies, logger, use_proxy=True, proxy='http://proxy.example:8080')
    assert socket.getdefaulttimeout() == before


class _ScriptedSession:
    """Session that returns a scripted response per call."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({'url': url, **kwargs})
        if not self._responses:
            return _FakeResponse()
        return self._responses.pop(0)


def test_handle_set_cookie_deleted_branch_retries_once(
    cookies: CookieRepository,
    logger: Logger,
    paths: WorkspacePaths,
) -> None:
    """When the first call hits ``BAHARUNE=deleted`` AND the response looks
    stale (status != 200 or empty body), the client must issue ONE homepage
    probe (which returns a fresh ``BAHARUNE=newvalue``) and then retry the
    original request exactly once.
    """
    paths.cookie_path.write_text('BAHARUNE=oldvalue', encoding='utf-8')
    client = _client(cookies, logger)

    first = _FakeResponse(
        status_code=403,
        content=b'',
        headers={'Set-Cookie': 'BAHARUNE=deleted; Path=/'},
        cookies={'BAHARUNE': 'deleted'},
    )
    probe = _FakeResponse(
        status_code=200,
        content=b'<html></html>',
        headers={'Set-Cookie': 'BAHARUNE=newvalue; Path=/'},
        cookies={'BAHARUNE': 'newvalue'},
    )
    retry = _FakeResponse(
        status_code=200,
        content=b'{"ok":true}',
        text='{"ok":true}',
    )
    session = _ScriptedSession([first, probe, retry])
    client._session = session  # type: ignore[assignment]

    response = client.get('https://example.com/api/resource')

    # Three calls: original, homepage probe, retry of original.
    assert len(session.calls) == 3
    assert session.calls[0]['url'] == 'https://example.com/api/resource'
    assert session.calls[1]['url'] == 'https://ani.gamer.com.tw/'
    assert session.calls[2]['url'] == 'https://example.com/api/resource'
    # The returned response is the successful retry, not the stale first hit.
    assert response.status_code == 200
    assert response.content == b'{"ok":true}'
    # Cookie store has the renewed value.
    assert cookies.load()['BAHARUNE'] == 'newvalue'


def test_handle_set_cookie_deleted_branch_does_not_retry_when_response_is_ok(
    cookies: CookieRepository,
    logger: Logger,
    paths: WorkspacePaths,
) -> None:
    """If the response already carried usable data, the deleted-cookie path
    must NOT spin up a redundant probe/retry round-trip.
    """
    paths.cookie_path.write_text('BAHARUNE=oldvalue', encoding='utf-8')
    client = _client(cookies, logger)

    ok = _FakeResponse(
        status_code=200,
        content=b'{"ok":true}',
        text='{"ok":true}',
        headers={'Set-Cookie': 'BAHARUNE=deleted; Path=/'},
        cookies={'BAHARUNE': 'deleted'},
    )
    session = _ScriptedSession([ok])
    client._session = session  # type: ignore[assignment]

    client.get('https://example.com/api/resource')

    # Single call — the response carried a body so no priming is needed.
    assert len(session.calls) == 1


def test_pyhttpx_calls_are_serialized_by_lock(
    cookies: CookieRepository,
    logger: Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parallel ``use_pyhttpx=True`` calls must be serialized by ``_pyhttpx_lock``.

    Strategy: use a threading.Event gate inside a fake pyhttpx session.  Each
    call records a (start, end) timestamp.  Because the lock serializes callers,
    no two intervals should overlap.  We verify this by checking that every pair
    of recorded intervals is disjoint (the later one starts at or after the
    earlier one ends).
    """
    N = 3
    intervals: list[tuple[float, float]] = []
    intervals_lock = threading.Lock()

    # Gate: the first thread to enter the fake holds an Event open until the
    # test lets it proceed.  This forces the other threads to actually queue on
    # _pyhttpx_lock while the first is "inside".
    gate = threading.Event()
    gate.set()  # all calls proceed immediately; we just record timestamps

    # Patch pyhttpx.HttpSession so the real C extension is never touched.
    class _RecordingPyhttpxSession:
        def __init__(self, *, browser_type: str) -> None:
            pass

        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            t_start = time.monotonic()
            # Brief busy-wait using monotonic so the autouse sleep-patch
            # does not interfere.  We spin for ~20 ms to give overlapping
            # threads enough time to be detectable.
            deadline = t_start + 0.02
            while time.monotonic() < deadline:
                pass
            t_end = time.monotonic()
            with intervals_lock:
                intervals.append((t_start, t_end))
            return _FakeResponse(
                status_code=200,
                content=b'{}',
                text='{}',
            )

    monkeypatch.setattr('app.downloader.http_client.pyhttpx.HttpSession', _RecordingPyhttpxSession)

    client = _client(cookies, logger)

    errors: list[BaseException] = []

    def _call() -> None:
        try:
            client.get('https://example.com/', use_pyhttpx=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_call) for _ in range(N)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors, f'threads raised: {errors}'
    assert len(intervals) == N, f'expected {N} intervals, got {len(intervals)}'

    # Sort by start time and check that no two intervals overlap.
    sorted_ivs = sorted(intervals, key=lambda iv: iv[0])
    for i in range(len(sorted_ivs) - 1):
        _, end_i = sorted_ivs[i]
        start_next, _ = sorted_ivs[i + 1]
        assert start_next >= end_i - 1e-6, (
            f'interval {i} [{sorted_ivs[i][0]:.4f}, {end_i:.4f}] overlaps '
            f'interval {i + 1} [{start_next:.4f}, {sorted_ivs[i + 1][1]:.4f}] — '
            'lock did not serialize calls'
        )

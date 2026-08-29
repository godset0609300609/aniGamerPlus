"""Dual-session HTTP client for the动画疯 API.

Replaces the ``Anime.__request`` state machine in legacy code. One
``AniGamerHttpClient`` instance owns a ``requests.Session`` plus a
``pyhttpx.HttpSession`` with a TLS fingerprint picked from the UA string.

The pyhttpx branch is not optional — the legacy code switched to it
specifically to bypass server-side TLS fingerprint checks introduced in
2023 (see ``Anime.__request`` docstring / inline comment on pyhttpx
issue #249). Dropping it breaks cookie refresh.

Cookie refresh is arbitrated through the ``CookieRepository``'s internal
lock, so parallel workers never corrupt ``cookie.txt``.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import threading
import time
import typing as T
import urllib.parse

import pyhttpx
import requests

from . import exceptions

if T.TYPE_CHECKING:
    from ..logging_ import Logger
    from ..models import AppSettings
    from ..persistence.cookie_repo import CookieRepository


_HOST = 'ani.gamer.com.tw'
_ORIGIN = f'https://{_HOST}'

_SUPPORTED_PROXY_SCHEMES = frozenset({'http', 'https', 'socks5', 'socks5h'})


@dataclasses.dataclass(slots=True)
class _PyhttpxResponseShim:
    """Uniform interface over a ``pyhttpx`` response.

    ``pyhttpx`` returns its own response object whose attribute surface
    differs from ``requests.Response``. The shim exposes just enough
    surface area for downstream code so call sites don't have to branch.
    """

    status_code: int
    text: str
    content: bytes
    cookies: dict[str, str]
    headers: dict[str, str]

    def json(self) -> T.Any:
        import json  # stdlib — json is typically fast enough to not cache

        return json.loads(self.text)


class AniGamerHttpClient:
    """One dual-transport HTTP client per downloader instance."""

    def __init__(
        self,
        settings: AppSettings,
        cookies: CookieRepository,
        logger: Logger,
    ) -> None:
        self._settings = settings
        self._cookies = cookies
        self._logger = logger
        self._session = requests.Session()
        browser = 'firefox' if 'firefox' in settings.ua.lower() else 'chrome'
        self._pyhttpx_session = pyhttpx.HttpSession(browser_type=browser)
        self._pyhttpx_browser_type = browser
        self._pyhttpx_lock = threading.Lock()
        self._proxies: dict[str, str] = {}
        self._apply_proxies()

    # ------------------------------------------------------------------ public

    def get(
        self,
        url: str,
        *,
        no_cookies: bool = False,
        max_retry: int = 3,
        extra_headers: collections.abc.Mapping[str, str] | None = None,
        use_pyhttpx: bool = False,
    ) -> requests.Response | _PyhttpxResponseShim:
        """GET ``url`` with automatic cookie + header + retry handling.

        Returns either a ``requests.Response`` or a ``_PyhttpxResponseShim``
        depending on ``use_pyhttpx``. Both surface ``status_code``, ``text``,
        ``content``, ``cookies``, ``headers`` and ``.json()``.
        """
        response = self._get_once(
            url,
            no_cookies=no_cookies,
            max_retry=max_retry,
            extra_headers=extra_headers,
            use_pyhttpx=use_pyhttpx,
        )

        if no_cookies:
            return response

        kind = self._handle_set_cookie(response)
        if kind == 'deleted' and _looks_stale(response):
            # Another worker won the refresh race; our response is likely
            # stale. Prime the session with one homepage probe so the server
            # issues a fresh Set-Cookie for this worker, then retry the
            # original request exactly once.
            self._logger.info(
                None,
                'cookie',
                f'priming session via homepage probe after deleted-cookie race for {url}',
                display=False,
            )
            try:
                probe = self._get_once(
                    f'{_ORIGIN}/',
                    no_cookies=False,
                    max_retry=max_retry,
                    extra_headers=None,
                    use_pyhttpx=use_pyhttpx,
                )
            except exceptions.TryTooManyTimeError:
                return response
            self._handle_set_cookie(probe)
            response = self._get_once(
                url,
                no_cookies=False,
                max_retry=max_retry,
                extra_headers=extra_headers,
                use_pyhttpx=use_pyhttpx,
            )
            self._handle_set_cookie(response)
        return response

    def _get_once(
        self,
        url: str,
        *,
        no_cookies: bool,
        max_retry: int,
        extra_headers: collections.abc.Mapping[str, str] | None,
        use_pyhttpx: bool,
    ) -> requests.Response | _PyhttpxResponseShim:
        """Execute a single GET with the configured retry-on-network policy."""
        headers = self._build_default_headers()
        if extra_headers:
            headers.update(extra_headers)

        cookies: dict[str, str] = {} if no_cookies else dict(self._cookies.load())

        error_count = 0
        while True:
            try:
                if use_pyhttpx:
                    with self._pyhttpx_lock:
                        raw = self._pyhttpx_session.get(
                            url,
                            headers=headers,
                            cookies=cookies,
                            timeout=10,
                            proxies=self._proxies or None,
                        )
                    response: requests.Response | _PyhttpxResponseShim = _wrap_pyhttpx(raw)
                else:
                    response = self._session.get(
                        url,
                        headers=headers,
                        cookies=cookies,
                        timeout=10,
                        proxies=self._proxies or None,
                    )
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.exceptions.RequestException,
            ) as exc:
                if error_count >= max_retry >= 0:
                    raise exceptions.TryTooManyTimeError(f'retries exhausted for {url}: {exc}') from exc
                self._logger.error(
                    None,
                    'http',
                    f'{url} request failed ({exc}); retrying',
                    display=False,
                )
                time.sleep(3)
                error_count += 1
                continue
            else:
                return response

    def get_json(
        self,
        url: str,
        *,
        no_cookies: bool = False,
        max_retry: int = 3,
        extra_headers: collections.abc.Mapping[str, str] | None = None,
        use_pyhttpx: bool = False,
    ) -> T.Any:
        """Convenience wrapper around ``get`` that decodes JSON.

        The upstream ajax endpoints answer with an HTML error page (or an
        empty body) when they rate-limit us or the WAF steps in. Left alone
        that surfaces as a bare ``JSONDecodeError``, which no caller catches
        — it escapes the worker thread and leaves the sn wedged in the
        queue's processing set. Translate it into ``TryTooManyTimeError``,
        the transient category the pipeline already treats as retriable.
        """
        response = self.get(
            url,
            no_cookies=no_cookies,
            max_retry=max_retry,
            extra_headers=extra_headers,
            use_pyhttpx=use_pyhttpx,
        )
        try:
            return response.json()
        except ValueError as exc:  # JSONDecodeError, from either transport
            snippet = ' '.join(response.text[:200].split())
            raise exceptions.TryTooManyTimeError(
                f'{url} returned non-JSON (HTTP {response.status_code}): {snippet!r}'
            ) from exc

    def build_web_headers(self, sn: int) -> dict[str, str]:
        """Header block the web animeVideo.php endpoints expect."""
        return {
            'User-Agent': self._settings.ua,
            'referer': f'{_ORIGIN}/animeVideo.php?sn={sn}',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.6',
            'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8'),
            'Accept-Encoding': 'gzip, deflate',
            'Cache-Control': 'max-age=0',
            'Origin': _ORIGIN,
        }

    def build_mobile_headers(self) -> dict[str, str]:
        """Header block the undocumented ``api.gamer.com.tw`` endpoints want."""
        return {
            'User-Agent': ('Animad/1.16.16 (tw.com.gamer.android.animad; build:328; Android 9) okHttp/4.4.0'),
            'X-Bahamut-App-Android': 'tw.com.gamer.android.animad',
            'X-Bahamut-App-Version': '328',
            'Accept-Encoding': 'gzip',
            'Connection': 'Keep-Alive',
        }

    # ------------------------------------------------------------------ internals

    def _build_default_headers(self) -> dict[str, str]:
        """The base header block used when no explicit builder is picked.

        Mirrors legacy ``__init_header``'s web defaults (sn=0 in the referer,
        which is harmless — the server doesn't validate the value).
        """
        if self._settings.use_mobile_api:
            return self.build_mobile_headers()
        return self.build_web_headers(0)

    def _handle_set_cookie(self, response: requests.Response | _PyhttpxResponseShim) -> str | None:
        """Cookie refresh state machine.

        The server occasionally responds with a ``Set-Cookie`` header to
        rotate the login token. Two cases:

        - ``BAHARUNE=deleted`` — a concurrent worker already consumed the
          one-shot refresh token. Re-read ``cookie.txt`` so this worker
          picks up the fresh value. Returns ``"deleted"`` so the caller can
          arrange a follow-up probe + retry if the original response looks
          stale.
        - Anything else — merge the new cookies into the repo. ``renew``
          is thread-safe; it serialises through the repo's lock. Returns
          ``"renewed"``.

        Returns ``None`` if there was no Set-Cookie header.
        """
        headers = response.headers
        set_cookie = headers.get('Set-Cookie') or headers.get('set-cookie')
        if not set_cookie:
            return None

        new_cookies = _extract_cookie_jar(response)
        if 'BAHARUNE=deleted' in set_cookie:
            # Another worker won the race. Re-read the cookie file; nothing
            # to write.
            self._logger.info(
                None,
                'cookie',
                'received BAHARUNE=deleted; reloading cookie.txt',
                display=False,
            )
            self._cookies.load()
            return 'deleted'

        if new_cookies:
            merged = dict(self._cookies.load())
            merged.update(new_cookies)
            self._cookies.renew(merged)
            self._logger.info(
                None,
                'cookie',
                'cookie refreshed from Set-Cookie',
                display=False,
            )
            return 'renewed'
        return None

    def _apply_proxies(self) -> None:
        """Mirror legacy ``__init_proxy`` for requests-native schemes only.

        Explicitly does NOT call ``socket.setdefaulttimeout`` — legacy did
        that here and the global leaked into FTP uploads. Unsupported
        schemes (e.g. ``quic://``) log a warning and fall back to
        no-proxy, rather than silently corrupting the environment.
        """
        if not self._settings.use_proxy:
            return
        raw = self._settings.proxy.strip()
        if not raw:
            return
        scheme = urllib.parse.urlsplit(raw).scheme.lower()
        if scheme not in _SUPPORTED_PROXY_SCHEMES:
            self._logger.error(
                None,
                'proxy',
                f'unsupported proxy scheme {scheme!r}; ignoring proxy',
                display=False,
            )
            return
        self._proxies = {'http': raw, 'https': raw}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _wrap_pyhttpx(raw: T.Any) -> _PyhttpxResponseShim:
    """Coerce a ``pyhttpx`` response into the uniform shim shape."""
    status = int(getattr(raw, 'status_code', 0))
    content = bytes(getattr(raw, 'content', b'') or b'')
    try:
        text = raw.text if isinstance(raw.text, str) else content.decode('utf-8', errors='replace')
    except AttributeError:
        text = content.decode('utf-8', errors='replace')
    headers = dict(getattr(raw, 'headers', {}) or {})
    cookies_attr = getattr(raw, 'cookies', None)
    if cookies_attr is None:
        cookies: dict[str, str] = {}
    elif isinstance(cookies_attr, collections.abc.Mapping):
        cookies = {str(k): str(v) for k, v in cookies_attr.items()}
    else:
        cookies = {}
    return _PyhttpxResponseShim(
        status_code=status,
        text=text,
        content=content,
        cookies=cookies,
        headers=headers,
    )


def _looks_stale(
    response: requests.Response | _PyhttpxResponseShim,
) -> bool:
    """Heuristic: did the request actually carry useful data?

    A response is "stale" if the status isn't 200 OR the body is empty OR
    the server echoed a common "still using stale cookie" marker back. The
    caller uses this to decide whether to bother with a second attempt
    after the deleted-cookie race.
    """
    status = int(getattr(response, 'status_code', 0) or 0)
    if status != 200:
        return True

    content = getattr(response, 'content', b'') or b''
    if isinstance(content, (bytes, bytearray)) and len(content) == 0:
        return True

    headers = getattr(response, 'headers', {}) or {}
    content_length = headers.get('Content-Length') or headers.get('content-length')
    if content_length is not None:
        try:
            if int(content_length) == 0:
                return True
        except TypeError, ValueError:
            pass

    text = getattr(response, 'text', '')
    return bool(isinstance(text, str) and 'cookie-expired' in text.lower())


def _extract_cookie_jar(
    response: requests.Response | _PyhttpxResponseShim,
) -> dict[str, str]:
    """Normalise response.cookies to a ``dict[str, str]``."""
    jar = getattr(response, 'cookies', None)
    if jar is None:
        return {}
    if isinstance(jar, collections.abc.Mapping):
        return {str(k): str(v) for k, v in jar.items()}
    try:
        return {str(k): str(v) for k, v in jar.items()}
    except AttributeError, TypeError:
        return {}

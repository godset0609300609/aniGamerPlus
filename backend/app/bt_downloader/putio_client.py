"""Thin REST/JSON wrapper around the Put.io v2 API.

Put.io's API is plain REST/JSON, so this hand-writes the handful of
endpoints the pipeline needs on top of the existing ``httpx`` dependency
rather than pulling in the (unmaintained) ``putiopy`` package.
"""

from __future__ import annotations

import collections.abc
import contextlib
import datetime
import email.utils
import pathlib
import typing as T

import httpx

_BASE_URL = 'https://api.put.io/v2'
_TIMEOUT_SECONDS = 30.0
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB
#: Fallback delay when Put.io returns 429 with no (or an unparsable)
#: ``Retry-After`` header — same order of magnitude as a generous manual
#: retry, without guessing wildly.
_DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS = 30


class PutioClientError(Exception):
    """Raised on a network failure or a non-2xx (non-401, non-429) response from Put.io."""


class PutioAuthError(PutioClientError):
    """Raised when Put.io returns 401 — the OAuth token is invalid or expired."""


class PutioNotFoundError(PutioClientError):
    """Raised when Put.io returns 404 — the requested resource no longer exists.

    Distinct from the generic :class:`PutioClientError` so callers (notably
    :class:`~app.bt_downloader.landing_worker.LandingWorker`) can tell "this
    transfer was deleted on Put.io's side" apart from a transient/unexpected
    failure — the former should silently reset local dispatch state for a
    fresh re-dispatch instead of firing a user-facing failure notification.
    """


class PutioRateLimitError(PutioClientError):
    """Raised when Put.io returns 429 — the account has hit its API rate limit.

    MEDIUM-5 (security audit): distinct from the generic
    :class:`PutioClientError` so callers (notably
    :class:`~app.bt_downloader.landing_worker.LandingWorker`) can back off
    for the server-advertised :attr:`retry_after` seconds and retry once,
    instead of treating this like any other failure (which would fire a
    user-facing ``bt_failed`` notification for what is really just
    transient throttling).
    """

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PutioClient:
    """Synchronous Put.io v2 API client, scoped to a single OAuth token.

    ``transport`` is injectable (defaults to a real network transport) so
    tests can drive the real ``httpx`` request/response machinery via
    ``httpx.MockTransport``.
    """

    def __init__(self, oauth_token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=_BASE_URL,
            headers={'Authorization': f'Bearer {oauth_token}'},
            transport=transport,
            timeout=_TIMEOUT_SECONDS,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PutioClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ transfers

    def add_transfer(self, url: str) -> dict[str, object]:
        """``POST /transfers/add`` — start downloading *url* into the user's Put.io account."""
        response = self._request('POST', '/transfers/add', data={'url': url})
        body: dict[str, object] = response.json()
        return T.cast('dict[str, object]', body.get('transfer', body))

    def get_transfer(self, transfer_id: int) -> dict[str, object]:
        """``GET /transfers/{id}`` — current status of a previously started transfer."""
        response = self._request('GET', f'/transfers/{transfer_id}')
        body: dict[str, object] = response.json()
        return T.cast('dict[str, object]', body.get('transfer', body))

    def list_files(self, folder_id: int) -> list[dict[str, object]]:
        """``GET /files/list?parent_id={folder_id}`` — files inside a completed transfer's folder."""
        response = self._request('GET', '/files/list', params={'parent_id': folder_id})
        body: dict[str, object] = response.json()
        files = body.get('files', [])
        return T.cast('list[dict[str, object]]', files)

    def get_file(self, file_id: int) -> dict[str, object]:
        """``GET /files/{id}`` — metadata for one file or folder.

        Used as a fallback when :meth:`list_files` comes back empty for a
        completed transfer: a single-file torrent's ``transfer.file_id``
        points at the file itself rather than a containing folder, so
        ``list_files(parent_id=file_id)`` legitimately returns ``[]`` (you
        can't list the children of a file). This lets the caller fetch the
        file's own metadata (name, id) to download it directly instead.
        """
        response = self._request('GET', f'/files/{file_id}')
        body: dict[str, object] = response.json()
        return T.cast('dict[str, object]', body.get('file', body))

    def download_file(
        self,
        file_id: int,
        dest: pathlib.Path,
        *,
        landing_dir: pathlib.Path | None = None,
        on_progress: collections.abc.Callable[[int, int], None] | None = None,
    ) -> pathlib.Path:
        """Download a completed file to *dest*, streaming in 1 MiB chunks.

        If *dest* already exists, a ``.1``, ``.2``, ... suffix is inserted
        before the extension rather than overwriting. Returns the path
        actually written to (which may differ from *dest* on collision) —
        the caller needs this to record the true on-disk filename.

        When *landing_dir* is given, *dest* must resolve to a path inside
        it — raises :class:`PutioClientError` otherwise. Callers building
        *dest* from an untrusted file name (e.g. Put.io's own ``name``
        field for the transferred file) should always pass this: it's a
        second, independent layer of defense against a crafted name
        escaping the landing directory, on top of whatever sanitisation
        was applied to build *dest* in the first place.

        When *on_progress* is given, it is called after every chunk is
        written to disk as ``on_progress(bytes_written, total_bytes)`` —
        ``bytes_written`` is the cumulative count so far and ``total_bytes``
        comes from the response's ``Content-Length`` header (``0`` if the
        header is missing or unparsable). A raising callback is swallowed
        (:func:`contextlib.suppress`) so a bug in progress reporting can
        never abort an otherwise-successful download.
        """
        if landing_dir is not None:
            resolved_landing_dir = landing_dir.resolve()
            if not dest.resolve().is_relative_to(resolved_landing_dir):
                raise PutioClientError('destination escapes landing_dir')

        response = self._request('GET', f'/files/{file_id}/url')
        download_url = T.cast('str', response.json()['url'])

        resolved = _resolve_collision(dest)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream('GET', download_url) as stream_response:
            self._check_response('GET', download_url, stream_response)
            total_bytes = 0
            content_length = stream_response.headers.get('content-length')
            if content_length is not None:
                with contextlib.suppress(ValueError):
                    total_bytes = int(content_length)
            bytes_written = 0
            with resolved.open('wb') as fh:
                for chunk in stream_response.iter_bytes(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                    fh.write(chunk)
                    bytes_written += len(chunk)
                    if on_progress is not None:
                        with contextlib.suppress(Exception):
                            on_progress(bytes_written, total_bytes)
        return resolved

    def delete_file(self, file_id: int) -> None:
        """``POST /files/delete`` — remove *file_id* from the user's Put.io files.

        Used by :class:`~app.bt_downloader.landing_worker.LandingWorker` to
        free remote storage once a transfer has landed locally. Put.io
        returns 200 even when *file_id* is already gone (idempotent-ish);
        this only raises for a genuine failure (auth rejected, bad request,
        network error) — the caller decides how to react (best-effort,
        never allowed to fail an already-successful landing).
        """
        self._request('POST', '/files/delete', data={'file_ids': str(file_id)})

    # ------------------------------------------------------------------ internals

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:
            raise PutioClientError(f'{method} {url} failed: {exc}') from exc
        self._check_response(method, url, response)
        return response

    def _check_response(self, method: str, url: str, response: httpx.Response) -> None:
        if response.status_code == 401:
            raise PutioAuthError(f'Put.io token rejected (401) for {method} {url}')
        if response.status_code == 404:
            raise PutioNotFoundError(f'Put.io resource not found (404) for {method} {url}')
        if response.status_code == 429:
            retry_after = _parse_retry_after(response.headers.get('retry-after'))
            raise PutioRateLimitError(
                f'Put.io rate limit (429) for {method} {url}, retry_after={retry_after}s', retry_after=retry_after
            )
        if response.status_code >= 400:
            raise PutioClientError(f'Put.io {method} {url} returned {response.status_code}: {response.text}')


def _parse_retry_after(raw: str | None) -> int:
    """Parse a ``Retry-After`` header value — either delta-seconds or an HTTP-date (RFC 9110 10.2.3).

    Falls back to :data:`_DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS` when the
    header is missing, unparsable as either form, or would work out to a
    non-positive delay (e.g. an HTTP-date already in the past).
    """
    if not raw:
        return _DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS
    raw = raw.strip()
    try:
        seconds = int(raw)
    except ValueError:
        pass
    else:
        return seconds if seconds > 0 else _DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except TypeError, ValueError:
        return _DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS
    if parsed is None:
        return _DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS
    now = datetime.datetime.now(parsed.tzinfo or datetime.UTC)
    delta_seconds = int((parsed - now).total_seconds())
    return delta_seconds if delta_seconds > 0 else _DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS


def _resolve_collision(dest: pathlib.Path) -> pathlib.Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = dest.with_name(f'{stem}.{n}{suffix}')
        if not candidate.exists():
            return candidate
        n += 1

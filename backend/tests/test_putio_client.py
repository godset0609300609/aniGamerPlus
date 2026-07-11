"""Tests for PutioClient.

Uses ``httpx.MockTransport`` so the real ``httpx.Client`` request/response
pipeline (headers, base_url joining, streaming) is exercised end to end.
"""

from __future__ import annotations

import collections.abc
import datetime
import email.utils
import json
import pathlib

import httpx
import pytest

from app.bt_downloader.putio_client import (
    PutioAuthError,
    PutioClient,
    PutioClientError,
    PutioNotFoundError,
    PutioRateLimitError,
)

_TOKEN = 'test-oauth-token-123'


def _json_response(status_code: int, body: object) -> httpx.Response:
    return httpx.Response(status_code, content=json.dumps(body).encode('utf-8'))


# ---------------------------------------------------------------------------
# add_transfer
# ---------------------------------------------------------------------------


def test_add_transfer_sends_bearer_token_and_returns_transfer_object() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _json_response(200, {'transfer': {'id': 42, 'status': 'IN_QUEUE', 'file_id': None}, 'status': 'OK'})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    result = client.add_transfer('magnet:?xt=urn:btih:aaa111')

    assert result == {'id': 42, 'status': 'IN_QUEUE', 'file_id': None}
    assert len(captured) == 1
    request = captured[0]
    assert request.method == 'POST'
    assert request.url.path == '/v2/transfers/add'
    assert request.headers['authorization'] == f'Bearer {_TOKEN}'


def test_add_transfer_401_raises_putio_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return _json_response(401, {'error_message': 'invalid token'})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioAuthError):
        client.add_transfer('magnet:?xt=urn:btih:aaa111')


def test_add_transfer_500_raises_putio_client_error_not_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(500, content=b'internal error')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioClientError) as exc_info:
        client.add_transfer('magnet:?xt=urn:btih:aaa111')
    assert not isinstance(exc_info.value, PutioAuthError)


def test_add_transfer_network_error_wraps_as_putio_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.ConnectError('boom')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioClientError) as exc_info:
        client.add_transfer('magnet:?xt=urn:btih:aaa111')
    assert not isinstance(exc_info.value, PutioAuthError)


# ---------------------------------------------------------------------------
# 429 rate limiting (MEDIUM-5)
# ---------------------------------------------------------------------------


def test_429_response_raises_putio_rate_limit_error_with_integer_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(429, headers={'retry-after': '17'}, content=b'{"error_message": "too many requests"}')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioRateLimitError) as exc_info:
        client.add_transfer('magnet:?xt=urn:btih:aaa111')
    assert exc_info.value.retry_after == 17
    assert isinstance(exc_info.value, PutioClientError)


def test_429_response_with_http_date_retry_after_is_parsed() -> None:
    future = email.utils.format_datetime(
        datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=45)
    )

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(429, headers={'retry-after': future}, content=b'{}')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioRateLimitError) as exc_info:
        client.get_transfer(1)
    # Allow a couple of seconds of slack for wall-clock rounding between
    # building `future` above and the client parsing it.
    assert 40 <= exc_info.value.retry_after <= 45


def test_429_response_with_missing_retry_after_uses_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(429, content=b'{}')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioRateLimitError) as exc_info:
        client.get_transfer(1)
    assert exc_info.value.retry_after > 0


def test_429_response_with_unparsable_retry_after_uses_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(429, headers={'retry-after': 'not-a-valid-value'}, content=b'{}')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioRateLimitError) as exc_info:
        client.get_transfer(1)
    assert exc_info.value.retry_after > 0


def test_429_response_is_not_confused_with_generic_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(500, content=b'internal error')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioClientError) as exc_info:
        client.get_transfer(1)
    assert not isinstance(exc_info.value, PutioRateLimitError)


def test_429_on_streamed_download_also_raises_putio_rate_limit_error(tmp_path: pathlib.Path) -> None:
    """The 429 check runs on every response, including the streamed download
    itself (not just the JSON endpoints) — see download_file's use of
    _check_response on the streaming response."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v2/files/55/url':
            return _json_response(200, {'url': 'https://dl.put.io/files/55/actual.mp4?token=xyz'})
        if str(request.url) == 'https://dl.put.io/files/55/actual.mp4?token=xyz':
            return httpx.Response(429, headers={'retry-after': '9'})
        raise AssertionError(f'unexpected request: {request.url}')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioRateLimitError) as exc_info:
        client.download_file(55, tmp_path / 'episode.mp4')
    assert exc_info.value.retry_after == 9


# ---------------------------------------------------------------------------
# get_transfer
# ---------------------------------------------------------------------------


def test_get_transfer_status_transitions() -> None:
    statuses = iter(['IN_QUEUE', 'DOWNLOADING', 'COMPLETED'])

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        status = next(statuses)
        return _json_response(200, {'transfer': {'id': 42, 'status': status, 'file_id': 99}})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))

    assert client.get_transfer(42)['status'] == 'IN_QUEUE'
    assert client.get_transfer(42)['status'] == 'DOWNLOADING'
    assert client.get_transfer(42)['status'] == 'COMPLETED'


def test_get_transfer_requests_the_correct_path() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _json_response(200, {'transfer': {'id': 7, 'status': 'COMPLETED'}})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    client.get_transfer(7)

    assert captured[0].method == 'GET'
    assert captured[0].url.path == '/v2/transfers/7'


def test_404_response_raises_putio_not_found_error() -> None:
    """A deleted/nonexistent transfer returns 404 — must raise the specific
    PutioNotFoundError subclass so LandingWorker can distinguish "gone" from
    a generic failure and reset dispatch state instead of firing bt_failed."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return _json_response(404, {'error_message': 'not found'})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioNotFoundError) as exc_info:
        client.get_transfer(99999)
    assert isinstance(exc_info.value, PutioClientError)


def test_404_response_is_not_confused_with_generic_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return _json_response(500, {'error_message': 'internal error'})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioClientError) as exc_info:
        client.get_transfer(1)
    assert not isinstance(exc_info.value, PutioNotFoundError)


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


def test_list_files_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return _json_response(200, {'files': [], 'status': 'OK'})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    assert client.list_files(123) == []


def test_list_files_non_empty_and_sends_parent_id() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _json_response(200, {'files': [{'id': 1, 'name': 'a.mp4'}, {'id': 2, 'name': 'b.mp4'}]})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    result = client.list_files(123)

    assert result == [{'id': 1, 'name': 'a.mp4'}, {'id': 2, 'name': 'b.mp4'}]
    assert captured[0].url.params['parent_id'] == '123'


# ---------------------------------------------------------------------------
# get_file
# ---------------------------------------------------------------------------


def test_get_file_returns_file_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return _json_response(200, {'file': {'id': 555, 'name': 'episode.mp4', 'file_type': 'VIDEO'}})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    result = client.get_file(555)

    assert result == {'id': 555, 'name': 'episode.mp4', 'file_type': 'VIDEO'}


def test_get_file_requests_the_correct_path() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _json_response(200, {'file': {'id': 555, 'name': 'episode.mp4', 'file_type': 'VIDEO'}})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    client.get_file(555)

    assert captured[0].method == 'GET'
    assert captured[0].url.path == '/v2/files/555'


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------


def _download_handler(content: bytes) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v2/files/55/url':
            return _json_response(200, {'url': 'https://dl.put.io/files/55/actual.mp4?token=xyz'})
        if str(request.url) == 'https://dl.put.io/files/55/actual.mp4?token=xyz':
            return httpx.Response(200, content=content)
        raise AssertionError(f'unexpected request: {request.url}')

    return handler


def test_download_file_writes_content_to_disk(tmp_path: pathlib.Path) -> None:
    content = b'hello world' * 100
    client = PutioClient(_TOKEN, transport=httpx.MockTransport(_download_handler(content)))  # type: ignore[arg-type]

    dest = tmp_path / 'episode.mp4'
    result = client.download_file(55, dest)

    assert result == dest
    assert dest.read_bytes() == content


def test_download_file_collision_appends_numeric_suffix(tmp_path: pathlib.Path) -> None:
    content = b'new content'
    client = PutioClient(_TOKEN, transport=httpx.MockTransport(_download_handler(content)))  # type: ignore[arg-type]

    dest = tmp_path / 'episode.mp4'
    dest.write_bytes(b'existing content, must not be overwritten')

    result = client.download_file(55, dest)

    assert result == tmp_path / 'episode.1.mp4'
    assert result.read_bytes() == content
    assert dest.read_bytes() == b'existing content, must not be overwritten'


def test_download_file_collision_increments_past_multiple_existing(tmp_path: pathlib.Path) -> None:
    content = b'newest content'
    client = PutioClient(_TOKEN, transport=httpx.MockTransport(_download_handler(content)))  # type: ignore[arg-type]

    dest = tmp_path / 'episode.mp4'
    dest.write_bytes(b'v0')
    (tmp_path / 'episode.1.mp4').write_bytes(b'v1')
    (tmp_path / 'episode.2.mp4').write_bytes(b'v2')

    result = client.download_file(55, dest)

    assert result == tmp_path / 'episode.3.mp4'
    assert result.read_bytes() == content


def test_download_file_creates_missing_parent_directory(tmp_path: pathlib.Path) -> None:
    content = b'content'
    client = PutioClient(_TOKEN, transport=httpx.MockTransport(_download_handler(content)))  # type: ignore[arg-type]

    dest = tmp_path / 'nested' / 'dir' / 'episode.mp4'
    result = client.download_file(55, dest)

    assert result == dest
    assert dest.read_bytes() == content


# ---------------------------------------------------------------------------
# download_file — on_progress callback
# ---------------------------------------------------------------------------


def _download_handler_with_content_length(content: bytes) -> object:
    """Like _download_handler but sets Content-Length so on_progress sees a real total."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v2/files/55/url':
            return _json_response(200, {'url': 'https://dl.put.io/files/55/actual.mp4?token=xyz'})
        if str(request.url) == 'https://dl.put.io/files/55/actual.mp4?token=xyz':
            return httpx.Response(200, content=content, headers={'content-length': str(len(content))})
        raise AssertionError(f'unexpected request: {request.url}')

    return handler


def test_download_file_invokes_on_progress_callback_per_chunk(tmp_path: pathlib.Path) -> None:
    """Content spanning 3 chunk-sized pieces must invoke on_progress 3 times
    with cumulative bytes_written and the Content-Length as total_bytes."""
    chunk = b'x' * (1024 * 1024)  # exactly _DOWNLOAD_CHUNK_SIZE (1 MiB)
    content = chunk * 3
    client = PutioClient(
        _TOKEN, transport=httpx.MockTransport(_download_handler_with_content_length(content))  # type: ignore[arg-type]
    )

    calls: list[tuple[int, int]] = []
    dest = tmp_path / 'episode.mp4'
    result = client.download_file(55, dest, on_progress=lambda a, b: calls.append((a, b)))

    assert result == dest
    assert dest.read_bytes() == content
    assert len(calls) == 3
    total = len(content)
    assert calls == [
        (1024 * 1024, total),
        (2 * 1024 * 1024, total),
        (3 * 1024 * 1024, total),
    ]


def test_download_file_on_progress_total_bytes_zero_when_content_length_missing(tmp_path: pathlib.Path) -> None:
    """A response streamed without a Content-Length header (e.g. chunked
    transfer-encoding) must report total_bytes=0 rather than raising."""
    content = b'hello world' * 100

    def _chunked_content() -> collections.abc.Iterator[bytes]:
        yield content

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v2/files/55/url':
            return _json_response(200, {'url': 'https://dl.put.io/files/55/actual.mp4?token=xyz'})
        if str(request.url) == 'https://dl.put.io/files/55/actual.mp4?token=xyz':
            # Passing a generator (rather than raw bytes) makes httpx use
            # chunked transfer-encoding and skip auto-computing Content-Length,
            # mirroring a real streamed-through-proxy response.
            return httpx.Response(200, content=_chunked_content())
        raise AssertionError(f'unexpected request: {request.url}')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))

    calls: list[tuple[int, int]] = []
    dest = tmp_path / 'episode.mp4'
    client.download_file(55, dest, on_progress=lambda a, b: calls.append((a, b)))

    assert len(calls) == 1  # content is smaller than one chunk
    assert calls[0] == (len(content), 0)  # no Content-Length header -> total_bytes=0


def test_on_progress_exception_does_not_abort_download(tmp_path: pathlib.Path) -> None:
    """A raising on_progress callback must not prevent the file from being
    fully written — contextlib.suppress(Exception) wraps every invocation."""
    content = b'hello world' * 100
    client = PutioClient(_TOKEN, transport=httpx.MockTransport(_download_handler(content)))  # type: ignore[arg-type]

    def boom(_bytes_written: int, _total_bytes: int) -> None:
        raise RuntimeError('progress callback exploded')

    dest = tmp_path / 'episode.mp4'
    result = client.download_file(55, dest, on_progress=boom)

    assert result == dest
    assert dest.read_bytes() == content


# ---------------------------------------------------------------------------
# download_file — landing_dir escape guard
# ---------------------------------------------------------------------------


def test_download_file_without_landing_dir_skips_the_escape_check(tmp_path: pathlib.Path) -> None:
    """Backward compatible default: no ``landing_dir`` -> no check performed."""
    content = b'content'
    client = PutioClient(_TOKEN, transport=httpx.MockTransport(_download_handler(content)))  # type: ignore[arg-type]

    dest = tmp_path / 'outside' / 'episode.mp4'
    result = client.download_file(55, dest)

    assert result == dest
    assert dest.read_bytes() == content


def test_download_file_accepts_destination_inside_landing_dir(tmp_path: pathlib.Path) -> None:
    content = b'content'
    client = PutioClient(_TOKEN, transport=httpx.MockTransport(_download_handler(content)))  # type: ignore[arg-type]

    landing_dir = tmp_path / 'landing'
    landing_dir.mkdir()
    dest = landing_dir / 'episode.mp4'

    result = client.download_file(55, dest, landing_dir=landing_dir)

    assert result == dest
    assert dest.read_bytes() == content


def test_download_file_accepts_destination_in_landing_dir_subdirectory(tmp_path: pathlib.Path) -> None:
    content = b'content'
    client = PutioClient(_TOKEN, transport=httpx.MockTransport(_download_handler(content)))  # type: ignore[arg-type]

    landing_dir = tmp_path / 'landing'
    landing_dir.mkdir()
    dest = landing_dir / 'sub' / 'episode.mp4'

    result = client.download_file(55, dest, landing_dir=landing_dir)

    assert result == dest
    assert dest.read_bytes() == content


def test_download_file_rejects_destination_outside_landing_dir(tmp_path: pathlib.Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise AssertionError('must not make a network request once the escape check has failed')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    landing_dir = tmp_path / 'landing'
    landing_dir.mkdir()
    escaping_dest = tmp_path / 'outside' / 'evil.mp4'

    with pytest.raises(PutioClientError, match='escapes landing_dir'):
        client.download_file(55, escaping_dest, landing_dir=landing_dir)
    assert not escaping_dest.exists()


def test_download_file_rejects_bare_dotdot_destination(tmp_path: pathlib.Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise AssertionError('must not make a network request once the escape check has failed')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    landing_dir = tmp_path / 'landing'
    landing_dir.mkdir()
    escaping_dest = landing_dir / '..' / 'evil.mp4'

    with pytest.raises(PutioClientError, match='escapes landing_dir'):
        client.download_file(55, escaping_dest, landing_dir=landing_dir)


def test_download_file_rejects_symlink_escape(tmp_path: pathlib.Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise AssertionError('must not make a network request once the escape check has failed')

    landing_dir = tmp_path / 'landing'
    landing_dir.mkdir()
    outside_dir = tmp_path / 'outside'
    outside_dir.mkdir()

    link = landing_dir / 'escape_link'
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation not permitted in this environment')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    escaping_dest = link / 'evil.mp4'

    with pytest.raises(PutioClientError, match='escapes landing_dir'):
        client.download_file(55, escaping_dest, landing_dir=landing_dir)


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------


def test_delete_file_sends_bearer_token_and_file_id() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _json_response(200, {'status': 'OK'})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    client.delete_file(555)

    assert len(captured) == 1
    request = captured[0]
    assert request.method == 'POST'
    assert request.url.path == '/v2/files/delete'
    assert request.headers['authorization'] == f'Bearer {_TOKEN}'
    body = request.read().decode('utf-8')
    assert 'file_ids=555' in body


def test_delete_file_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return _json_response(200, {'status': 'OK'})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    assert client.delete_file(555) is None


def test_delete_file_401_raises_putio_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return _json_response(401, {'error_message': 'invalid token'})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioAuthError):
        client.delete_file(555)


def test_delete_file_404_raises_putio_not_found_error() -> None:
    """A file that's already gone from Put.io must still surface as the
    specific PutioNotFoundError subclass, matching every other endpoint —
    LandingWorker's auto-delete path treats this as "nothing further to do"
    rather than a hard failure."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return _json_response(404, {'error_message': 'not found'})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioNotFoundError):
        client.delete_file(555)


def test_delete_file_500_raises_generic_putio_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(500, content=b'internal error')

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(PutioClientError) as exc_info:
        client.delete_file(555)
    assert not isinstance(exc_info.value, PutioAuthError)


# ---------------------------------------------------------------------------
# close / context manager
# ---------------------------------------------------------------------------


def test_close_closes_the_underlying_httpx_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return _json_response(200, {})

    client = PutioClient(_TOKEN, transport=httpx.MockTransport(handler))
    client.close()
    assert client._client.is_closed is True


def test_context_manager_closes_on_exit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return _json_response(200, {})

    with PutioClient(_TOKEN, transport=httpx.MockTransport(handler)) as client:
        assert client._client.is_closed is False
    assert client._client.is_closed is True

"""Tests for :class:`MyAnimeExporter`."""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Mapping
from typing import Any

import pytest

from app.integrations.my_anime_export import MyAnimeExporter
from app.logging_ import Logger


@dataclasses.dataclass
class _FakeResponse:
    status_code: int
    text: str
    url: str = ''


class _FakeClient:
    def __init__(
        self,
        *,
        pages: list[_FakeResponse] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._pages = list(pages or [])
        self._raises = raises
        self.get_calls: list[tuple[str, Mapping[str, str] | None]] = []

    def get(
        self,
        url: str,
        *,
        extra_headers: Mapping[str, str] | None = None,
        **_kwargs: Any,
    ) -> _FakeResponse:
        self.get_calls.append((url, extra_headers))
        if self._raises is not None:
            raise self._raises
        if self._pages:
            return self._pages.pop(0)
        # No more pages — fall back to an empty-looking response.
        return _FakeResponse(status_code=200, text='目前沒有訂閱內容')


def _logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


def _page_html(items: list[tuple[str, str]]) -> str:
    """Return a minimal HTML string holding the expected DOM structure."""
    anchors = ''.join(f'<a href="?sn={sn}"><p class="theme-name">{name}</p></a>' for sn, name in items)
    return f'<html><body><div class="theme-list-block">{anchors}</div></body></html>'


def test_exports_entries_from_single_page(tmp_path: pathlib.Path) -> None:
    html = _page_html([('111', '動畫一'), ('222', '動畫二')])
    client = _FakeClient(
        pages=[
            _FakeResponse(200, html),
            _FakeResponse(200, '<html><body>目前沒有訂閱內容</body></html>'),
        ],
    )
    exporter = MyAnimeExporter(client, _logger(tmp_path))  # type: ignore[arg-type]

    output = tmp_path / 'my_anime.txt'
    count = exporter.export(output)

    assert count == 2
    lines = output.read_text(encoding='utf-8').splitlines()
    assert '111 all <動畫一>' in lines
    assert '222 all <動畫二>' in lines


def test_empty_page_writes_empty_file(tmp_path: pathlib.Path) -> None:
    client = _FakeClient(
        pages=[_FakeResponse(200, '<html><body>目前沒有訂閱內容</body></html>')],
    )
    exporter = MyAnimeExporter(client, _logger(tmp_path))  # type: ignore[arg-type]

    output = tmp_path / 'my_anime.txt'
    count = exporter.export(output)

    assert count == 0
    assert output.exists()
    assert output.read_text(encoding='utf-8') == ''


def test_network_error_returns_zero_and_does_not_raise(
    tmp_path: pathlib.Path,
) -> None:
    client = _FakeClient(raises=RuntimeError('network down'))
    exporter = MyAnimeExporter(client, _logger(tmp_path))  # type: ignore[arg-type]

    output = tmp_path / 'my_anime.txt'
    count = exporter.export(output)
    assert count == 0
    assert output.exists()

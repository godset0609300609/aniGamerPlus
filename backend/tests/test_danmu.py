"""Tests for ``DanmuRenderer``.

The template file is created in ``tmp_path`` so tests are self-contained;
the HTTP client is stubbed to return canned JSON payloads.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Mapping
from typing import Any

import pytest

from app.downloader.danmu import DanmuRenderer
from app.logging_ import Logger
from app.persistence.paths import WorkspacePaths

_TEMPLATE_BODY = (
    '[Script Info]\n'
    'Title: bahaDanmu\n'
    'PlayResX: 1920\n'
    'PlayResY: 1080\n\n'
    '[V4+ Styles]\n'
    'Format: Name, Fontname, Fontsize\n'
    'Style: Roll,Microsoft JhengHei,50\n'
    'Style: Top,Microsoft JhengHei,50\n'
    'Style: Bottom,Microsoft JhengHei,50\n\n'
    '[Events]\n'
    'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
)


@dataclasses.dataclass
class _FakeResponse:
    status_code: int = 200
    text: str = ''
    content: bytes = b''
    cookies: dict[str, str] = dataclasses.field(default_factory=dict)
    headers: dict[str, str] = dataclasses.field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.text or 'null')


class _FakeClient:
    def __init__(
        self,
        danmu_payload: list[dict[str, Any]] | None = None,
        *,
        danmu_status: int = 200,
        fail: bool = False,
    ) -> None:
        self.danmu_payload = danmu_payload or []
        self.danmu_status = danmu_status
        self.fail = fail

    def get(
        self,
        url: str,
        *,
        no_cookies: bool = False,
        max_retry: int = 3,
        extra_headers: Mapping[str, str] | None = None,
        use_pyhttpx: bool = False,
    ) -> _FakeResponse:
        if self.fail:
            raise RuntimeError('network boom')
        if 'danmuGet' in url:
            return _FakeResponse(
                status_code=self.danmu_status,
                text=json.dumps(self.danmu_payload),
            )
        if 'keywordGet' in url:
            return _FakeResponse(status_code=200, text='[]')
        return _FakeResponse(status_code=200, text='[]')


@pytest.fixture
def paths(tmp_path: pathlib.Path) -> WorkspacePaths:
    # The template itself now ships inside ``app.downloader.assets`` (loaded
    # via importlib.resources), so tests don't need to seed it on disk.
    return WorkspacePaths.detect(working_dir=tmp_path)


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


def test_render_writes_ass_next_to_video_file(tmp_path: pathlib.Path, paths: WorkspacePaths, logger: Logger) -> None:
    payload = [
        {'text': 'hello', 'time': 100, 'color': '#FFFFFF', 'position': 0},
        {'text': 'world', 'time': 200, 'color': '#FF0000', 'position': 1},
    ]
    client = _FakeClient(payload)
    renderer = DanmuRenderer(client, logger)

    video_file = tmp_path / 'out' / 'episode.mp4'
    video_file.parent.mkdir()
    renderer.render(1, video_file)

    ass = video_file.with_suffix('.ass')
    assert ass.exists()
    body = ass.read_text(encoding='utf-8')
    assert '[Script Info]' in body
    assert 'hello' in body
    assert 'world' in body


def test_ban_words_filter_removes_matching_lines(tmp_path: pathlib.Path, paths: WorkspacePaths, logger: Logger) -> None:
    payload = [
        {'text': 'please keep me', 'time': 100, 'color': '#FFFFFF', 'position': 0},
        {'text': 'contains banword1', 'time': 200, 'color': '#FFFFFF', 'position': 0},
        {'text': 'has banword2 here', 'time': 300, 'color': '#FFFFFF', 'position': 0},
    ]
    client = _FakeClient(payload)
    renderer = DanmuRenderer(client, logger)

    video_file = tmp_path / 'episode.mp4'
    renderer.render(1, video_file, ban_words=['banword1', 'banword2'])

    body = video_file.with_suffix('.ass').read_text(encoding='utf-8')
    assert 'please keep me' in body
    assert 'banword1' not in body
    assert 'banword2' not in body


def test_empty_danmu_response_writes_header_only(tmp_path: pathlib.Path, paths: WorkspacePaths, logger: Logger) -> None:
    client = _FakeClient([])
    renderer = DanmuRenderer(client, logger)

    video_file = tmp_path / 'episode.mp4'
    renderer.render(1, video_file)

    body = video_file.with_suffix('.ass').read_text(encoding='utf-8')
    assert '[Script Info]' in body
    assert 'Dialogue:' not in body


def test_fetch_failure_is_swallowed(tmp_path: pathlib.Path, paths: WorkspacePaths, logger: Logger) -> None:
    client = _FakeClient(fail=True)
    renderer = DanmuRenderer(client, logger)

    video_file = tmp_path / 'episode.mp4'
    # Must not raise.
    renderer.render(1, video_file)

"""Tests for ``CompositeNotifier``."""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Mapping
from typing import Any

import pytest

from app.downloader.notifier import CompositeNotifier
from app.logging_ import Logger
from app.models import AppSettings, CoolQSettings


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


class _FakeClient:
    def __init__(self, *, chat_id: str = '999') -> None:
        self.get_calls: list[str] = []
        self.json_calls: list[str] = []
        self.chat_id = chat_id
        self.raise_on: set[str] = set()

    def get(
        self,
        url: str,
        *,
        no_cookies: bool = False,
        max_retry: int = 3,
        extra_headers: Mapping[str, str] | None = None,
        use_pyhttpx: bool = False,
    ) -> _FakeResponse:
        self.get_calls.append(url)
        if url in self.raise_on:
            raise RuntimeError('simulated failure')
        return _FakeResponse()

    def get_json(
        self,
        url: str,
        *,
        no_cookies: bool = False,
        max_retry: int = 3,
        extra_headers: Mapping[str, str] | None = None,
        use_pyhttpx: bool = False,
    ) -> Any:
        self.json_calls.append(url)
        if 'getUpdates' in url:
            return {'result': [{'message': {'chat': {'id': int(self.chat_id)}}}]}
        return {}


@pytest.fixture
def logger(tmp_path: pathlib.Path) -> Logger:
    return Logger(tmp_path / 'logs', save_logs=False, quantity_of_logs=7)


def _settings(**overrides: Any) -> AppSettings:
    base: dict[str, Any] = {}
    base.update(overrides)
    return AppSettings(**base)


def test_coolq_fires_each_query_url(logger: Logger) -> None:
    client = _FakeClient()
    settings = _settings(
        coolq_notify=True,
        coolq_settings=CoolQSettings(
            query=[
                'http://cq.example.com/send_private_msg',
                'http://cq.example.com/send_group_msg?qq=1234',
            ],
            msg_argument_name='message',
            message_suffix='',
        ),
    )
    notifier = CompositeNotifier(settings, client, logger)
    notifier.notify_completed(filename='episode.mp4', size_mb=42, sn=1)

    assert len(client.get_calls) == 2
    for call in client.get_calls:
        assert 'message=' in call


def test_telebot_fires_with_explicit_chat_id(logger: Logger) -> None:
    client = _FakeClient()
    settings = _settings(
        telebot_notify=True,
        telebot_token='TOKEN',
        telebot_use_chat_id=True,
        telebot_chat_id='12345',
    )
    notifier = CompositeNotifier(settings, client, logger)
    notifier.notify_completed(filename='ep.mp4', size_mb=1, sn=1)
    assert any('sendMessage?chat_id=12345' in c for c in client.get_calls)
    # With explicit chat id, no getUpdates call.
    assert not any('getUpdates' in c for c in client.json_calls)


def test_telebot_falls_back_to_getUpdates(logger: Logger) -> None:
    client = _FakeClient(chat_id='999')
    settings = _settings(
        telebot_notify=True,
        telebot_token='TOKEN',
        telebot_use_chat_id=False,
    )
    notifier = CompositeNotifier(settings, client, logger)
    notifier.notify_completed(filename='ep.mp4', size_mb=1, sn=1)
    assert any('getUpdates' in c for c in client.json_calls)
    assert any('chat_id=999' in c for c in client.get_calls)


def test_discord_fires_webhook(logger: Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class _FakeResp:
        status_code = 204
        text = ''

    def fake_post(url: str, *, json: Any, timeout: float) -> Any:
        calls.append({'url': url, 'json': json, 'timeout': timeout})
        return _FakeResp()

    monkeypatch.setattr('app.downloader.notifier.requests.post', fake_post)

    client = _FakeClient()
    settings = _settings(discord_notify=True, discord_token='https://discord.example/hook')
    notifier = CompositeNotifier(settings, client, logger)
    notifier.notify_completed(filename='ep.mp4', size_mb=1, sn=1)
    assert len(calls) == 1
    assert calls[0]['url'] == 'https://discord.example/hook'


def test_plex_refresh_fires(logger: Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _FakeResp:
        status_code = 200
        text = ''

    def fake_get(url: str, *, timeout: float) -> Any:
        calls.append(url)
        return _FakeResp()

    monkeypatch.setattr('app.downloader.notifier.requests.get', fake_get)

    client = _FakeClient()
    settings = _settings(
        plex_refresh=True,
        plex_url='plex.example.com:32400',
        plex_token='TOKEN',
        plex_section='3',
    )
    notifier = CompositeNotifier(settings, client, logger)
    notifier.notify_completed(filename='ep.mp4', size_mb=1, sn=1)
    assert len(calls) == 1
    assert 'sections/3/refresh' in calls[0]
    assert 'X-Plex-Token=TOKEN' in calls[0]


def test_channel_failure_does_not_block_other_channels(logger: Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    # Make coolq raise.
    client.raise_on = {'http://cq.example.com/s?message=%E3%80%90aniGamerPlus'}

    # Simplest: raise on any URL that starts with the coolq base.
    orig_get = client.get

    def get_stub(url: str, **kwargs: Any) -> _FakeResponse:
        if url.startswith('http://cq.example.com/'):
            raise RuntimeError('cq fail')
        return orig_get(url, **kwargs)

    client.get = get_stub  # type: ignore[method-assign]

    discord_calls: list[str] = []

    class _FakeResp:
        status_code = 204
        text = ''

    def fake_post(url: str, *, json: Any, timeout: float) -> Any:
        discord_calls.append(url)
        return _FakeResp()

    monkeypatch.setattr('app.downloader.notifier.requests.post', fake_post)

    settings = _settings(
        coolq_notify=True,
        coolq_settings=CoolQSettings(query=['http://cq.example.com/s']),
        discord_notify=True,
        discord_token='https://d.example/hook',
    )
    notifier = CompositeNotifier(settings, client, logger)
    # Must not raise, and discord must still fire.
    notifier.notify_completed(filename='ep.mp4', size_mb=1, sn=1)
    assert discord_calls == ['https://d.example/hook']


def test_all_flags_false_is_no_op(logger: Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    post_calls: list[Any] = []
    get_calls: list[Any] = []

    monkeypatch.setattr(
        'app.downloader.notifier.requests.post',
        lambda *a, **kw: post_calls.append((a, kw)),
    )
    monkeypatch.setattr(
        'app.downloader.notifier.requests.get',
        lambda *a, **kw: get_calls.append((a, kw)),
    )

    client = _FakeClient()
    settings = _settings()  # all notification flags default False
    notifier = CompositeNotifier(settings, client, logger)
    notifier.notify_completed(filename='ep.mp4', size_mb=1, sn=1)

    assert client.get_calls == []
    assert client.json_calls == []
    assert post_calls == []
    assert get_calls == []


def test_coolq_two_urls_two_http_calls(logger: Logger) -> None:
    client = _FakeClient()
    settings = _settings(
        coolq_notify=True,
        coolq_settings=CoolQSettings(
            query=['http://a.example/s', 'http://b.example/s'],
        ),
    )
    notifier = CompositeNotifier(settings, client, logger)
    notifier.notify_completed(filename='ep.mp4', size_mb=1, sn=1)
    assert len(client.get_calls) == 2
    assert client.get_calls[0].startswith('http://a.example/s?')
    assert client.get_calls[1].startswith('http://b.example/s?')

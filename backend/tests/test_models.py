"""Tests for pydantic models (alias round-trip, validation)."""

from __future__ import annotations

import pydantic
import pytest

from app.models import ManualTaskRequest, WebSettings


def test_web_settings_accepts_hyphenated_alias() -> None:
    payload = {'multi-thread': 3, 'download_resolution': '1080'}
    model = WebSettings.model_validate(payload)
    assert model.multi_thread == 3


def test_web_settings_round_trip_emits_alias() -> None:
    model = WebSettings(multi_thread=4)
    dumped = model.model_dump(by_alias=True)
    assert 'multi-thread' in dumped
    assert dumped['multi-thread'] == 4
    assert 'multi_thread' not in dumped


def test_web_settings_rejects_unknown_resolution() -> None:
    with pytest.raises(pydantic.ValidationError):
        WebSettings.model_validate({'download_resolution': '2160'})


def test_manual_task_request_validates_thread_range() -> None:
    with pytest.raises(pydantic.ValidationError):
        ManualTaskRequest(sn='1', thread=0)
    with pytest.raises(pydantic.ValidationError):
        ManualTaskRequest(sn='1', thread=100)


def test_manual_task_request_mode_literal() -> None:
    with pytest.raises(pydantic.ValidationError):
        ManualTaskRequest.model_validate({'sn': '1', 'mode': 'not-a-mode'})

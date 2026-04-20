"""Tests for :func:`app.downloader._file_utils.move_file`."""

from __future__ import annotations

import errno
import pathlib
from unittest import mock

import pytest

from app.downloader._file_utils import move_file


def test_move_file_same_fs_uses_replace(tmp_path: pathlib.Path) -> None:
    """Normal same-filesystem move: src lands at dst, src no longer exists."""
    src = tmp_path / 'src.mp4'
    dst = tmp_path / 'dst.mp4'
    src.write_bytes(b'\x00' * 1024)

    move_file(src, dst)

    assert dst.exists(), 'dst must exist after move'
    assert not src.exists(), 'src must be gone after move'
    assert dst.stat().st_size == 1024


def test_move_file_overwrites_existing_dst(tmp_path: pathlib.Path) -> None:
    """move_file must silently overwrite an existing dst (same as Path.replace)."""
    src = tmp_path / 'src.mp4'
    dst = tmp_path / 'dst.mp4'
    src.write_bytes(b'new-content')
    dst.write_bytes(b'old-content')

    move_file(src, dst)

    assert dst.read_bytes() == b'new-content'
    assert not src.exists()


def test_move_file_cross_device_falls_back_to_shutil(tmp_path: pathlib.Path) -> None:
    """When Path.replace raises EXDEV, move_file must fall back to shutil.move
    and the file must still end up at dst."""
    src = tmp_path / 'src.mp4'
    dst = tmp_path / 'dst.mp4'
    src.write_bytes(b'\xff' * 512)

    exdev = OSError(errno.EXDEV, 'Invalid cross-device link')

    with mock.patch('pathlib.Path.replace', side_effect=exdev):
        with mock.patch('shutil.move') as mock_move:
            # shutil.move is patched: simulate it by writing dst ourselves so
            # the test can verify the call arguments without touching the real FS.
            def _fake_move(s: str, d: str) -> None:
                pathlib.Path(d).write_bytes(pathlib.Path(s).read_bytes())

            mock_move.side_effect = _fake_move
            move_file(src, dst)

    mock_move.assert_called_once_with(str(src), str(dst))
    assert dst.exists()
    assert dst.read_bytes() == b'\xff' * 512


def test_move_file_propagates_non_exdev_errors(tmp_path: pathlib.Path) -> None:
    """OSError with errno != EXDEV (e.g. EACCES) must propagate unchanged."""
    src = tmp_path / 'src.mp4'
    dst = tmp_path / 'dst.mp4'
    src.write_bytes(b'data')

    eacces = OSError(errno.EACCES, 'Permission denied')

    with mock.patch('pathlib.Path.replace', side_effect=eacces):
        with pytest.raises(OSError) as exc_info:
            move_file(src, dst)

    assert exc_info.value.errno == errno.EACCES

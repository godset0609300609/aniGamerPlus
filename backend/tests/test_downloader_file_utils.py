"""Tests for :func:`app.downloader._file_utils.move_file`."""

from __future__ import annotations

import errno
import os
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


def test_move_file_cross_device_fsyncs_destination_and_dir(
    tmp_path: pathlib.Path,
) -> None:
    """After an EXDEV fallback, os.fsync must be called for both the file fd
    and the parent-directory fd.

    We stub os.open so it always returns a sentinel fd (99) even on Windows
    where opening a directory with O_RDONLY raises PermissionError.  That lets
    os.fsync recording work for the directory branch on all platforms.
    """
    src = tmp_path / 'src.mp4'
    dst = tmp_path / 'dst.mp4'
    src.write_bytes(b'\xab' * 256)

    exdev = OSError(errno.EXDEV, 'Invalid cross-device link')
    fsync_calls: list[int] = []
    _real_open = os.open
    FAKE_DIR_FD = 99

    def _fake_move(s: str, d: str) -> None:
        pathlib.Path(d).write_bytes(pathlib.Path(s).read_bytes())

    def _recording_fsync(fd: int) -> None:
        fsync_calls.append(fd)

    def _stubbed_os_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
        # Return a real fd for the file (used inside open(path, 'rb')), but
        # return a sentinel for the parent-directory open so fsync can be
        # recorded without requiring directory-open support (Windows).
        target = pathlib.Path(path)
        if target.is_dir():
            return FAKE_DIR_FD
        return _real_open(path, flags, *args, **kwargs)

    def _stubbed_os_close(fd: int) -> None:
        if fd != FAKE_DIR_FD:
            os.close(fd)  # type: ignore[arg-type]  # real close

    with (
        mock.patch('pathlib.Path.replace', side_effect=exdev),
        mock.patch('shutil.move', side_effect=_fake_move),
        mock.patch('os.fsync', side_effect=_recording_fsync),
        mock.patch('app.downloader._file_utils.os.open', side_effect=_stubbed_os_open),
        mock.patch('app.downloader._file_utils.os.close', side_effect=_stubbed_os_close),
    ):
        move_file(src, dst)

    assert dst.exists()
    assert len(fsync_calls) >= 2, (
        f'expected at least 2 fsync calls (file + dir), got {len(fsync_calls)}'
    )


def test_move_file_same_device_skips_fsync(tmp_path: pathlib.Path) -> None:
    """On a same-device rename, os.fsync must NOT be called."""
    src = tmp_path / 'src.mp4'
    dst = tmp_path / 'dst.mp4'
    src.write_bytes(b'\x00' * 128)

    with mock.patch('os.fsync') as mock_fsync:
        move_file(src, dst)

    mock_fsync.assert_not_called()
    assert dst.exists()
    assert not src.exists()


def test_move_file_fsync_failure_does_not_propagate(
    tmp_path: pathlib.Path,
) -> None:
    """If os.fsync raises OSError, move_file must still complete successfully."""
    src = tmp_path / 'src.mp4'
    dst = tmp_path / 'dst.mp4'
    src.write_bytes(b'\xff' * 64)

    exdev = OSError(errno.EXDEV, 'Invalid cross-device link')

    def _fake_move(s: str, d: str) -> None:
        pathlib.Path(d).write_bytes(pathlib.Path(s).read_bytes())

    with (
        mock.patch('pathlib.Path.replace', side_effect=exdev),
        mock.patch('shutil.move', side_effect=_fake_move),
        mock.patch('os.fsync', side_effect=OSError('fsync not supported')),
    ):
        move_file(src, dst)  # must not raise

    assert dst.exists()
    assert dst.read_bytes() == b'\xff' * 64

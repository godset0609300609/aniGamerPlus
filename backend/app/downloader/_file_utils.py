"""Shared file-operation helpers for downloaders."""

from __future__ import annotations

import contextlib
import errno
import os
import pathlib
import shutil


def move_file(src: pathlib.Path, dst: pathlib.Path) -> None:
    """Move *src* to *dst*, tolerating cross-device links (EXDEV) via copy+delete.

    Overwrites *dst* if it already exists — same semantic as
    :meth:`pathlib.Path.replace`.

    On Linux, ``rename(2)`` only works within the same filesystem.  Docker
    deployments commonly put the temp directory on one mount (e.g. a tmpfs
    or the image layer) and the downloads directory on a different bind-mount,
    causing ``OSError: [Errno 18] Invalid cross-device link``.  This helper
    falls back to :func:`shutil.move`, which performs a copy-then-delete when
    the underlying ``rename`` fails.

    After a cross-device move the file bytes sit in the OS page cache; the host
    side of a Docker bind mount won't see the file until the kernel flushes.
    :func:`_fsync_file_and_dir` forces that flush so the file appears promptly.
    Same-device :meth:`~pathlib.Path.replace` (``rename(2)``) is atomic at the
    VFS layer and needs no fsync.
    """
    if dst.exists():
        dst.unlink()
    try:
        src.replace(dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        # Cross-device — use shutil.move (copy2 + unlink) then force flush.
        shutil.move(str(src), str(dst))
        _fsync_file_and_dir(dst)


def _fsync_file_and_dir(path: pathlib.Path) -> None:
    """Best-effort fsync on a file and its parent directory.

    Needed after a cross-device move on Docker bind mounts: the host
    filesystem doesn't see the new file until the kernel flushes the
    page cache + directory entry.  A plain ``shutil.move`` returns
    before either happens.

    Silent on ``OSError`` — some filesystems (Windows FAT, tmpfs,
    bind mounts without write-back) reject fsync on directories.
    """
    # Flush the file's data pages.
    with contextlib.suppress(OSError), open(path, 'rb') as fh:
        os.fsync(fh.fileno())
    # Flush the parent directory entry.
    with contextlib.suppress(OSError):
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

"""Shared file-operation helpers for downloaders."""

from __future__ import annotations

import errno
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
    """
    if dst.exists():
        dst.unlink()
    try:
        src.replace(dst)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            # Different filesystems — shutil.move handles cross-device via
            # copy2 + unlink internally.
            shutil.move(str(src), str(dst))
        else:
            raise

"""Low-level file helpers shared by persistence repositories.

Currently exposes a single helper:

``atomic_write_text``
    Write a UTF-8 text file via a rename for atomicity, with a fallback to
    in-place overwrite when the destination inode is pinned by a Docker
    single-file bind mount (EBUSY, errno 16).
"""

from __future__ import annotations

import contextlib
import errno
import os
import pathlib
import tempfile


def atomic_write_text(path: pathlib.Path, content: str) -> None:
    """Write *content* to *path* as UTF-8, preferring an atomic rename.

    The sequence is:

    1. Create a sibling temp file via :func:`tempfile.mkstemp`.
    2. Write *content* to the temp file.
    3. Rename the temp file onto *path* with :func:`os.replace` — atomic on
       POSIX; near-atomic on Windows (replaces dest in one syscall).

    Docker single-file bind-mount fallback
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    When ``/app/cookie.txt`` (or any other file) is bind-mounted from the
    host as a *single file*, the destination inode is pinned by the mount.
    ``os.replace`` then fails with ``[Errno 16] EBUSY``.  In that case we
    fall back to an in-place overwrite of the file, which is less atomic but
    compatible with the bind-mount constraint.

    Any other :class:`OSError` (permissions, disk full, …) is re-raised
    unchanged.  The temp file is always cleaned up on error.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(content)
        try:
            os.replace(tmp, path)
        except OSError as exc:
            # Docker single-file bind mounts: the destination inode is pinned
            # by the mount so atomic rename fails with EBUSY (16). Fall back
            # to in-place overwrite — less atomic but survives the bind mount.
            if exc.errno == errno.EBUSY:
                with open(path, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(content)
                os.unlink(tmp)
            else:
                raise
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

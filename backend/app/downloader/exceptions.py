"""Downloader exception hierarchy.

These replace the single ``TryTooManyTimeError`` defined in legacy
``Anime.py``. The new layer distinguishes "retries exhausted" (a transient
HTTP problem) from "cookie revoked" (we need the user to re-authenticate)
from "no stream at all" (VIP-only / deleted / geo-blocked).
"""

from __future__ import annotations


class TryTooManyTimeError(Exception):
    """Downstream retries have exhausted their budget."""


class InvalidCookieError(Exception):
    """Cookie was recognised by the server as revoked."""


class NoAvailableStreamError(Exception):
    """Requested sn has no playable stream (VIP-only, deleted, etc)."""


class TaskCancelledError(Exception):
    """Raised when a running download receives a cancel signal."""

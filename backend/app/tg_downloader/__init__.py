"""Telegram User API (MTProto, via hydrogram) downloader pipeline.

Per-Discord-user Telegram chat monitoring/downloading — the "watch chats I'm
in and download new media" counterpart to the animad / Bilibili / BT
pipelines, but driven by each user's own Telegram account session rather
than the shared Bot API (whose 20MB file cap makes it unusable for video).

Formerly pyrogram, now hydrogram
---------------------------------
This package used to sit on ``pyrogram==2.0.106``. It was migrated to
``hydrogram`` (an actively-maintained fork with an identical ``Client`` /
``raw`` / ``errors`` / ``types`` API surface) because pyrogram 2.0.106's
``pyrogram.utils.get_peer_type()`` still carries a narrow, 32-bit-derived
``MIN_CHANNEL_ID`` that rejects newly-created large-id supergroups/channels
outright (``ValueError: Peer id invalid``) — hydrogram 0.2.0 widened that
range specifically to support them.

The migration also retired a Python 3.14 import-time compat shim that used
to live here: pyrogram's ``pyrogram.sync`` module eagerly wrapped every
async ``Client`` method with a synchronous counterpart at import time via
``asyncio.get_event_loop()``, which raised ``RuntimeError: There is no
current event loop in thread 'MainThread'`` on Python 3.14 whenever
``import pyrogram`` was the first import in a thread with no running loop
(e.g. during ``app.core.build_container()`` at process startup, before
uvicorn's event loop exists). hydrogram has no equivalent ``hydrogram.sync``
module — it dropped that legacy sync-wrapping mechanism entirely — so a bare
``import hydrogram`` is safe with no loop present and no shim is needed.
None of this affects the async code paths this package actually uses
(``await client.connect()`` etc.), which always run inside FastAPI's real
running loop regardless.
"""

from __future__ import annotations

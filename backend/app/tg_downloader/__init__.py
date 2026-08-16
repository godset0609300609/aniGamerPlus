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

hydrogram ``ChannelForbidden`` parsing shim
--------------------------------------------
``hydrogram.types.Chat._parse_channel_chat`` (hydrogram==0.2.0) crashes with
``AttributeError`` when parsing a channel/supergroup the current account is
banned from or otherwise can't access (Telegram's raw ``ChannelForbidden``
type) — it unconditionally reads attributes, e.g. ``channel.verified``, that
only exist on the regular ``Channel`` type. Because ``Client.get_dialogs()``
builds every ``Dialog`` in a paginated batch before yielding any of them,
one such channel anywhere in a user's dialog list kills the whole listing.
``app.tg_downloader.hydrogram_compat.apply_patches()`` monkey-patches
``Chat._parse_channel_chat`` to tolerate ``ChannelForbidden`` (mirroring how
``Chat._parse_chat_chat`` already handles its own forbidden counterpart,
``ChatForbidden``, via ``getattr``). It is called below, at import time of
this package, so every TG code path (which all import through here) gets
the patch before any hydrogram call that could hit this. See that module's
docstring for the full defect writeup, and
``app.services.tg_service.TgService.list_available_chats`` for the
user-visible bug this was causing (dialogs silently missing from
``GET /api/tg/chats/available``).

This shim can be deleted, along with this call and its import, once
upstream hydrogram fixes ``_parse_channel_chat`` to handle
``ChannelForbidden`` itself.
"""

from __future__ import annotations

from . import hydrogram_compat as _hydrogram_compat

_hydrogram_compat.apply_patches()

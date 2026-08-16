"""Runtime patches for defects in the vendored ``hydrogram`` client library.

Upstream defect: ``Chat._parse_channel_chat`` vs. ``raw.types.ChannelForbidden``
---------------------------------------------------------------------------
``hydrogram.types.Chat._parse_channel_chat`` (hydrogram==0.2.0,
``hydrogram/types/user_and_chats/chat.py``) is the staticmethod that turns a
raw MTProto channel constructor into a ``types.Chat``. It assumes its
argument is always ``raw.types.Channel`` and reads several attributes
unconditionally (``channel.verified``, ``channel.restricted``,
``channel.creator``, ``channel.scam``, ``channel.fake``, ``channel.forum``,
``channel.username``, ``channel.access_hash``, ``channel.restriction_reason``,
``channel.default_banned_rights``, ``channel.participants_count``,
``channel.noforwards``, ``channel.usernames``) with no ``getattr`` fallback
— unlike its sibling ``_parse_chat_chat``, which already guards every field
with ``getattr`` for exactly this reason.

Telegram, however, also represents a channel/supergroup the current account
was banned from (or otherwise can't access) as ``raw.types.ChannelForbidden``
— a *different* raw type sharing the same ``raw.base.Chat`` union, but with a
much smaller ``__slots__``: ``['id', 'access_hash', 'title', 'broadcast',
'megagroup', 'until_date']``. None of the attributes ``_parse_channel_chat``
reads unconditionally exist on it, so parsing one raises
``AttributeError: 'ChannelForbidden' object has no attribute 'verified'``.

That would be an annoyance in isolation, but it compounds badly inside
``Client.get_dialogs()``: Telegram's ``messages.getDialogs`` RPC is paginated
in batches of up to 100 dialogs, and hydrogram builds every ``Dialog`` (and
therefore every ``Chat``) in a batch before yielding any of them. One
``ChannelForbidden`` dialog anywhere in a batch — e.g. a channel the user was
kicked from since their last sync — kills the async generator outright, and
there is no way to resume it past just the bad entry. See
``app.services.tg_service.TgService.list_available_chats`` for the
call site this used to silently truncate.

The fix here patches ``Chat._parse_channel_chat`` to special-case
``raw.types.ChannelForbidden`` and build a minimal ``Chat`` directly from the
fields that *do* exist on it, instead of raising. Every other input
(``raw.types.Channel``, the only other type ``_parse_channel_chat`` is ever
actually called with) is delegated to the original implementation unchanged.

Applied once, at import time of this package (see ``app.tg_downloader
.__init__``), via :func:`apply_patches`.

When this can be removed
-------------------------
Once upstream hydrogram makes ``_parse_channel_chat`` tolerate
``ChannelForbidden`` (mirroring ``_parse_chat_chat``'s ``getattr`` guards),
this module and its call site in ``app.tg_downloader.__init__`` can be
deleted, and ``TgService.list_available_chats``'s ``except AttributeError``
backstop can revert to being purely defensive documentation (it already is —
see that method's docstring).
"""

from __future__ import annotations

import typing as T

import hydrogram
import hydrogram.enums
import hydrogram.raw
import hydrogram.types
import hydrogram.utils

#: Stamped onto the replacement function so :func:`apply_patches` can tell,
#: just by looking at the *current* ``Chat._parse_channel_chat``, whether
#: it has already been wrapped — makes the patch idempotent without needing
#: a separate module-level "have I run" flag (which would go stale if two
#: independent imports of this module somehow ended up as different module
#: objects, e.g. under an editable install path duplication).
_PATCHED_MARKER = '_anigamerplus_channel_forbidden_patch'


def apply_patches() -> None:
    """Apply every compat patch in this module. Safe to call more than once.

    Currently patches only ``hydrogram.types.Chat._parse_channel_chat`` (see
    module docstring). Call this before any code path that might reach
    ``Client.get_dialogs()`` / ``Client.get_chat()`` / anything else that
    parses a raw channel — in practice that means "as early as possible",
    which is why it runs at ``app.tg_downloader`` import time.
    """
    _patch_parse_channel_chat()


def _patch_parse_channel_chat() -> None:
    chat_cls = hydrogram.types.Chat
    current = chat_cls._parse_channel_chat
    if getattr(current, _PATCHED_MARKER, False):
        return  # already patched — nothing to do

    original = current

    def _parse_channel_chat_tolerant(
        # Untyped ``client``, matching the original's own signature (it
        # doesn't annotate it either — see hydrogram's source) — it's
        # ``hydrogram.Client | None`` at runtime (``None`` in these unit
        # tests), but ``Chat.__init__`` itself declares ``client:
        # hydrogram.Client = None``, so annotating this any stricter than
        # ``Any`` here would fight that same upstream inconsistency.
        client: T.Any,
        channel: hydrogram.raw.types.Channel | hydrogram.raw.types.ChannelForbidden,
    ) -> hydrogram.types.Chat:
        """Drop-in replacement for ``Chat._parse_channel_chat``.

        Delegates to the original for the normal ``raw.types.Channel`` case.
        For ``raw.types.ChannelForbidden`` (detected by ``isinstance``, not
        by probing for a missing attribute), builds a ``Chat`` from just the
        fields ``ChannelForbidden`` actually carries. Everything else on the
        returned ``Chat`` stays at its default (``None``/unset) — there is
        no raw data to fill it from, and callers already have to treat a
        forbidden channel's ``Chat`` as minimal (Telegram doesn't tell us
        anything more about a channel we can't access).
        """
        if not isinstance(channel, hydrogram.raw.types.ChannelForbidden):
            return original(client, channel)

        peer_id = hydrogram.utils.get_channel_id(channel.id)
        return hydrogram.types.Chat(
            id=peer_id,
            type=hydrogram.enums.ChatType.SUPERGROUP if channel.megagroup else hydrogram.enums.ChatType.CHANNEL,
            title=channel.title,
            # A channel Telegram reports as ChannelForbidden is, by
            # definition, one we're restricted from — there's no separate
            # "restricted" flag on the raw type to read, unlike Channel.
            is_restricted=True,
            client=client,
        )

    setattr(_parse_channel_chat_tolerant, _PATCHED_MARKER, True)
    # Monkey-patching a method onto the class is exactly what mypy's
    # method-assign check exists to flag — this ignore is the point of the
    # module, not a bug to fix.
    chat_cls._parse_channel_chat = staticmethod(_parse_channel_chat_tolerant)  # type: ignore[method-assign]

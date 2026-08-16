"""Pins a hydrogram==0.2.0 defect and the patch that works around it.

``hydrogram.types.Chat._parse_channel_chat`` (``hydrogram/types/user_and_chats
/chat.py``) unconditionally reads attributes — ``channel.verified``,
``channel.restricted``, ``channel.creator``, ``channel.scam``,
``channel.fake``, ``channel.forum``, ``channel.username``,
``channel.access_hash``, ``channel.restriction_reason``,
``channel.default_banned_rights``, ``channel.participants_count``,
``channel.noforwards``, ``channel.usernames`` — that exist only on
``raw.types.Channel``. Telegram also represents a channel/supergroup the
account can't access (banned, kicked, etc.) as the different raw type
``raw.types.ChannelForbidden``, whose ``__slots__`` are just ``['id',
'access_hash', 'title', 'broadcast', 'megagroup', 'until_date']`` — none of
what ``_parse_channel_chat`` reads unconditionally. Parsing one raises
``AttributeError: 'ChannelForbidden' object has no attribute 'verified'``.

``app.tg_downloader.hydrogram_compat.apply_patches()`` monkey-patches
``Chat._parse_channel_chat`` to special-case ``ChannelForbidden`` instead of
crashing (see that module's docstring for the full writeup, including why
this matters: it kills ``Client.get_dialogs()`` outright for any account
with such a channel in its dialog list).

These tests build and parse *real* ``hydrogram.raw.types`` objects through
the *real*, patched staticmethod — not stand-ins that only assert on the
shape of data we constructed ourselves — so a hydrogram upgrade that changes
``_parse_channel_chat``'s attribute access (and silently breaks this patch)
fails these tests instead of shipping quietly back into production.
"""

from __future__ import annotations

import hydrogram.enums
import hydrogram.raw
import hydrogram.types
import hydrogram.utils

from app.tg_downloader import hydrogram_compat


def _forbidden_channel(
    *, channel_id: int = 555, title: str = 'Kicked Channel', megagroup: bool = False
) -> hydrogram.raw.types.ChannelForbidden:
    return hydrogram.raw.types.ChannelForbidden(id=channel_id, access_hash=0, title=title, megagroup=megagroup)


def _real_channel(*, channel_id: int = 777, title: str = 'Real Channel') -> hydrogram.raw.types.Channel:
    """A fully-populated ``raw.types.Channel`` — everything ``_parse_channel_chat``'s
    original (unpatched) code path reads unconditionally must be present,
    exactly like a real one hydrogram would receive off the wire."""
    return hydrogram.raw.types.Channel(
        id=channel_id,
        title=title,
        photo=hydrogram.raw.types.ChatPhotoEmpty(),
        date=0,
        megagroup=True,
        verified=False,
        restricted=False,
        creator=False,
        scam=False,
        fake=False,
        forum=False,
        username=None,
        access_hash=123,
        restriction_reason=[],
        default_banned_rights=None,
        participants_count=42,
        noforwards=False,
        usernames=[],
    )


def test_apply_patches_lets_channel_forbidden_parse() -> None:
    """The whole point of the patch: this used to raise ``AttributeError``."""
    hydrogram_compat.apply_patches()
    forbidden = _forbidden_channel(channel_id=555, title='Kicked Channel')

    chat = hydrogram.types.Chat._parse_channel_chat(None, forbidden)

    assert isinstance(chat, hydrogram.types.Chat)
    assert chat.id == hydrogram.utils.get_channel_id(555)
    assert chat.type == hydrogram.enums.ChatType.CHANNEL
    assert chat.title == 'Kicked Channel'
    assert chat.is_restricted is True


def test_apply_patches_megagroup_forbidden_parses_as_supergroup() -> None:
    hydrogram_compat.apply_patches()
    forbidden = _forbidden_channel(channel_id=556, title='Kicked Supergroup', megagroup=True)

    chat = hydrogram.types.Chat._parse_channel_chat(None, forbidden)

    assert chat.type == hydrogram.enums.ChatType.SUPERGROUP
    assert chat.id == hydrogram.utils.get_channel_id(556)


def test_apply_patches_leaves_real_channel_parsing_unchanged() -> None:
    """Guard against the wrapper breaking the happy (``raw.types.Channel``) path
    it delegates to — every other caller of ``_parse_channel_chat`` still needs
    the original, full parse."""
    hydrogram_compat.apply_patches()
    channel = _real_channel()

    chat = hydrogram.types.Chat._parse_channel_chat(None, channel)

    assert chat.id == hydrogram.utils.get_channel_id(channel.id)
    assert chat.type == hydrogram.enums.ChatType.SUPERGROUP
    assert chat.title == 'Real Channel'
    assert chat.is_verified is False
    assert chat.is_restricted is False
    assert chat.is_creator is False
    assert chat.members_count == 42
    assert chat.has_protected_content is False


def test_apply_patches_is_idempotent() -> None:
    """Calling ``apply_patches()`` twice must not double-wrap the staticmethod."""
    hydrogram_compat.apply_patches()
    first = hydrogram.types.Chat._parse_channel_chat

    hydrogram_compat.apply_patches()
    second = hydrogram.types.Chat._parse_channel_chat

    assert first is second

    # And a double-wrapped version would still behave correctly, but
    # identity above is the real proof there's only one layer.
    forbidden = _forbidden_channel()
    chat = hydrogram.types.Chat._parse_channel_chat(None, forbidden)
    assert chat.title == 'Kicked Channel'

"""Regression pin for the pyrogram -> hydrogram migration's whole reason for
existing: large-id supergroup/channel peer resolution.

pyrogram 2.0.106's ``pyrogram.utils.get_peer_type()`` carries a narrow,
32-bit-derived ``MIN_CHANNEL_ID`` (``-1002147483647``) that rejects any
supergroup/channel id below it. Newly-created large-id supergroups commonly
fall outside that range, so ``resolve_peer()`` blew up with
``ValueError: Peer id invalid`` for real users, e.g.:

    TG回填 chat_id=-1004365674865 回填失敗: Peer id invalid: -1004365674865

hydrogram 0.2.0 widened ``MIN_CHANNEL_ID`` to ``-1007852516352`` specifically
to support these (hydrogram/hydrogram#25, "Support newly-created chats by
increasing MIN_CHANNEL_ID and MIN_CHAT_ID"). This test pins that fix against
the exact id from the real failure above so a future hydrogram upgrade (or
an accidental pyrogram reintroduction) can't silently regress it.
"""

from __future__ import annotations

from hydrogram import utils

#: The exact chat_id from the real user-reported failure this migration fixes.
_REAL_WORLD_LARGE_SUPERGROUP_ID = -1004365674865


def test_hydrogram_accepts_large_id_supergroup() -> None:
    assert utils.get_peer_type(_REAL_WORLD_LARGE_SUPERGROUP_ID) == 'channel'


def test_hydrogram_min_channel_id_is_wider_than_pyrogram_2_0_106s() -> None:
    """pyrogram 2.0.106's MIN_CHANNEL_ID was -1002147483647 (a 32-bit-derived
    bound) — hydrogram's must be more negative (wider range) for the id above
    to resolve, not merely equal or narrower."""
    pyrogram_2_0_106_min_channel_id = -1002147483647
    assert pyrogram_2_0_106_min_channel_id > utils.MIN_CHANNEL_ID

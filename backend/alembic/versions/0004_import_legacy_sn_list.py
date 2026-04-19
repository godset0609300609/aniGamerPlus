"""Import legacy sn_list.txt into anime_list_entries.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-18

If ``sn_list.txt`` exists at the workspace root, parse it and INSERT rows
into ``anime_list_entries``. After a successful import the file is renamed
to ``sn_list.txt.imported``. If the file is absent, this migration is a
no-op.

Owner assignment:
  - If at least one admin exists in ``users``, use that admin's ``id``.
  - Otherwise create a sentinel user (id = ``__legacy_import__``) with
    ``role="admin"`` and ``username="Legacy Import"``.

``downgrade()`` is a no-op — we don't try to undo data imports.
"""

from __future__ import annotations

import codecs
import datetime
import pathlib
import re
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SENTINEL_USER_ID = "__legacy_import__"

# ---------------------------------------------------------------------------
# Inline sn_list.txt parser (mirrors SnListRepository.parse_legacy)
# ---------------------------------------------------------------------------

_LEGAL_MODE_RE = re.compile(r"^(all|latest|largest-sn)$")
_TAG_LINE_RE = re.compile(r"^@.+")
_EMPTY_TAG_LINE_RE = re.compile(r"^@ *$")
_SN_LINE_RE = re.compile(r"^\d+$")
_COMMENT_STRIP_RE = re.compile(r"#.+\n")
_MULTI_SPACE_RE = re.compile(r" +")
_RENAME_RE = re.compile(r"<.*>")
_TRAILING_SPACE_RE = re.compile(r"( )+$")


def _parse_sn_list(path: pathlib.Path) -> list[dict[str, Any]]:
    """Parse ``sn_list.txt`` into a list of entry dicts.

    Each dict has keys: ``sn``, ``mode``, ``tag``, ``rename``.
    ``mode`` is always a non-empty string because the parser falls back to
    ``"latest"`` for unrecognised tokens.
    """
    data = path.read_bytes()
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8) :]
    raw = data.decode("utf-8")

    entries: list[dict[str, Any]] = []
    bangumi_tag = ""

    for line in raw.splitlines(keepends=True):
        if _TAG_LINE_RE.match(line) and not _EMPTY_TAG_LINE_RE.match(line):
            bangumi_tag = line[1:].rstrip("\n").rstrip("\r")
            continue
        if _EMPTY_TAG_LINE_RE.match(line):
            bangumi_tag = ""
            continue

        stripped = _COMMENT_STRIP_RE.sub("", line).strip()
        stripped = _MULTI_SPACE_RE.sub(" ", stripped)
        parts = stripped.split(" ")
        if not parts[0]:
            continue
        if not _SN_LINE_RE.match(parts[0]):
            continue

        sn = int(parts[0])
        rename = ""
        if len(parts) > 1:
            if _LEGAL_MODE_RE.match(parts[1]):
                mode = parts[1]
            else:
                mode = "latest"
            rename_match = _RENAME_RE.search(stripped)
            if rename_match:
                rename = rename_match.group(0)[1:-1]
        else:
            mode = "latest"

        tag_clean = _TRAILING_SPACE_RE.sub("", bangumi_tag)
        entries.append(
            {"sn": sn, "mode": mode, "tag": tag_clean, "rename": rename}
        )

    return entries


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()

    # Locate ``sn_list.txt`` relative to the Alembic script location.
    # The script is at ``<backend_root>/alembic/versions/0004_*.py``;
    # we need ``<backend_root>/sn_list.txt``.
    script_dir = pathlib.Path(__file__).resolve()
    backend_root = script_dir.parents[2]
    sn_list_path = backend_root / "sn_list.txt"

    if not sn_list_path.exists():
        print(f"[0004] sn_list.txt not found at {sn_list_path} — skipping import.")
        return

    if sn_list_path.stat().st_size == 0:
        print("[0004] sn_list.txt is empty — skipping import.")
        return

    entries = _parse_sn_list(sn_list_path)
    if not entries:
        print("[0004] sn_list.txt parsed to 0 entries — skipping import.")
        return

    # Determine owner: first admin or sentinel.
    admin_row = bind.execute(
        sa.text("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
    ).fetchone()

    if admin_row is not None:
        owner_id: str = admin_row[0]
    else:
        # Check whether sentinel already exists (idempotency).
        sentinel_row = bind.execute(
            sa.text("SELECT id FROM users WHERE id = :uid"),
            {"uid": _SENTINEL_USER_ID},
        ).fetchone()
        if sentinel_row is None:
            now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
            bind.execute(
                sa.text(
                    "INSERT INTO users (id, username, avatar_url, role, created_at)"
                    " VALUES (:id, :username, NULL, 'admin', :now)"
                ),
                {"id": _SENTINEL_USER_ID, "username": "Legacy Import", "now": now},
            )
        owner_id = _SENTINEL_USER_ID

    # Insert entries (skip those already present — idempotency).
    existing_sns_rows = bind.execute(
        sa.text(
            "SELECT sn FROM anime_list_entries WHERE user_id = :uid"
        ),
        {"uid": owner_id},
    ).fetchall()
    existing_sns = {row[0] for row in existing_sns_rows}

    inserted = 0
    for idx, entry in enumerate(entries):
        if entry["sn"] in existing_sns:
            continue
        # Note: the rename column was dropped in migration 0006.
        # Use column-list INSERT to be resilient regardless of which
        # columns exist — always insert the minimal required set.
        bind.execute(
            sa.text(
                "INSERT INTO anime_list_entries"
                " (user_id, sn, enabled, mode, tag, comment, sort_order)"
                " VALUES (:user_id, :sn, 1, :mode, :tag, '', :sort_order)"
            ),
            {
                "user_id": owner_id,
                "sn": entry["sn"],
                "mode": entry["mode"],
                "tag": entry["tag"],
                "sort_order": idx,
            },
        )
        inserted += 1

    print(f"[0004] Imported {inserted} entries from sn_list.txt (owner={owner_id!r}).")

    # Rename sn_list.txt -> sn_list.txt.imported
    imported_path = sn_list_path.with_suffix(".txt.imported")
    sn_list_path.rename(imported_path)
    print(f"[0004] Renamed {sn_list_path.name} -> {imported_path.name}.")


def downgrade() -> None:
    # Intentionally a no-op: we don't undo data imports.
    pass

"""Resolution of one group (design §8.3 "Confirming"), shared by the API and the bulk job."""

from __future__ import annotations

import sqlite3

from visualstore.db import groups as groups_db
from visualstore.db import pairs as pairs_db


def commit_decisions(
    conn: sqlite3.Connection, group_id: bytes, decisions: dict[bytes, str]
) -> list[bytes]:
    """Store keep/delete decisions and mark pairs among kept members related_keep.
    Returns the file ids to trash; the caller enqueues apply_action for them or, when the
    list is empty, marks the group resolved."""
    groups_db.set_decisions(conn, group_id, decisions)
    kept = {f for f, d in decisions.items() if d == "keep"}
    for p in pairs_db.inner_pairs(conn, kept):
        if p["judgment"] == "none":
            pairs_db.set_judgment(
                conn, bytes(p["file_a"]), bytes(p["file_b"]), "related_keep", "user"
            )
    return [f for f, d in decisions.items() if d == "delete"]

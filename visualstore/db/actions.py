"""Action table: audit trail and the memory the trash needs for restore (design §5.1 Action)."""

from __future__ import annotations

import json
import sqlite3

from visualstore.clock import now_iso
from visualstore.ids import id_to_str, new_id

TYPES = ("trash", "restore", "move", "rename", "purge")


def record(
    conn: sqlite3.Connection, type: str, file_id: bytes | None, before: dict, after: dict
) -> bytes:
    if type not in TYPES:
        raise ValueError(f"action type must be one of {TYPES}")
    action_id = new_id()
    with conn:
        conn.execute(
            "INSERT INTO action (id, type, file_id, before, after, at) VALUES (?, ?, ?, ?, ?, ?)",
            (action_id, type, file_id, json.dumps(before), json.dumps(after), now_iso()),
        )
    return action_id


def get(conn: sqlite3.Connection, action_id: bytes) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM action WHERE id = ?", (action_id,)).fetchone()


def list_trash(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM action WHERE type = 'trash' AND superseded_at IS NULL"
        " ORDER BY at DESC, rowid DESC LIMIT ?",
        (limit,),
    ).fetchall()


def trash_older_than(conn: sqlite3.Connection, cutoff_iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM action WHERE type = 'trash' AND superseded_at IS NULL AND at < ?"
        " ORDER BY at",
        (cutoff_iso,),
    ).fetchall()


def supersede(conn: sqlite3.Connection, action_id: bytes) -> None:
    with conn:
        conn.execute("UPDATE action SET superseded_at = ? WHERE id = ?", (now_iso(), action_id))


def action_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["id"] = id_to_str(row["id"])
    d["file_id"] = id_to_str(row["file_id"]) if row["file_id"] else None
    d["before"] = json.loads(row["before"])
    d["after"] = json.loads(row["after"])
    return d

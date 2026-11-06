"""Pair table queries (design §5.1 Pair)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from visualstore.clock import now_iso
from visualstore.ids import id_to_str

JUDGMENTS = ("none", "related_keep", "unrelated")


def ordered(a: bytes, b: bytes) -> tuple[bytes, bytes]:
    return (a, b) if a < b else (b, a)


def upsert_sha1_pair(conn: sqlite3.Connection, a: bytes, b: bytes) -> None:
    fa, fb = ordered(a, b)
    with conn:
        conn.execute(
            "INSERT INTO pair (file_a, file_b, distance, source) VALUES (?, ?, 0.0, 'sha1')"
            " ON CONFLICT(file_a, file_b) DO UPDATE SET source = 'sha1', distance = 0.0",
            (fa, fb),
        )


def set_judgment(conn: sqlite3.Connection, a: bytes, b: bytes, judgment: str, origin: str) -> bool:
    if judgment not in JUDGMENTS:
        raise ValueError(f"judgment must be one of {JUDGMENTS}")
    fa, fb = ordered(a, b)
    with conn:
        cur = conn.execute(
            "UPDATE pair SET judgment = ?, judged_at = ?, judgment_origin = ?"
            " WHERE file_a = ? AND file_b = ?",
            (judgment, now_iso(), origin, fa, fb),
        )
    return cur.rowcount == 1


def edges_touching(conn: sqlite3.Connection, file_ids: Iterable[bytes]) -> list[sqlite3.Row]:
    ids = list(file_ids)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT * FROM pair WHERE file_a IN ({marks}) OR file_b IN ({marks})", (*ids, *ids)
    ).fetchall()


def pairs_of_file(conn: sqlite3.Connection, file_id: bytes) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pair WHERE file_a = ? OR file_b = ? ORDER BY distance", (file_id, file_id)
    ).fetchall()


def pair_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["file_a"] = id_to_str(row["file_a"])
    d["file_b"] = id_to_str(row["file_b"])
    return d

"""Group, membership and dirty-queue queries (design §5.1 Group, §7.5)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from visualstore.clock import now_iso
from visualstore.db.util import chunked
from visualstore.ids import id_to_str, new_id

STATUSES = ("open", "auto_resolved", "resolved", "dismissed")


@dataclass(frozen=True)
class NewGroup:
    members: frozenset[bytes]
    min_distance: float | None
    has_identical: bool
    status: str
    decisions: dict[bytes, str]
    reuse_id: bytes | None = None  # the existing group with this member set keeps its id


# -- dirty queue -------------------------------------------------------------------------


def mark_dirty(conn: sqlite3.Connection, file_ids: Iterable[bytes]) -> None:
    """A repeated mark bumps the generation, so a regroup that peeked the old one leaves it."""
    with conn:
        conn.executemany(
            "INSERT INTO regroup_queue (file_id, gen) VALUES (?, 0)"
            " ON CONFLICT(file_id) DO UPDATE SET gen = gen + 1",
            [(f,) for f in file_ids],
        )


def mark_all_paired_dirty(conn: sqlite3.Connection) -> int:
    """Every file that has a pair: used when the grouping distance changes."""
    with conn:
        cur = conn.execute(
            "INSERT INTO regroup_queue (file_id, gen)"
            " SELECT id, 0 FROM file"
            " WHERE id IN (SELECT file_a FROM pair UNION SELECT file_b FROM pair)"
            " ON CONFLICT(file_id) DO UPDATE SET gen = gen + 1"
        )
    return cur.rowcount


def peek_marks(conn: sqlite3.Connection, limit: int = 5000) -> list[tuple[bytes, int]]:
    rows = conn.execute("SELECT file_id, gen FROM regroup_queue LIMIT ?", (limit,)).fetchall()
    return [(bytes(r["file_id"]), int(r["gen"])) for r in rows]


def peek_dirty(conn: sqlite3.Connection, limit: int = 5000) -> list[bytes]:
    return [f for f, _ in peek_marks(conn, limit)]


def clear_marks(conn: sqlite3.Connection, marks: Iterable[tuple[bytes, int]]) -> None:
    """Remove the marks exactly as peeked; a mark bumped meanwhile stays for the next pass."""
    with conn:
        conn.executemany(
            "DELETE FROM regroup_queue WHERE file_id = ? AND gen = ?", [(f, g) for f, g in marks]
        )


def clear_dirty(conn: sqlite3.Connection, file_ids: Iterable[bytes]) -> None:
    with conn:
        conn.executemany("DELETE FROM regroup_queue WHERE file_id = ?", [(f,) for f in file_ids])


def pop_dirty(conn: sqlite3.Connection, limit: int = 5000) -> list[bytes]:
    ids = peek_dirty(conn, limit)
    clear_dirty(conn, ids)
    return ids


def dirty_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM regroup_queue").fetchone()[0]


# -- groups ------------------------------------------------------------------------------

_MEMBER_SQL = (
    "SELECT m.file_id, m.decision, f.directory, f.name, f.size, f.sha1, f.state, f.width, f.height,"
    " f.folder_id, fo.name AS folder_name, fo.path AS folder_path, fo.role,"
    " fo.priority AS folder_priority, e.media_type"
    " FROM file_group_member m JOIN file f ON f.id = m.file_id"
    " JOIN folder fo ON fo.id = f.folder_id"
    " JOIN extension e ON e.id = f.extension_id"
)


def group_of_file(conn: sqlite3.Connection, file_id: bytes) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT g.* FROM file_group g JOIN file_group_member m ON m.group_id = g.id"
        " WHERE m.file_id = ?",
        (file_id,),
    ).fetchone()


def get_group(conn: sqlite3.Connection, group_id: bytes) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM file_group WHERE id = ?", (group_id,)).fetchone()


def members(conn: sqlite3.Connection, group_id: bytes) -> list[sqlite3.Row]:
    return conn.execute(
        f"{_MEMBER_SQL} WHERE m.group_id = ? ORDER BY fo.priority, fo.name, f.directory, f.name",
        (group_id,),
    ).fetchall()


def member_file_ids(conn: sqlite3.Connection, group_ids: Iterable[bytes]) -> list[bytes]:
    out: list[bytes] = []
    for chunk in chunked(list(group_ids)):
        out.extend(_member_file_ids_chunk(conn, chunk))
    return out


def _member_file_ids_chunk(conn: sqlite3.Connection, group_ids: list[bytes]) -> list[bytes]:
    ids = list(group_ids)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT file_id FROM file_group_member WHERE group_id IN ({marks})", ids
    ).fetchall()
    return [bytes(r["file_id"]) for r in rows]


def replace_groups(
    conn: sqlite3.Connection, remove_group_ids: Iterable[bytes], new_groups: list[NewGroup]
) -> list[bytes]:
    created: list[bytes] = []
    now = now_iso()
    kept = {ng.reuse_id for ng in new_groups if ng.reuse_id is not None}
    with conn:
        conn.executemany(
            "DELETE FROM file_group WHERE id = ?",
            [(g,) for g in remove_group_ids if g not in kept],
        )
        for ng in new_groups:
            if ng.reuse_id is not None:  # same member set as before: the id (and links) stay
                gid = ng.reuse_id
                conn.execute(
                    "UPDATE file_group SET member_count = ?, min_distance = ?, has_identical = ?,"
                    " status = ?, updated_at = ? WHERE id = ?",
                    (
                        len(ng.members),
                        ng.min_distance,
                        1 if ng.has_identical else 0,
                        ng.status,
                        now,
                        gid,
                    ),
                )
                conn.execute("DELETE FROM file_group_member WHERE group_id = ?", (gid,))
            else:
                gid = new_id()
                conn.execute(
                    "INSERT INTO file_group"
                    " (id, member_count, min_distance, has_identical, status, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        gid,
                        len(ng.members),
                        ng.min_distance,
                        1 if ng.has_identical else 0,
                        ng.status,
                        now,
                    ),
                )
            conn.executemany(
                "INSERT INTO file_group_member (group_id, file_id, decision) VALUES (?, ?, ?)",
                [(gid, f, ng.decisions.get(f, "undecided")) for f in sorted(ng.members)],
            )
            created.append(gid)
    return created


def set_decisions(conn: sqlite3.Connection, group_id: bytes, decisions: dict[bytes, str]) -> None:
    with conn:
        conn.executemany(
            "UPDATE file_group_member SET decision = ? WHERE group_id = ? AND file_id = ?",
            [(d, group_id, f) for f, d in decisions.items()],
        )
        conn.execute("UPDATE file_group SET updated_at = ? WHERE id = ?", (now_iso(), group_id))


def set_status(conn: sqlite3.Connection, group_id: bytes, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with conn:
        conn.execute(
            "UPDATE file_group SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), group_id),
        )


def list_groups(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    folder_id: bytes | None = None,
    has_identical: bool | None = None,
    min_size: int | None = None,
    min_distance_gt: float | None = None,
    min_distance_le: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[sqlite3.Row]:
    where, params = [], []
    if min_distance_gt is not None:
        where.append("g.min_distance > ?")
        params.append(min_distance_gt)
    if min_distance_le is not None:
        where.append("g.min_distance <= ?")
        params.append(min_distance_le)
    if status is not None:
        where.append("g.status = ?")
        params.append(status)
    if has_identical is not None:
        where.append("g.has_identical = ?")
        params.append(1 if has_identical else 0)
    if min_size is not None:
        where.append("g.member_count >= ?")
        params.append(min_size)
    if folder_id is not None:
        where.append(
            "EXISTS (SELECT 1 FROM file_group_member m JOIN file f ON f.id = m.file_id"
            " WHERE m.group_id = g.id AND f.folder_id = ?)"
        )
        params.append(folder_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return conn.execute(
        f"SELECT g.* FROM file_group g {clause}"
        " ORDER BY g.updated_at DESC, g.rowid DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM file_group GROUP BY status"):
        counts[row["status"]] = row["n"]
    return counts


def count_by_band(conn: sqlite3.Connection, near: float, variant: float) -> dict[str, int]:
    """Open groups per distance band (design §7.6)."""
    row = conn.execute(
        "SELECT"
        " SUM(has_identical = 1) AS identical,"
        " SUM(has_identical = 0 AND min_distance <= ?) AS near_duplicate,"
        " SUM(has_identical = 0 AND min_distance > ? AND min_distance <= ?) AS variant,"
        " SUM(has_identical = 0 AND (min_distance > ? OR min_distance IS NULL)) AS candidate"
        " FROM file_group WHERE status = 'open'",
        (near, near, variant, variant),
    ).fetchone()
    return {k: int(row[k] or 0) for k in ("identical", "near_duplicate", "variant", "candidate")}


def group_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["id"] = id_to_str(row["id"])
    d["has_identical"] = bool(row["has_identical"])
    return d


def member_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["file_id"] = id_to_str(row["file_id"])
    d["folder_id"] = id_to_str(row["folder_id"])
    d["sha1"] = row["sha1"].hex() if row["sha1"] else None
    d["relative_path"] = f"{row['directory']}/{row['name']}" if row["directory"] else row["name"]
    d["absolute_path"] = f"{row['folder_path']}/{d['relative_path']}"
    return d

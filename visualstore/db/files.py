"""File table queries (design §5.1 File)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from visualstore.clock import now_iso
from visualstore.ids import id_to_str, new_id

STATES = ("new", "fingerprinted", "unreadable", "not_media", "missing")


def insert_file(
    conn: sqlite3.Connection,
    *,
    folder_id: bytes,
    directory: str,
    name: str,
    extension_id: bytes,
    size: int,
    mtime: int,
    state: str = "new",
    sha1: bytes | None = None,
    file_id: bytes | None = None,
) -> bytes:
    file_id = file_id or new_id()
    with conn:
        conn.execute(
            "INSERT INTO file (id, folder_id, directory, name, extension_id, size, mtime, sha1,"
            " state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                file_id,
                folder_id,
                directory,
                name,
                extension_id,
                size,
                mtime,
                sha1,
                state,
                now_iso(),
            ),
        )
    return file_id


def get_file(conn: sqlite3.Connection, file_id: bytes) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM file WHERE id = ?", (file_id,)).fetchone()


def find_file(
    conn: sqlite3.Connection, folder_id: bytes, directory: str, name: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM file WHERE folder_id = ? AND directory = ? AND name = ?",
        (folder_id, directory, name),
    ).fetchone()


def list_files_in_folder(conn: sqlite3.Connection, folder_id: bytes) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM file WHERE folder_id = ? ORDER BY directory, name", (folder_id,)
    ).fetchall()


def update_path(conn: sqlite3.Connection, file_id: bytes, directory: str, name: str) -> None:
    with conn:
        conn.execute(
            "UPDATE file SET directory = ?, name = ? WHERE id = ?", (directory, name, file_id)
        )


def update_stat(conn: sqlite3.Connection, file_id: bytes, size: int, mtime: int) -> None:
    with conn:
        conn.execute("UPDATE file SET size = ?, mtime = ? WHERE id = ?", (size, mtime, file_id))


def reset_for_recompute(conn: sqlite3.Connection, file_id: bytes) -> None:
    """Content changed: forget the hash and the similarity pairs; keep the id and judgments."""
    with conn:
        conn.execute("UPDATE file SET state = 'new', sha1 = NULL WHERE id = ?", (file_id,))
        conn.execute(
            "DELETE FROM pair WHERE source IN ('sha1', 'vector') AND (file_a = ? OR file_b = ?)",
            (file_id, file_id),
        )
        conn.execute("INSERT OR IGNORE INTO regroup_queue (file_id) VALUES (?)", (file_id,))


def set_missing(conn: sqlite3.Connection, file_ids: Iterable[bytes]) -> None:
    with conn:
        conn.executemany("UPDATE file SET state = 'missing' WHERE id = ?", [(f,) for f in file_ids])


def delete_files(conn: sqlite3.Connection, file_ids: Iterable[bytes]) -> None:
    ids = list(file_ids)
    with conn:
        # the other members of a deleted file's group must be regrouped
        conn.executemany(
            "INSERT OR IGNORE INTO regroup_queue (file_id)"
            " SELECT m2.file_id FROM file_group_member m1 JOIN file_group_member m2"
            " ON m1.group_id = m2.group_id WHERE m1.file_id = ? AND m2.file_id != ?",
            [(f, f) for f in ids],
        )
        conn.executemany("DELETE FROM file WHERE id = ?", [(f,) for f in ids])


def set_fingerprint_result(
    conn: sqlite3.Connection, file_id: bytes, *, sha1: bytes | None, state: str
) -> None:
    with conn:
        conn.execute("UPDATE file SET sha1 = ?, state = ? WHERE id = ?", (sha1, state, file_id))


def files_by_sha1(conn: sqlite3.Connection, sha1: bytes) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM file WHERE sha1 = ? ORDER BY created_at, rowid", (sha1,)
    ).fetchall()


def files_by_state(
    conn: sqlite3.Connection, state: str, folder_id: bytes | None = None, limit: int = 1000
) -> list[sqlite3.Row]:
    if folder_id is None:
        return conn.execute(
            "SELECT * FROM file WHERE state = ? ORDER BY folder_id, directory, name LIMIT ?",
            (state, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM file WHERE state = ? AND folder_id = ? ORDER BY directory, name LIMIT ?",
        (state, folder_id, limit),
    ).fetchall()


def count_by_folder_and_state(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        "SELECT folder_id, state, COUNT(*) AS n FROM file GROUP BY folder_id, state"
    ):
        out.setdefault(id_to_str(row["folder_id"]), {})[row["state"]] = row["n"]
    return out


def relative_path(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def file_to_dict(row: sqlite3.Row, folder_path: str) -> dict:
    d = dict(row)
    d["id"] = id_to_str(row["id"])
    d["folder_id"] = id_to_str(row["folder_id"])
    d["extension_id"] = id_to_str(row["extension_id"])
    d["sha1"] = row["sha1"].hex() if row["sha1"] else None
    d["relative_path"] = relative_path(row["directory"], row["name"])
    d["absolute_path"] = f"{folder_path.rstrip('/')}/{d['relative_path']}"
    return d

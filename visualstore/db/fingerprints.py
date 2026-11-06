"""Vector storage and the usearch key map, all in vectors.db (design §5.1 Fingerprint, §5.2)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator


def replace_frames(
    vconn: sqlite3.Connection, file_id: bytes, model_id: str, frames: list[tuple[int, int, bytes]]
) -> None:
    with vconn:
        vconn.execute(
            "DELETE FROM fingerprint WHERE file_id = ? AND model_id = ?", (file_id, model_id)
        )
        vconn.executemany(
            "INSERT INTO fingerprint (file_id, model_id, frame_index, frame_time_ms, vector)"
            " VALUES (?, ?, ?, ?, ?)",
            [(file_id, model_id, idx, ms, vec) for idx, ms, vec in frames],
        )


def frames_of_file(vconn: sqlite3.Connection, file_id: bytes, model_id: str) -> list[sqlite3.Row]:
    return vconn.execute(
        "SELECT * FROM fingerprint WHERE file_id = ? AND model_id = ? ORDER BY frame_index",
        (file_id, model_id),
    ).fetchall()


def vector_count(vconn: sqlite3.Connection, model_id: str) -> int:
    return vconn.execute(
        "SELECT COUNT(*) FROM fingerprint WHERE model_id = ?", (model_id,)
    ).fetchone()[0]


def files_with_vectors(vconn: sqlite3.Connection, model_id: str) -> set[bytes]:
    rows = vconn.execute(
        "SELECT DISTINCT file_id FROM fingerprint WHERE model_id = ?", (model_id,)
    ).fetchall()
    return {bytes(r["file_id"]) for r in rows}


def delete_for_files(vconn: sqlite3.Connection, file_ids: Iterable[bytes]) -> None:
    ids = [(f,) for f in file_ids]
    with vconn:
        vconn.executemany("DELETE FROM fingerprint WHERE file_id = ?", ids)
        vconn.executemany("UPDATE index_key SET deleted = 1 WHERE file_id = ?", ids)


def allocate_keys(
    vconn: sqlite3.Connection, file_id: bytes, model_id: str, frame_indices: list[int]
) -> list[int]:
    with vconn:
        vconn.executemany(
            "INSERT OR IGNORE INTO index_key (file_id, model_id, frame_index) VALUES (?, ?, ?)",
            [(file_id, model_id, i) for i in frame_indices],
        )
        vconn.executemany(
            "UPDATE index_key SET deleted = 0"
            " WHERE file_id = ? AND model_id = ? AND frame_index = ? AND deleted = 1",
            [(file_id, model_id, i) for i in frame_indices],
        )
        marks = ",".join("?" * len(frame_indices)) or "NULL"
        vconn.execute(  # frames that vanished (fewer frames this time) leave the index
            "UPDATE index_key SET deleted = 1 WHERE file_id = ? AND model_id = ?"
            f" AND frame_index NOT IN ({marks})",
            (file_id, model_id, *frame_indices),
        )
    rows = vconn.execute(
        "SELECT seq, frame_index FROM index_key WHERE file_id = ? AND model_id = ?",
        (file_id, model_id),
    ).fetchall()
    by_frame = {r["frame_index"]: r["seq"] for r in rows}
    return [by_frame[i] for i in frame_indices]


def resolve_keys(vconn: sqlite3.Connection, seqs: Iterable[int]) -> dict[int, tuple[bytes, int]]:
    seqs = list(seqs)
    out: dict[int, tuple[bytes, int]] = {}
    for start in range(0, len(seqs), 900):  # stay under SQLite's variable limit
        chunk = seqs[start : start + 900]
        marks = ",".join("?" * len(chunk))
        for r in vconn.execute(
            "SELECT seq, file_id, frame_index FROM index_key"
            f" WHERE deleted = 0 AND seq IN ({marks})",
            chunk,
        ):
            out[r["seq"]] = (bytes(r["file_id"]), r["frame_index"])
    return out


def pending_removals(vconn: sqlite3.Connection, model_id: str) -> list[int]:
    rows = vconn.execute(
        "SELECT seq FROM index_key WHERE deleted = 1 AND model_id = ? ORDER BY seq", (model_id,)
    ).fetchall()
    return [r["seq"] for r in rows]


def drop_keys(vconn: sqlite3.Connection, seqs: Iterable[int]) -> None:
    with vconn:
        vconn.executemany("DELETE FROM index_key WHERE seq = ?", [(s,) for s in seqs])


def get_meta(vconn: sqlite3.Connection, key: str) -> str | None:
    row = vconn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(vconn: sqlite3.Connection, key: str, value: str) -> None:
    with vconn:
        vconn.execute(
            "INSERT INTO index_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def iter_vectors(
    vconn: sqlite3.Connection, model_id: str, batch: int = 4096, after: int = -1
) -> Iterator[list[sqlite3.Row]]:
    """Live (key, vector) rows for one model with seq > `after`, in key order."""
    last = after
    while True:
        rows = vconn.execute(
            "SELECT k.seq, k.file_id, k.frame_index, f.vector FROM index_key k"
            " JOIN fingerprint f ON f.file_id = k.file_id AND f.model_id = k.model_id"
            " AND f.frame_index = k.frame_index"
            " WHERE k.model_id = ? AND k.deleted = 0 AND k.seq > ? ORDER BY k.seq LIMIT ?",
            (model_id, last, batch),
        ).fetchall()
        if not rows:
            return
        yield rows
        last = rows[-1]["seq"]

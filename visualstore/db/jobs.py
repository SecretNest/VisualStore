"""Query functions for the job table (design §5.1 Job, §6.1)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from visualstore.clock import now_iso
from visualstore.ids import id_to_str, new_id

MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = 30  # a re-queued attempt waits this × attempts (ADR-0008)
STATES = ("queued", "running", "done", "failed", "cancelled")


def enqueue(
    conn: sqlite3.Connection,
    *,
    type: str,
    kind: str,
    params: dict | None = None,
    priority: int = 0,
) -> bytes:
    job_id = new_id()
    with conn:
        conn.execute(
            "INSERT INTO job (id, type, params, state, priority, kind, created_at)"
            " VALUES (?, ?, ?, 'queued', ?, ?, ?)",
            (job_id, type, json.dumps(params or {}), priority, kind, now_iso()),
        )
    return job_id


def update_params(
    conn: sqlite3.Connection, job_id: bytes, params: dict, priority: int | None = None
) -> None:
    with conn:
        if priority is None:
            conn.execute("UPDATE job SET params = ? WHERE id = ?", (json.dumps(params), job_id))
        else:
            conn.execute(
                "UPDATE job SET params = ?, priority = ? WHERE id = ?",
                (json.dumps(params), priority, job_id),
            )


def prune_finished(conn: sqlite3.Connection, done_before: str, failed_before: str) -> int:
    """Forget done/cancelled jobs finished before `done_before` and failed ones before
    `failed_before` (ISO timestamps). The job table is a queue, not an archive (audit B1)."""
    with conn:
        a = conn.execute(
            "DELETE FROM job WHERE state IN ('done', 'cancelled') AND finished_at < ?",
            (done_before,),
        ).rowcount
        b = conn.execute(
            "DELETE FROM job WHERE state = 'failed' AND finished_at < ?", (failed_before,)
        ).rowcount
    return a + b


def get(conn: sqlite3.Connection, job_id: bytes) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()


def list_jobs(
    conn: sqlite3.Connection, state: str | None = None, limit: int = 100
) -> list[sqlite3.Row]:
    if state is None:
        return conn.execute(
            "SELECT * FROM job ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM job WHERE state = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (state, limit),
    ).fetchall()


def count_by_state(conn: sqlite3.Connection) -> dict[str, int]:
    counts = dict.fromkeys(STATES, 0)
    for row in conn.execute("SELECT state, COUNT(*) AS n FROM job GROUP BY state"):
        counts[row["state"]] = row["n"]
    return counts


def next_queued(
    conn: sqlite3.Connection,
    kind: str,
    exclude_types: tuple[str, ...] = (),
    now: str | None = None,
) -> sqlite3.Row | None:
    clause = ""
    params: list = [kind, now or now_iso()]
    if exclude_types:
        clause = f" AND type NOT IN ({','.join('?' * len(exclude_types))})"
        params.extend(exclude_types)
    return conn.execute(
        f"SELECT * FROM job WHERE state = 'queued' AND kind = ?"
        f" AND (not_before IS NULL OR not_before <= ?){clause}"
        " ORDER BY priority DESC, created_at ASC, rowid ASC LIMIT 1",
        params,
    ).fetchone()


def latest_of_type(conn: sqlite3.Connection, type: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM job WHERE type = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (type,)
    ).fetchone()


def latest_with_param(conn: sqlite3.Connection, type: str, key: str, value) -> sqlite3.Row | None:
    """Newest job of a type whose params JSON has key == value (key is a code literal)."""
    return conn.execute(
        f"SELECT * FROM job WHERE type = ? AND json_extract(params, '$.{key}') = ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (type, value),
    ).fetchone()


def has_active_with_param(conn: sqlite3.Connection, type: str, key: str, value) -> bool:
    row = conn.execute(
        "SELECT 1 FROM job WHERE type = ? AND state IN ('queued', 'running')"
        f" AND json_extract(params, '$.{key}') = ? LIMIT 1",
        (type, value),
    ).fetchone()
    return row is not None


def active_jobs_for_files(conn: sqlite3.Connection, type: str) -> dict[str, bytes]:
    """file id (str) -> job id for every file named in a queued/running job of a type."""
    rows = conn.execute(
        "SELECT id, params FROM job WHERE type = ? AND state IN ('queued', 'running')", (type,)
    ).fetchall()
    out: dict[str, bytes] = {}
    for r in rows:
        for entry in (json.loads(r["params"]) or {}).get("files", []):
            if isinstance(entry, dict) and "id" in entry:
                out[str(entry["id"])] = bytes(r["id"])
    return out


def active_file_ids(conn: sqlite3.Connection, type: str) -> set[str]:
    """File ids named in the `files` list of queued/running jobs of a type."""
    return set(active_jobs_for_files(conn, type))


def raise_priority(conn: sqlite3.Connection, job_ids: Iterable[bytes], priority: int) -> int:
    """A manual scan jumps the queue: queued jobs below `priority` are lifted to it."""
    with conn:
        cur = conn.executemany(
            "UPDATE job SET priority = ? WHERE id = ? AND state = 'queued' AND priority < ?",
            [(priority, j, priority) for j in set(job_ids)],
        )
    return cur.rowcount if cur.rowcount >= 0 else 0


def has_active(conn: sqlite3.Connection, type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM job WHERE type = ? AND state IN ('queued', 'running') LIMIT 1", (type,)
    ).fetchone()
    return row is not None


def mark_running(conn: sqlite3.Connection, job_id: bytes) -> None:
    """The previous attempt's error is kept so a retrying job still shows why it failed."""
    with conn:
        conn.execute(
            "UPDATE job SET state = 'running', started_at = ? WHERE id = ?", (now_iso(), job_id)
        )


def set_progress(conn: sqlite3.Connection, job_id: bytes, progress: dict) -> None:
    with conn:
        conn.execute("UPDATE job SET progress = ? WHERE id = ?", (json.dumps(progress), job_id))


def mark_done(conn: sqlite3.Connection, job_id: bytes) -> None:
    with conn:
        conn.execute(
            "UPDATE job SET state = 'done', finished_at = ? WHERE id = ?", (now_iso(), job_id)
        )


def mark_failed(conn: sqlite3.Connection, job_id: bytes, error: str) -> None:
    """Final failure with no retry, for errors that cannot be fixed by running again."""
    with conn:
        conn.execute(
            "UPDATE job SET state = 'failed', error = ?, finished_at = ? WHERE id = ?",
            (error, now_iso(), job_id),
        )


def record_failure(
    conn: sqlite3.Connection, job_id: bytes, error: str, backoff: bool = True
) -> str:
    """Count one failed attempt. Returns the new state: 'queued' to retry, 'failed' when spent.
    A retried attempt waits RETRY_BACKOFF_S × attempts unless `backoff` is off."""
    row = get(conn, job_id)
    attempts = int(row["attempts"]) + 1
    state = "queued" if attempts < MAX_ATTEMPTS else "failed"
    finished = None if state == "queued" else now_iso()
    not_before = None
    if state == "queued" and backoff and RETRY_BACKOFF_S:
        delay = timedelta(seconds=RETRY_BACKOFF_S * attempts)
        not_before = (datetime.now(UTC) + delay).isoformat(timespec="seconds")
    with conn:
        conn.execute(
            "UPDATE job SET state = ?, attempts = ?, error = ?, finished_at = ?, not_before = ?"
            " WHERE id = ?",
            (state, attempts, error, finished, not_before, job_id),
        )
    return state


def requeue_interrupted(conn: sqlite3.Connection) -> int:
    """At start-up: every job left 'running' by the previous process was interrupted."""
    rows = conn.execute("SELECT id FROM job WHERE state = 'running'").fetchall()
    for row in rows:
        record_failure(conn, row["id"], "interrupted by restart", backoff=False)
    return len(rows)


def retry(conn: sqlite3.Connection, job_id: bytes) -> bool:
    with conn:
        cur = conn.execute(
            "UPDATE job SET state = 'queued', attempts = 0, error = NULL, not_before = NULL,"
            " started_at = NULL, finished_at = NULL WHERE id = ? AND state = 'failed'",
            (job_id,),
        )
    return cur.rowcount == 1


def cancel(conn: sqlite3.Connection, job_id: bytes) -> bool:
    with conn:
        cur = conn.execute(
            "UPDATE job SET state = 'cancelled', finished_at = ? WHERE id = ? AND state = 'queued'",
            (now_iso(), job_id),
        )
    return cur.rowcount == 1


def to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["id"] = id_to_str(row["id"])
    d["params"] = json.loads(row["params"])
    d["progress"] = json.loads(row["progress"])
    return d

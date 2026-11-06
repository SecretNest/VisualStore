"""Result sinks: how a compute result becomes database rows.

Compute children return plain data (design §3). The scheduler calls the sink for the job's type
on the event-loop thread with the registry connection, so all database writes stay on one thread.
Milestone 2 registers `fingerprint`; milestone 3 adds `match`, `rebuild_index`, `eval`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

ResultSink = Callable[[sqlite3.Connection, str, dict, dict], None]

RESULT_SINKS: dict[str, ResultSink] = {}


def sink_fingerprint(conn: sqlite3.Connection, profile: str, params: dict, result: dict) -> None:
    from visualstore.db import files as files_db
    from visualstore.db import groups as groups_db
    from visualstore.db import jobs as jobs_db
    from visualstore.db import pairs as pairs_db
    from visualstore.ids import id_from_str
    from visualstore.jobs.types import PRIORITY_INBOX

    dirty = False
    for entry in result.get("files", []):
        try:
            file_id = id_from_str(entry["id"])
        except ValueError:
            continue
        if files_db.get_file(conn, file_id) is None:
            continue  # deleted or trashed since the job was queued
        sha1 = bytes.fromhex(entry["sha1"]) if entry.get("sha1") else None
        files_db.set_fingerprint_result(conn, file_id, sha1=sha1, state=entry["state"])
        if sha1 is None or entry["state"] != "fingerprinted":
            continue
        bucket = [r for r in files_db.files_by_sha1(conn, sha1) if r["state"] == "fingerprinted"]
        if len(bucket) < 2:
            continue
        hub = bytes(bucket[0]["id"])
        for row in bucket[1:]:
            pairs_db.upsert_sha1_pair(conn, hub, bytes(row["id"]))
        groups_db.mark_dirty(conn, [bytes(r["id"]) for r in bucket])
        dirty = True
    if dirty and not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)


RESULT_SINKS["fingerprint"] = sink_fingerprint

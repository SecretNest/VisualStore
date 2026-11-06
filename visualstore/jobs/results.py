"""Result sinks: how a compute result becomes database rows.

Compute children return plain data (design §3). The scheduler calls the sink for the job's type
on the event-loop thread with the registry connection, so all database writes stay on one thread.
Milestone 2 registers `fingerprint`; milestone 3 adds `match`, `rebuild_index`, `eval`.
"""

from __future__ import annotations

import base64
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from visualstore.profiles import ProfilePaths, ProfileRegistry


@dataclass
class SinkContext:
    conn: sqlite3.Connection  # profile metadata db (registry connection, loop thread)
    vconn: sqlite3.Connection  # profile vectors db
    registry: ProfileRegistry
    profile: str
    paths: ProfilePaths
    settings: dict
    model_dir: Path
    params: dict  # the job's params
    result: dict  # what the child returned


ResultSink = Callable[[SinkContext], None]

RESULT_SINKS: dict[str, ResultSink] = {}


def _stale(row, entry: dict) -> bool:
    """A result describes the file as it was when hashed; the row describes it now."""
    if entry.get("state") != "fingerprinted":
        return False
    if "size" in entry and entry["size"] != row["size"]:
        return True
    return "mtime" in entry and row["mtime"] != 0 and entry["mtime"] != row["mtime"]


def link_identical(conn: sqlite3.Connection, file_id: bytes, sha1: bytes) -> bool:
    """Connect a hashed file to the other fingerprinted files with the same sha1 (star from the
    earliest). Returns True when a bucket of two or more was marked dirty. Shared by the
    fingerprint sink and the trash-restore path."""
    from visualstore.db import files as files_db
    from visualstore.db import groups as groups_db
    from visualstore.db import pairs as pairs_db

    bucket = [r for r in files_db.files_by_sha1(conn, sha1) if r["state"] == "fingerprinted"]
    if len(bucket) < 2:
        return False
    hub = bytes(bucket[0]["id"])
    for r in bucket[1:]:
        pairs_db.upsert_sha1_pair(conn, hub, bytes(r["id"]))
    groups_db.mark_dirty(conn, [bytes(r["id"]) for r in bucket])
    return True


def sink_fingerprint(ctx: SinkContext) -> None:
    from visualstore.db import files as files_db
    from visualstore.db import fingerprints as fp
    from visualstore.db import jobs as jobs_db
    from visualstore.ids import id_from_str
    from visualstore.jobs.types import PRIORITY_INBOX

    conn, vconn = ctx.conn, ctx.vconn
    model = ctx.params.get("model")
    priority = int(ctx.params.get("priority", PRIORITY_INBOX))
    dirty = False
    entries_for_match: list[dict] = []
    for entry in ctx.result.get("files", []):
        try:
            file_id = id_from_str(entry["id"])
        except ValueError:
            continue
        row = files_db.get_file(conn, file_id)
        if row is None or _stale(row, entry):
            continue  # deleted/trashed since the job was queued, or content changed meanwhile
        sha1 = bytes.fromhex(entry["sha1"]) if entry.get("sha1") else None
        files_db.set_fingerprint_result(conn, file_id, sha1=sha1, state=entry["state"])
        if entry.get("frames") and model:
            frames = [
                (int(f["index"]), int(f.get("time_ms", 0)), base64.b64decode(f["vector"]))
                for f in entry["frames"]
            ]
            fp.replace_frames(vconn, file_id, model["id"], frames)
            seqs = fp.allocate_keys(vconn, file_id, model["id"], [f[0] for f in frames])
            entries_for_match.extend(
                {"seq": seq, "vector": f["vector"]}
                for seq, f in zip(seqs, entry["frames"], strict=True)
            )
        if entry.get("thumbnail"):
            text = entry["id"]
            path = ctx.paths.thumbs / text[:2] / f"{text}.webp"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(entry["thumbnail"]))
        if sha1 is None or entry["state"] != "fingerprinted":
            continue
        if link_identical(conn, file_id, sha1):
            dirty = True
    if entries_for_match and model:
        index_ok = ctx.paths.index.is_file() and fp.get_meta(vconn, "model_id") == model["id"]
        if index_ok:
            jobs_db.enqueue(
                conn,
                type="match",
                kind="compute",
                priority=priority,
                params={
                    "model": model,
                    "index_path": str(ctx.paths.index),
                    "entries": entries_for_match,
                    "remove": fp.pending_removals(vconn, model["id"]),
                    "top_k": int(ctx.settings["similarity.top_k"]),
                    "candidate_distance": float(ctx.settings["similarity.candidate_distance"]),
                    "priority": priority,
                },
            )
        elif not (
            jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(conn, "build_index")
        ):
            # No usable index yet (or built for another model): rebuild from vectors.db, which
            # now includes the vectors just written (design §7.4, Review Focus 1).
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=priority)
    if dirty and not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)


RESULT_SINKS["fingerprint"] = sink_fingerprint

"""Result sinks: how a compute result becomes database rows.

Compute children return plain data (design §3). The scheduler calls the sink for the job's type
on the event-loop thread with the registry connection, so all database writes stay on one thread.
Milestone 2 registers `fingerprint`; milestone 3 adds `match`, `rebuild_index`, `eval`.
"""

from __future__ import annotations

import base64
import json
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


MATCH_BATCH = 64
MATCH_COALESCE_MAX = 4096  # entries one queued match job may accumulate (audit B10)


def compact_result(job_type: str, result: dict) -> dict:
    """What of a compute result is worth keeping in the job table once the sink has consumed
    it: counts, never vectors, thumbnails or hit lists (audit B1)."""
    if job_type == "fingerprint":
        files = result.get("files", [])
        by_state: dict[str, int] = {}
        for f in files:
            by_state[f.get("state", "?")] = by_state.get(f.get("state", "?"), 0) + 1
        return {
            "files": len(files),
            "by_state": by_state,
            "frames": sum(len(f.get("frames", [])) for f in files),
        }
    if job_type == "match":
        out = {k: v for k, v in result.items() if k != "hits"}
        out["hits"] = len(result.get("hits", []))
        return out
    return result


def compact_params(job_type: str, params: dict) -> dict:
    """Params worth keeping once a compute job is done (the vectors of a match job are not)."""
    if job_type == "match" and isinstance(params.get("entries"), list):
        return {
            **params,
            "entries": len(params["entries"]),
            "remove": len(params.get("remove", [])),
        }
    return params


def _options(quantization: str, dims: int, paths, model_dim: int) -> dict:
    return {
        "quantization": quantization,
        "pca_dims": dims,
        "pca_path": str(paths.root / "pca.npz") if dims else None,
        "index_dim": dims or model_dim,
    }


def index_options(settings: dict, paths, model_dim: int, vector_count: int | None = None) -> dict:
    """The index the settings ask for (ADR-0008). With `vector_count`, PCA is dropped when the
    profile has fewer vectors than dimensions: a projection cannot be fitted, so the index is
    built unreduced rather than failing forever."""
    dims = int(settings.get("index.pca_dims", 0))
    if vector_count is not None and dims and vector_count < dims:
        dims = 0
    return _options(str(settings.get("index.quantization", "f16")), dims, paths, model_dim)


def index_options_on_disk(vconn, paths, model_dim: int) -> dict:
    """The index as it was last built (index_meta): what a `match` job must open."""
    from visualstore.db import fingerprints as fp

    quantization = fp.get_meta(vconn, "quantization") or "f16"
    dims = int(fp.get_meta(vconn, "pca_dims") or 0)
    return _options(quantization, dims, paths, model_dim)


def rebuild_active(conn) -> bool:
    from visualstore.db import jobs as jobs_db

    return jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(conn, "build_index")


def enqueue_match(conn, vconn, settings: dict, paths, model: dict, entries, priority: int) -> int:
    """Queue one `match` job per MATCH_BATCH entries ({"seq", "vector"}); returns the job count."""
    from visualstore.db import fingerprints as fp
    from visualstore.db import jobs as jobs_db

    entries = list(entries)
    made = 0
    options = index_options_on_disk(vconn, paths, int(model["dim"]))
    # A queued (not yet running) match job for the same index absorbs new entries, so a backlog
    # of fingerprint batches rewrites the index once, not once per 64 vectors (audit B10).
    row = conn.execute(
        "SELECT id, params, priority FROM job WHERE type = 'match' AND state = 'queued'"
        " ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        params = json.loads(row["params"])
        same = params.get("index") == options and params.get("model", {}).get("id") == model["id"]
        if same and len(params.get("entries", [])) + len(entries) <= MATCH_COALESCE_MAX:
            params["entries"] = params.get("entries", []) + entries
            params["remove"] = sorted(
                set(params.get("remove", [])) | set(fp.pending_removals(vconn, model["id"]))
            )
            jobs_db.update_params(
                conn, bytes(row["id"]), params, max(int(row["priority"]), priority)
            )
            return 0
    for start in range(0, len(entries), MATCH_BATCH):
        jobs_db.enqueue(
            conn,
            type="match",
            kind="compute",
            priority=priority,
            params={
                "model": model,
                "index_path": str(paths.index),
                "index": options,
                "entries": entries[start : start + MATCH_BATCH],
                "remove": fp.pending_removals(vconn, model["id"]) if start == 0 else [],
                "top_k": int(settings["similarity.top_k"]),
                "candidate_distance": float(settings["similarity.candidate_distance"]),
                "priority": priority,
            },
        )
        made += 1
    return made


def match_unindexed(conn, vconn, settings: dict, paths, model_dir, after_seq: int, priority) -> int:
    """Queue `match` jobs for every live key above `after_seq`: vectors that were written while an
    index was being rebuilt are not in the snapshot the build used (Review Focus 1)."""
    from visualstore.db import fingerprints as fp
    from visualstore.models.registry import get_spec, job_params

    spec = get_spec(settings["model.id"], model_dir)
    model = job_params(spec, model_dir)
    entries = [
        {"seq": r["seq"], "vector": base64.b64encode(bytes(r["vector"])).decode()}
        for rows in fp.iter_vectors(vconn, spec.id, after=after_seq)
        for r in rows
    ]
    return enqueue_match(conn, vconn, settings, paths, model, entries, priority)


def sink_fingerprint(ctx: SinkContext) -> None:
    from visualstore.db import files as files_db
    from visualstore.db import fingerprints as fp
    from visualstore.db import jobs as jobs_db
    from visualstore.ids import id_from_str
    from visualstore.jobs.types import PRIORITY_INBOX

    conn, vconn = ctx.conn, ctx.vconn
    model = ctx.params.get("model")
    # Vectors of a model that is no longer active (job queued before a switch) are discarded;
    # the hash and the thumbnail are still valid (design §7.7).
    if model and model.get("id") != ctx.settings.get("model.id"):
        model = None
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
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(base64.b64decode(entry["thumbnail"]))
            tmp.replace(path)  # never serve a half-written thumbnail
            (ctx.paths.previews / text[:2] / f"{text}.webp").unlink(missing_ok=True)
            for stale in (ctx.paths.previews / "frames" / text[:2]).glob(f"{text}-*.webp"):
                stale.unlink(missing_ok=True)
        if sha1 is None or entry["state"] != "fingerprinted":
            continue
        if link_identical(conn, file_id, sha1):
            dirty = True
    if entries_for_match and model:
        # While a rebuild is active the index on disk is about to be replaced: queue nothing,
        # the build sink matches every vector written since its snapshot (Review Focus 1).
        active = rebuild_active(conn)
        index_ok = (
            ctx.paths.index.is_file()
            and fp.get_meta(vconn, "model_id") == model["id"]
            and not active
        )
        if index_ok:
            enqueue_match(conn, vconn, ctx.settings, ctx.paths, model, entries_for_match, priority)
        elif not active:
            # No usable index yet (or built for another model): rebuild from vectors.db, which
            # now includes the vectors just written (design §7.4, Review Focus 1). While a
            # rebuild is active, nothing is queued: sink_build_index matches what arrived late.
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=priority)
    if dirty and not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)


RESULT_SINKS["fingerprint"] = sink_fingerprint


def sink_match(ctx: SinkContext) -> None:
    from visualstore.db import fingerprints as fp
    from visualstore.db import groups as groups_db
    from visualstore.db import jobs as jobs_db
    from visualstore.db import pairs as pairs_db
    from visualstore.domain.scope import ScopeInfo, allowed
    from visualstore.jobs.types import PRIORITY_INBOX

    conn, vconn = ctx.conn, ctx.vconn
    model = ctx.params["model"]
    if model["id"] != ctx.settings.get("model.id"):
        return  # queued before a model switch: distances of two models are never mixed
    hits = ctx.result.get("hits", [])
    seqs = {int(h[0]) for h in hits} | {int(h[1]) for h in hits}
    owner = fp.resolve_keys(vconn, seqs)
    best: dict[tuple[bytes, bytes], float] = {}
    for q, h, distance in hits:
        a = owner.get(int(q))
        b = owner.get(int(h))
        if a is None or b is None or a[0] == b[0]:
            continue
        key = tuple(sorted((a[0], b[0])))
        if key not in best or distance < best[key]:
            best[key] = float(distance)
    involved = {f for pair in best for f in pair}
    scopes: dict[bytes, ScopeInfo] = {}
    if involved:
        marks = ",".join("?" * len(involved))
        rows = conn.execute(
            "SELECT f.id, f.folder_id, f.directory, fo.compare_scope FROM file f"
            f" JOIN folder fo ON fo.id = f.folder_id WHERE f.id IN ({marks})",
            list(involved),
        )
        for r in rows:
            scopes[bytes(r["id"])] = ScopeInfo(
                bytes(r["folder_id"]), r["directory"], r["compare_scope"]
            )
    dirty: set[bytes] = set()
    for (a, b), distance in best.items():
        if a not in scopes or b not in scopes or not allowed(scopes[a], scopes[b]):
            continue
        pairs_db.upsert_vector_pair(conn, a, b, distance, model["id"])
        dirty.update((a, b))
    if dirty:
        groups_db.mark_dirty(conn, dirty)
        if not jobs_db.has_active(conn, "regroup"):
            jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
    removed = [int(s) for s in ctx.params.get("remove", [])]
    if removed:
        fp.drop_keys(vconn, removed)


RESULT_SINKS["match"] = sink_match


def sink_build_index(ctx: SinkContext) -> None:
    from visualstore.clock import now_iso
    from visualstore.db import fingerprints as fp
    from visualstore.db import jobs as jobs_db
    from visualstore.jobs.types import PRIORITY_INBOX

    fp.set_meta(ctx.vconn, "model_id", ctx.result["model_id"])
    fp.set_meta(ctx.vconn, "dim", str(ctx.result["dim"]))
    fp.set_meta(ctx.vconn, "built_at", now_iso())
    options = ctx.params.get("index") or {}
    fp.set_meta(ctx.vconn, "quantization", str(options.get("quantization", "f16")))
    fp.set_meta(ctx.vconn, "pca_dims", str(int(options.get("pca_dims", 0))))
    if ctx.result["model_id"] != ctx.settings.get("model.id"):
        return  # the model changed again meanwhile; the switch queued its own rebuild
    wanted = index_options(
        ctx.settings,
        ctx.paths,
        int(ctx.result["dim"]),
        fp.vector_count(ctx.vconn, ctx.result["model_id"]),
    )
    built = (str(options.get("quantization", "f16")), int(options.get("pca_dims", 0)))
    if (wanted["quantization"], wanted["pca_dims"]) != built:
        # the settings changed while the child was building: build again (final review I1)
        if not jobs_db.has_active(ctx.conn, "rebuild_index"):
            jobs_db.enqueue(ctx.conn, type="rebuild_index", kind="io", priority=PRIORITY_INBOX)
        return
    match_unindexed(
        ctx.conn,
        ctx.vconn,
        ctx.settings,
        ctx.paths,
        ctx.model_dir,
        int(ctx.params.get("max_seq", -1)),
        int(ctx.params.get("priority", PRIORITY_INBOX)),
    )


RESULT_SINKS["build_index"] = sink_build_index

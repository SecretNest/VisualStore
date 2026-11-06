"""Start-up consistency checks (design §7.4: rebuild when the index is missing or stale)."""

from __future__ import annotations

import logging
from pathlib import Path

from visualstore.db import fingerprints as fp
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.index.usearch_index import IndexError_, inspect
from visualstore.jobs.results import index_options
from visualstore.jobs.types import PRIORITY_INBOX
from visualstore.models.registry import ModelError, get_spec
from visualstore.profiles import ProfileRegistry

log = logging.getLogger(__name__)


def index_is_stale(registry: ProfileRegistry, name: str, model_dir: Path | None = None) -> bool:
    conn, vconn = registry.conn(name), registry.vectors_conn(name)
    try:
        spec = get_spec(settings_db.get(conn, "model.id"), model_dir)
    except ModelError:
        return False
    if fp.vector_count(vconn, spec.id) == 0:
        return False
    if fp.get_meta(vconn, "model_id") != spec.id:
        return True
    wanted = index_options(settings_db.all(conn), registry.paths(name), spec.dim)
    if (fp.get_meta(vconn, "quantization") or "f16") != wanted["quantization"]:
        return True
    if int(fp.get_meta(vconn, "pca_dims") or 0) != wanted["pca_dims"]:
        return True
    try:
        meta = inspect(registry.paths(name).index)
    except IndexError_:
        return True
    return (
        meta is None
        or meta["dimensions"] != wanted["index_dim"]
        or meta["dtype"] != wanted["quantization"]
    )


def check_indexes(registry: ProfileRegistry, model_dir: Path | None = None) -> int:
    enqueued = 0
    for name in registry.names():
        conn = registry.conn(name)
        if jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(conn, "build_index"):
            continue
        if index_is_stale(registry, name, model_dir):
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=PRIORITY_INBOX)
            enqueued += 1
    return enqueued


def check_consistency(registry: ProfileRegistry, model_dir: Path | None = None) -> dict:
    """Heal the two databases after a restore (design §9.1): frames whose file row is gone
    are removed and their keys retired (then the index is rebuilt); fingerprinted media files
    without vectors for the active model get fingerprint jobs."""
    from visualstore.jobs.io.model_switch import queue_missing_vectors

    totals = {"unknown_file_frames": 0, "files_without_vectors": 0, "rebuilds": 0}
    for name in registry.names():
        conn, vconn = registry.conn(name), registry.vectors_conn(name)
        known = {bytes(r["id"]) for r in conn.execute("SELECT id FROM file")}
        unknown = fp.file_ids_with_frames(vconn) - known
        if unknown:
            totals["unknown_file_frames"] += fp.count_frames_of(vconn, unknown)
            fp.delete_for_files(vconn, unknown)
            active = jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(
                conn, "build_index"
            )
            if not active:
                jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=PRIORITY_INBOX)
                totals["rebuilds"] += 1
        try:
            files_queued, _ = queue_missing_vectors(conn, vconn, settings_db.all(conn), model_dir)
        except ModelError:
            files_queued = 0  # no usable model configured; nothing to queue
        totals["files_without_vectors"] += files_queued
        if unknown or files_queued:
            log.info(
                "profile %s: %d frames of unknown files removed, %d files queued for vectors",
                name,
                len(unknown),
                files_queued,
            )
    return totals

"""Start-up consistency checks (design §7.4: rebuild when the index is missing or stale)."""

from __future__ import annotations

from visualstore.db import fingerprints as fp
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.index.usearch_index import IndexError_, inspect
from visualstore.jobs.types import PRIORITY_INBOX
from visualstore.models.registry import ModelError, get_spec
from visualstore.profiles import ProfileRegistry


def index_is_stale(registry: ProfileRegistry, name: str) -> bool:
    conn, vconn = registry.conn(name), registry.vectors_conn(name)
    try:
        spec = get_spec(settings_db.get(conn, "model.id"))
    except ModelError:
        return False
    if fp.vector_count(vconn, spec.id) == 0:
        return False
    if fp.get_meta(vconn, "model_id") != spec.id:
        return True
    try:
        meta = inspect(registry.paths(name).index)
    except IndexError_:
        return True
    return meta is None or meta["dimensions"] != spec.dim


def check_indexes(registry: ProfileRegistry) -> int:
    enqueued = 0
    for name in registry.names():
        conn = registry.conn(name)
        if jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(conn, "build_index"):
            continue
        if index_is_stale(registry, name):
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=PRIORITY_INBOX)
            enqueued += 1
    return enqueued

"""Index rebuild (design §7.4 last paragraph, §7.7): export live vectors, let a child build."""

from __future__ import annotations

from contextlib import closing

import numpy as np

from visualstore.clock import now_iso
from visualstore.db import fingerprints as fp
from visualstore.db import jobs as jobs_db
from visualstore.index.usearch_index import VectorIndex
from visualstore.jobs.results import match_unindexed
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX
from visualstore.models.registry import get_spec

EXPORT_NAME = "index.export.npz"


def run_rebuild_index(ctx: IoContext) -> dict:
    spec = get_spec(ctx.settings["model.id"], ctx.model_dir)
    export = ctx.paths.root / EXPORT_NAME
    with closing(ctx.open_vectors()) as vconn, closing(ctx.open_metadata()) as conn:
        keys: list[int] = []
        chunks: list[np.ndarray] = []
        for rows in fp.iter_vectors(vconn, spec.id):
            keys.extend(r["seq"] for r in rows)
            chunks.append(np.array([np.frombuffer(r["vector"], dtype=np.float16) for r in rows]))
            ctx.report({"done": len(keys), "total": 0, "message": f"exported {len(keys)} vectors"})
        priority = int(ctx.params.get("priority", PRIORITY_INBOX))
        if not keys:
            VectorIndex.empty(spec.dim).save(ctx.paths.index)
            fp.set_meta(vconn, "model_id", spec.id)
            fp.set_meta(vconn, "dim", str(spec.dim))
            fp.set_meta(vconn, "built_at", now_iso())
            matched = match_unindexed(
                conn, vconn, ctx.settings, ctx.paths, ctx.model_dir, -1, priority
            )
            return {"exported": 0, "built": True, "match_jobs": matched}
        vectors = np.concatenate(chunks).reshape(len(keys), spec.dim)
        np.savez(export, keys=np.asarray(keys, dtype=np.uint64), vectors=vectors)
        jobs_db.enqueue(
            conn,
            type="build_index",
            kind="compute",
            priority=priority,
            params={
                "index_path": str(ctx.paths.index),
                "export_path": str(export),
                "dim": spec.dim,
                "model_id": spec.id,
                "max_seq": max(keys),  # keys above this were written after the snapshot
                "priority": priority,
            },
        )
        return {"exported": len(keys), "built": False}

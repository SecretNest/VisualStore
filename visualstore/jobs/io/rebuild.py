"""Index rebuild (design §7.4 last paragraph, §7.7): export live vectors, let a child build."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import numpy as np

from visualstore.clock import now_iso
from visualstore.db import fingerprints as fp
from visualstore.db import jobs as jobs_db
from visualstore.index import pca
from visualstore.index.usearch_index import VectorIndex
from visualstore.jobs.results import index_options, match_unindexed
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
        options = index_options(ctx.settings, ctx.paths, spec.dim)
        if not keys:
            VectorIndex.empty(options["index_dim"], options["quantization"]).save(ctx.paths.index)
            fp.set_meta(vconn, "model_id", spec.id)
            fp.set_meta(vconn, "dim", str(spec.dim))
            fp.set_meta(vconn, "built_at", now_iso())
            fp.set_meta(vconn, "quantization", options["quantization"])
            fp.set_meta(vconn, "pca_dims", str(options["pca_dims"]))
            matched = match_unindexed(
                conn, vconn, ctx.settings, ctx.paths, ctx.model_dir, -1, priority
            )
            return {"exported": 0, "built": True, "match_jobs": matched}
        vectors = np.concatenate(chunks).reshape(len(keys), spec.dim)
        if options["pca_dims"]:  # fitted on this profile's own vectors (ADR-0008)
            pca.save(
                pca.fit(vectors.astype(np.float32), options["pca_dims"]), Path(options["pca_path"])
            )
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
                "index": options,
                "max_seq": max(keys),  # keys above this were written after the snapshot
                "priority": priority,
            },
        )
        return {"exported": len(keys), "built": False}

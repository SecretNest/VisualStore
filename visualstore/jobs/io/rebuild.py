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
        # The snapshot is "every live key up to max_seq as of now". Keys the fingerprint sink
        # writes while this export runs have higher seqs and are matched afterwards through
        # match_unindexed(after=max_seq); they must never enter the export, least of all as
        # zero vectors (audit 2026-11-07).
        max_seq = fp.max_live_seq(vconn, spec.id)
        total = fp.live_key_count(vconn, spec.id)
        vectors = np.empty((total, spec.dim), dtype=np.float16)  # one copy, filled in place
        keys: list[int] = []
        for rows in fp.iter_vectors(vconn, spec.id):
            for r in rows:
                if r["seq"] > max_seq or len(keys) >= total:
                    break
                vectors[len(keys)] = np.frombuffer(r["vector"], dtype=np.float16)
                keys.append(r["seq"])
            ctx.report({"done": len(keys), "total": 0, "message": f"exported {len(keys)} vectors"})
            if rows[-1]["seq"] >= max_seq:
                break
        if len(keys) < total:  # rows retired under us: keep what was read
            vectors = vectors[: len(keys)]
        priority = int(ctx.params.get("priority", PRIORITY_INBOX))
        options = index_options(ctx.settings, ctx.paths, spec.dim, vector_count=len(keys))
        if not options["pca_dims"]:
            (ctx.paths.root / "pca.npz").unlink(missing_ok=True)
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
        if options["pca_dims"]:  # fitted on a sample of this profile's own vectors (ADR-0008)
            pca.save(pca.fit(vectors, options["pca_dims"]), Path(options["pca_path"]))
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
                "max_seq": max_seq,  # keys above this were written after the snapshot
                "priority": priority,
            },
        )
        return {"exported": len(keys), "built": False}

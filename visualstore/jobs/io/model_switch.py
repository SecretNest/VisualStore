"""Switch the active model (design §7.7).

Queues vectors for files that lack them and rebuilds the index; judgments are untouched.
"""

from __future__ import annotations

from contextlib import closing

from visualstore.db import fingerprints as fp
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io.scan import FINGERPRINT_BATCH, _abs
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_ARCHIVE, PRIORITY_INBOX
from visualstore.models.registry import get_spec, job_params, resolve_model_path


def run_model_switch(ctx: IoContext) -> dict:
    spec = get_spec(ctx.params["model_id"])
    resolve_model_path(spec, ctx.model_dir)  # downloads when allowed; raises before any change
    model = job_params(spec, ctx.model_dir)
    with closing(ctx.open_metadata()) as conn, closing(ctx.open_vectors()) as vconn:
        settings_db.set(conn, "model.id", spec.id)
        have = fp.files_with_vectors(vconn, spec.id)
        queued_files = jobs_made = 0
        for folder in folders_db.list_folders(conn):
            rows = conn.execute(
                "SELECT f.id, f.directory, f.name FROM file f"
                " JOIN extension e ON e.id = f.extension_id"
                " WHERE f.folder_id = ? AND f.state = 'fingerprinted' AND e.media_type = 'image'"
                " ORDER BY f.directory, f.name",
                (bytes(folder["id"]),),
            ).fetchall()
            pending = [r for r in rows if bytes(r["id"]) not in have]
            priority = PRIORITY_INBOX if folder["role"] == "inbox" else PRIORITY_ARCHIVE
            for start in range(0, len(pending), FINGERPRINT_BATCH):
                batch = pending[start : start + FINGERPRINT_BATCH]
                jobs_db.enqueue(
                    conn,
                    type="fingerprint",
                    kind="compute",
                    priority=priority,
                    params={
                        "files": [
                            {
                                "id": id_to_str(r["id"]),
                                "path": _abs(folder["path"], r["directory"], r["name"]),
                                "media_type": "image",
                            }
                            for r in batch
                        ],
                        "model": model,
                        "max_pixels": int(ctx.settings["media.max_pixels"]),
                        "priority": priority,
                    },
                )
                jobs_made += 1
                queued_files += len(batch)
        rebuild = not (
            jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(conn, "build_index")
        )
        if rebuild:
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=PRIORITY_INBOX)
        return {
            "model_id": spec.id,
            "queued_files": queued_files,
            "fingerprint_jobs": jobs_made,
            "rebuild_queued": rebuild,
        }

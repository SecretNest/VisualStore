"""Switch the active model (design §7.7).

Queues vectors for files that lack them and rebuilds the index; judgments are untouched.
"""

from __future__ import annotations

import json
from contextlib import closing

from visualstore.db import fingerprints as fp
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io.scan import FINGERPRINT_BATCH, _abs
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_ARCHIVE, PRIORITY_INBOX
from visualstore.media.frames import sampling_params
from visualstore.models.registry import get_spec, job_params, resolve_model_path


def _cancel_foreign_jobs(conn, model_id: str) -> int:
    """Queued fingerprint/match jobs still carry the previous model: their results would be
    discarded by the sinks anyway, so cancel them instead of computing them."""
    rows = conn.execute(
        "SELECT id, params FROM job WHERE state = 'queued' AND type IN ('fingerprint', 'match')"
    ).fetchall()
    cancelled = 0
    for row in rows:
        model = (json.loads(row["params"]) or {}).get("model") or {}
        if model.get("id") != model_id and jobs_db.cancel(conn, bytes(row["id"])):
            cancelled += 1
    return cancelled


def queue_missing_vectors(
    conn,
    vconn,
    settings: dict,
    model_dir,
    folder_id: bytes | None = None,
    have: set[bytes] | None = None,
) -> tuple[int, int]:
    """Fingerprint jobs for every fingerprinted media file lacking vectors for the active model,
    skipping files already named by a queued or running fingerprint job. Returns (files, jobs).
    Shared by the model switch and the start-up consistency check."""
    spec = get_spec(settings["model.id"], model_dir)
    model = job_params(spec, model_dir)
    have = fp.files_with_vectors(vconn, spec.id) if have is None else have
    in_flight = jobs_db.active_file_ids(conn, "fingerprint")
    queued_files = jobs_made = 0
    for folder in folders_db.list_folders(conn):
        if folder_id is not None and bytes(folder["id"]) != folder_id:
            continue
        rows = conn.execute(
            "SELECT f.id, f.directory, f.name, e.media_type FROM file f"
            " JOIN extension e ON e.id = f.extension_id"
            " WHERE f.folder_id = ? AND f.state = 'fingerprinted'"
            " AND e.media_type IN ('image', 'animated', 'video')"
            " ORDER BY f.directory, f.name",
            (bytes(folder["id"]),),
        ).fetchall()
        pending = [
            r for r in rows if bytes(r["id"]) not in have and id_to_str(r["id"]) not in in_flight
        ]
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
                            "media_type": r["media_type"],
                        }
                        for r in batch
                    ],
                    "model": model,
                    "max_pixels": int(settings["media.max_pixels"]),
                    "frames": sampling_params(settings),
                    "priority": priority,
                },
            )
            jobs_made += 1
            queued_files += len(batch)
    return queued_files, jobs_made


def run_model_switch(ctx: IoContext) -> dict:
    spec = get_spec(ctx.params["model_id"], ctx.model_dir)
    resolve_model_path(spec, ctx.model_dir)  # downloads when allowed; raises before any change
    with closing(ctx.open_metadata()) as conn, closing(ctx.open_vectors()) as vconn:
        settings_db.set(conn, "model.id", spec.id)
        cancelled = _cancel_foreign_jobs(conn, spec.id)
        settings = {**ctx.settings, "model.id": spec.id}
        queued_files, jobs_made = queue_missing_vectors(conn, vconn, settings, ctx.model_dir)
        rebuild = not (
            jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(conn, "build_index")
        )
        if rebuild:
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=PRIORITY_INBOX)
        return {
            "model_id": spec.id,
            "cancelled_jobs": cancelled,
            "queued_files": queued_files,
            "fingerprint_jobs": jobs_made,
            "rebuild_queued": rebuild,
        }

"""Folder deletion as a job (audit B12): a 500 000-file Folder must not be removed inside one
request on the event loop, and its vectors, index keys and derived images must go with it."""

from __future__ import annotations

from contextlib import closing

from visualstore.db import files as files_db
from visualstore.db import fingerprints as fp
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.db.util import chunked
from visualstore.ids import id_from_str
from visualstore.jobs.derived import remove_derived
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX


def run_folder_delete(ctx: IoContext) -> dict:
    folder_id = id_from_str(ctx.params["folder_id"])
    removed = 0
    with closing(ctx.open_metadata()) as conn, closing(ctx.open_vectors()) as vconn:
        folder = folders_db.get_folder(conn, folder_id)
        if folder is None:
            return {"files": 0, "skipped": "folder missing"}
        ids = [bytes(r["id"]) for r in files_db.list_files_in_folder(conn, folder_id)]
        for chunk in chunked(ids):
            for fid in chunk:
                remove_derived(ctx.paths, fid)
            fp.delete_for_files(vconn, chunk)  # frames gone, keys retired for the next match
            files_db.delete_files(conn, chunk)  # cascades pairs and group membership
            removed += len(chunk)
            ctx.report({"done": removed, "total": len(ids), "message": f"{removed} files removed"})
        folders_db.delete_folder(conn, folder_id)  # marks co-members of other Folders dirty
        if not jobs_db.has_active(conn, "regroup"):
            jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
    return {"files": removed}

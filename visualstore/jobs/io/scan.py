"""Scan job (design §7.1): reconcile a Folder's directory tree with the file table.

Runs in the scheduler's worker thread with its own connection. Pure decisions live in
domain.scan; this module does the walking, hashing of move candidates, and the writes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import closing

from visualstore.db import files as files_db
from visualstore.db import fingerprints as fp
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.domain.scan import (
    DbFile,
    DiskEntry,
    diff,
    move_candidates,
    resolve_moves,
    should_skip_directory,
)
from visualstore.ids import id_from_str, id_to_str
from visualstore.jobs.compute.handlers import sha1_of
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_ARCHIVE, PRIORITY_INBOX
from visualstore.media.frames import sampling_params
from visualstore.models.registry import ModelError, get_spec, job_params, resolve_model_path

FINGERPRINT_BATCH = 64


class ScanError(Exception):
    """The Folder cannot be scanned safely; the job fails and the database is left alone."""


def _abs(folder_path: str, directory: str, name: str) -> str:
    if directory:
        return os.path.join(folder_path, directory, name)
    return os.path.join(folder_path, name)


def walk(
    folder_path: str,
    extensions: dict,
    ignored: list[tuple[str, bool]],
    report: Callable[[dict], None],
) -> list[DiskEntry]:
    out: list[DiskEntry] = []
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        if should_skip_directory(rel_dir, ignored):
            continue
        abs_dir = os.path.join(folder_path, rel_dir) if rel_dir else folder_path
        try:
            with os.scandir(abs_dir) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                stack.append(f"{rel_dir}/{entry.name}" if rel_dir else entry.name)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            _, dot, ext = entry.name.rpartition(".")
            if not dot or ext.lower() not in extensions:
                continue
            st = entry.stat(follow_symlinks=False)
            out.append(DiskEntry(rel_dir, entry.name, ext.lower(), st.st_size, st.st_mtime_ns))
            if len(out) % 1000 == 0:
                report({"done": len(out), "total": 0, "message": f"listed {len(out)} files"})
    return out


def run_scan(ctx: IoContext) -> dict:
    folder_id = id_from_str(ctx.params["folder_id"])
    with closing(ctx.open_metadata()) as conn:
        folder = folders_db.get_folder(conn, folder_id)
        if folder is None:
            return {"skipped": "folder missing"}
        if not folder["enabled"]:
            return {"skipped": "folder disabled"}
        extensions = folders_db.extension_map(conn)
        ignored = [
            (r["directory"], bool(r["recursive"])) for r in folders_db.list_ignored(conn, folder_id)
        ]
        _check_root(folder["path"])
        model = _resolve_model(ctx)
        disk = walk(folder["path"], extensions, ignored, ctx.report)
        db_rows = files_db.list_files_in_folder(conn, folder_id)
        if db_rows and not disk:
            raise ScanError(
                f"{folder['path']} lists no media files while {len(db_rows)} are recorded;"
                " refusing to delete them. Is the share mounted?"
            )
        plan = diff(
            disk,
            [
                DbFile(bytes(r["id"]), r["directory"], r["name"], r["size"], r["mtime"], r["sha1"])
                for r in db_rows
            ],
        )
        ctx.report(
            {
                "done": 0,
                "total": 0,
                "message": f"{len(plan.insert)} new, {len(plan.missing)} missing",
            }
        )

        inserted: dict[tuple[str, str], bytes] = {}
        for e in plan.insert:
            inserted[(e.directory, e.name)] = files_db.insert_file(
                conn,
                folder_id=folder_id,
                directory=e.directory,
                name=e.name,
                extension_id=bytes(extensions[e.extension]["id"]),
                size=e.size,
                mtime=e.mtime,
            )
        for fid, e in plan.changed:
            files_db.update_stat(conn, fid, e.size, e.mtime)
            files_db.reset_for_recompute(conn, fid)
        for fid, e in plan.touch:  # imported rows learn their real mtime, hash untouched
            files_db.update_stat(conn, fid, e.size, e.mtime)
        for fid, e in plan.renamed:  # same file under NOCASE, different spelling on disk
            files_db.update_path(conn, fid, e.directory, e.name)

        moved_ids: set[bytes] = set()
        candidates = move_candidates(plan)
        if candidates:
            missing_sha1 = {f.id: f.sha1 for olds, _ in candidates.values() for f in olds if f.sha1}
            inserted_sha1: dict[tuple[str, str], bytes] = {}
            for _, news in candidates.values():
                for e in news:
                    try:
                        inserted_sha1[(e.directory, e.name)] = sha1_of(
                            _abs(folder["path"], e.directory, e.name)
                        )
                    except OSError:
                        pass
            for old_id, (directory, name) in resolve_moves(missing_sha1, inserted_sha1):
                new_row_id = inserted.pop((directory, name))
                entry = next(e for e in plan.insert if (e.directory, e.name) == (directory, name))
                files_db.delete_files(conn, [new_row_id])
                files_db.update_path(conn, old_id, directory, name)
                files_db.update_stat(conn, old_id, entry.size, entry.mtime)
                moved_ids.add(old_id)

        to_delete = [f.id for f in plan.missing if f.id not in moved_ids]
        files_db.delete_files(conn, to_delete)
        gone_vectors = to_delete + [fid for fid, _ in plan.changed]
        if gone_vectors:
            with closing(ctx.open_vectors()) as vconn:
                fp.delete_for_files(vconn, gone_vectors)
        if to_delete and not jobs_db.has_active(conn, "regroup"):
            jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)

        default_priority = PRIORITY_INBOX if folder["role"] == "inbox" else PRIORITY_ARCHIVE
        priority = int(ctx.params.get("priority") or default_priority)
        jobs = _enqueue_fingerprints(conn, folder, priority, ctx, model)
        return {
            "inserted": len(plan.insert) - len(moved_ids),
            "changed": len(plan.changed),
            "unchanged": plan.unchanged,
            "moved": len(moved_ids),
            "deleted": len(to_delete),
            "renamed": len(plan.renamed),
            "collisions": len(plan.collisions),
            "fingerprint_jobs": jobs,
        }


def _check_root(folder_path: str) -> None:
    if not os.path.isdir(folder_path):
        raise ScanError(f"Folder path is not available: {folder_path}")
    try:
        with os.scandir(folder_path):
            pass
    except OSError as exc:
        raise ScanError(f"Folder path is not available: {folder_path} ({exc})") from exc


def _resolve_model(ctx: IoContext) -> dict:
    """The active model as compute-job params; downloads it on first use (ADR-0005). A model
    that cannot be obtained fails the scan before anything is recorded."""
    try:
        spec = get_spec(ctx.settings["model.id"], ctx.model_dir)
        resolve_model_path(spec, ctx.model_dir)
    except ModelError as exc:
        raise ScanError(f"The active model is not available: {exc}") from exc
    return job_params(spec, ctx.model_dir)


def _enqueue_fingerprints(conn, folder, priority: int, ctx: IoContext, model: dict) -> int:
    extensions = {bytes(r["id"]): r["media_type"] for r in folders_db.list_extensions(conn)}
    max_pixels = int(ctx.settings["media.max_pixels"])
    pending = files_db.files_by_state(conn, "new", bytes(folder["id"]), limit=1_000_000)
    count = 0
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
                        "media_type": extensions.get(bytes(r["extension_id"]), "other"),
                    }
                    for r in batch
                ],
                "model": model,
                "max_pixels": max_pixels,
                "frames": sampling_params(ctx.settings),
                "priority": priority,
            },
        )
        count += 1
    return count

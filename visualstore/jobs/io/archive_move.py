"""Archive move (design §8.5): move an inbox Folder's files into an archive Folder, keeping
relative paths, ids, hashes, pairs and judgments."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.domain.paths import is_under, normalise_relative
from visualstore.ids import id_from_str
from visualstore.jobs.io.actions import move_file, remove_empty_parents
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX


def _selected(row, prefixes: list[str]) -> bool:
    return not prefixes or any(is_under(row["directory"], p, recursive=True) for p in prefixes)


def run_archive_move(ctx: IoContext) -> dict:
    prefixes = [normalise_relative(d) for d in ctx.params.get("directories") or []]
    with closing(ctx.open_metadata()) as conn:
        source = folders_db.get_folder(conn, id_from_str(ctx.params["source_folder_id"]))
        target = folders_db.get_folder(conn, id_from_str(ctx.params["target_folder_id"]))
        if source is None or target is None:
            return {"skipped": "folder missing"}
        if source["role"] != "inbox" or target["role"] != "archive":
            return {"skipped": "source must be an inbox Folder and target an archive Folder"}
        rows = [
            r
            for r in files_db.list_files_in_folder(conn, bytes(source["id"]))
            if _selected(r, prefixes)
        ]
        open_groups = {
            bytes(g["id"])
            for r in rows
            if (g := groups_db.group_of_file(conn, bytes(r["id"]))) is not None
            and g["status"] == "open"
        }
        if open_groups:
            return {
                "skipped": f"{len(open_groups)} open group(s) still contain files from this"
                " selection; resolve them first"
            }

        moved, conflicts, moved_ids = 0, [], []
        for i, row in enumerate(rows, start=1):
            rel = files_db.relative_path(row["directory"], row["name"])
            src = Path(source["path"]) / rel
            dest = Path(target["path"]) / rel
            taken = files_db.find_file(conn, bytes(target["id"]), row["directory"], row["name"])
            if dest.exists() or taken is not None or not src.is_file():
                conflicts.append(rel)
                continue
            move_file(src, dest)
            with conn:
                conn.execute(
                    "UPDATE file SET folder_id = ? WHERE id = ?",
                    (bytes(target["id"]), bytes(row["id"])),
                )
            moved += 1
            moved_ids.append(bytes(row["id"]))
            remove_empty_parents(src.parent, Path(source["path"]))
            if i % 100 == 0:
                ctx.report({"done": i, "total": len(rows), "message": rel})
        if moved_ids:
            groups_db.mark_dirty(conn, moved_ids)
            if not jobs_db.has_active(conn, "regroup"):
                jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
        ctx.report(
            {
                "done": len(rows),
                "total": len(rows),
                "message": f"moved {moved}, {len(conflicts)} conflicts",
            }
        )
        return {"moved": moved, "conflicts": conflicts}

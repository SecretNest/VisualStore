"""File actions (design §8.5): trash with retention, restore, purge. Disk first, database second."""

from __future__ import annotations

import errno
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from visualstore.db import actions as actions_db
from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.domain.paths import TRASH_DIR
from visualstore.ids import id_from_str, id_to_str
from visualstore.jobs import results
from visualstore.jobs.scheduler import IoContext


class ActionError(Exception):
    pass


# -- disk primitives ---------------------------------------------------------------------


def trash_path(folder_path: str, directory: str, name: str, when: datetime) -> Path:
    base = Path(folder_path) / TRASH_DIR / when.astimezone(UTC).strftime("%Y-%m")
    return base / directory / name if directory else base / name


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for i in range(1, 10_000):
        candidate = dest.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    raise ActionError(f"too many name collisions for {dest}")


def move_file(src: Path, dest: Path) -> Path:
    """Move with collision suffixes; cross-device moves copy, verify the size, then delete."""
    dest = _unique(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dest)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            shutil.copy2(src, dest)
            if dest.stat().st_size != src.stat().st_size:
                dest.unlink(missing_ok=True)
                raise ActionError(f"size mismatch after copying {src} to {dest}") from exc
            os.unlink(src)
    except ActionError:
        raise
    except OSError as exc:
        raise ActionError(f"could not move {src} to {dest}: {exc}") from exc
    return dest


def remove_empty_parents(path: Path, stop_at: Path) -> None:
    current = path if path.is_dir() else path.parent
    stop_at = stop_at.resolve()
    while True:
        try:
            resolved = current.resolve()
        except OSError:
            return
        if resolved == stop_at or stop_at not in resolved.parents or current.name == TRASH_DIR:
            return
        try:
            current.rmdir()  # fails (and we stop) when not empty
        except OSError:
            return
        current = current.parent


# -- trash / restore -----------------------------------------------------------------------


def _trash_one(
    conn: sqlite3.Connection, ctx: IoContext, file_id: bytes, now: datetime
) -> str | None:
    row = files_db.get_file(conn, file_id)
    if row is None:
        return "missing row"
    folder = folders_db.get_folder(conn, bytes(row["folder_id"]))
    rel = files_db.relative_path(row["directory"], row["name"])
    src = Path(folder["path"]) / rel
    if not src.is_file():
        return "missing file"
    before = {
        "file_id": id_to_str(file_id),
        "folder_id": id_to_str(bytes(row["folder_id"])),
        "path": str(src),
        "relative_path": rel,
        "directory": row["directory"],
        "name": row["name"],
        "extension_id": id_to_str(bytes(row["extension_id"])),
        "size": row["size"],
        "mtime": row["mtime"],
        "sha1": row["sha1"].hex() if row["sha1"] else None,
    }
    if ctx.settings.get("trash.enabled", True):
        dest = move_file(src, trash_path(folder["path"], row["directory"], row["name"], now))
        after = {"path": str(dest)}
    else:
        try:
            src.unlink()
        except OSError as exc:
            raise ActionError(f"could not delete {src}: {exc}") from exc
        after = {"path": None, "permanent": True}
    action_id = actions_db.record(conn, "trash", file_id, before, after)
    if after.get("permanent"):
        actions_db.supersede(conn, action_id)
    files_db.delete_files(conn, [file_id])
    remove_empty_parents(src.parent, Path(folder["path"]))
    return None


def _restore_one(conn: sqlite3.Connection, ctx: IoContext, action_id: bytes) -> str | None:
    act = actions_db.get(conn, action_id)
    if act is None or act["type"] != "trash" or act["superseded_at"]:
        return "not restorable"
    d = actions_db.action_to_dict(act)
    before, after = d["before"], d["after"]
    if after.get("path") is None:
        return "permanently deleted"
    folder = folders_db.get_folder(conn, id_from_str(before["folder_id"]))
    if folder is None:
        return "folder missing"
    trashed = Path(after["path"])
    if not trashed.is_file():
        return "trash file missing"
    target = Path(folder["path"]) / before["relative_path"]
    occupied = files_db.find_file(conn, bytes(folder["id"]), before["directory"], before["name"])
    if target.exists() or occupied is not None:
        return "exists"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(trashed, target)
    except OSError as exc:
        raise ActionError(f"could not restore {trashed} to {target}: {exc}") from exc
    file_id = id_from_str(before["file_id"])
    sha1 = bytes.fromhex(before["sha1"]) if before.get("sha1") else None
    files_db.insert_file(
        conn,
        folder_id=bytes(folder["id"]),
        directory=before["directory"],
        name=before["name"],
        extension_id=id_from_str(before["extension_id"]),
        size=before["size"],
        mtime=before["mtime"],
        state="fingerprinted" if sha1 else "new",
        sha1=sha1,
        file_id=file_id,
    )
    if sha1:
        results.sink_fingerprint(
            conn,
            ctx.profile,
            {},
            {
                "files": [
                    {
                        "id": before["file_id"],
                        "sha1": before["sha1"],
                        "state": "fingerprinted",
                        "size": before["size"],
                    }
                ]
            },
        )
    actions_db.supersede(conn, action_id)
    actions_db.record(
        conn,
        "restore",
        file_id,
        {"trash_action": id_to_str(action_id), "path": str(trashed)},
        {"path": str(target)},
    )
    remove_empty_parents(trashed.parent, Path(folder["path"]))
    return None


def run_apply_action(ctx: IoContext) -> dict:
    now = datetime.now(UTC)
    result: dict = {"trashed": 0, "restored": 0, "skipped": []}
    with closing(ctx.open_metadata()) as conn:
        for text in ctx.params.get("trash", []):
            reason = _trash_one(conn, ctx, id_from_str(text), now)
            if reason:
                result["skipped"].append({"id": text, "reason": reason})
            else:
                result["trashed"] += 1
        for text in ctx.params.get("restore", []):
            reason = _restore_one(conn, ctx, id_from_str(text))
            if reason:
                result["skipped"].append({"id": text, "reason": reason})
            else:
                result["restored"] += 1
        group_text = ctx.params.get("group_id")
        if group_text and not result["skipped"]:
            g = groups_db.get_group(conn, id_from_str(group_text))
            if g is not None and g["status"] == "open":
                groups_db.set_status(conn, bytes(g["id"]), "resolved")
    return result


# -- purge ---------------------------------------------------------------------------------


def run_purge_trash(ctx: IoContext) -> dict:
    days = int(ctx.settings.get("trash.retention_days", 30))
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    purged = missing = 0
    with closing(ctx.open_metadata()) as conn:
        for act in actions_db.trash_older_than(conn, cutoff):
            d = actions_db.action_to_dict(act)
            path = Path(d["after"]["path"]) if d["after"].get("path") else None
            if path is not None and path.is_file():
                try:
                    path.unlink()
                except OSError as exc:
                    raise ActionError(f"could not purge {path}: {exc}") from exc
                purged += 1
            else:
                missing += 1
            actions_db.supersede(conn, bytes(act["id"]))
            actions_db.record(
                conn, "purge", act["file_id"], {"trash_action": d["id"], "path": str(path)}, {}
            )
            if path is not None:
                folder = folders_db.get_folder(conn, id_from_str(d["before"]["folder_id"]))
                if folder is not None:
                    remove_empty_parents(path.parent, Path(folder["path"]))
    return {"purged": purged, "missing": missing}

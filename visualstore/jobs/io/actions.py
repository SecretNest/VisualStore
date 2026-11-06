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
from visualstore.db import fingerprints as fp
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.domain.paths import TRASH_DIR, normalise_relative
from visualstore.ids import id_from_str, id_to_str
from visualstore.jobs import results
from visualstore.jobs.io.scan import _abs
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX, PRIORITY_USER
from visualstore.models.registry import ModelError, get_spec, job_params


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


def move_file(src: Path, dest: Path, *, allow_suffix: bool = True) -> Path:
    """Move with collision suffixes (or refuse an occupied target when `allow_suffix` is off);
    cross-device moves copy, verify the size, then delete."""
    if allow_suffix:
        dest = _unique(dest)
    elif dest.exists():
        raise ActionError("target exists")
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
    conn: sqlite3.Connection,
    ctx: IoContext,
    file_id: bytes,
    now: datetime,
    group_id: bytes | None,
) -> str | None:
    row = files_db.get_file(conn, file_id)
    if row is None:
        return "missing row"
    folder = folders_db.get_folder(conn, bytes(row["folder_id"]))
    rel = files_db.relative_path(row["directory"], row["name"])
    src = Path(folder["path"]) / rel
    if not src.is_file():
        return "missing file"
    st = src.stat()
    if st.st_size != row["size"] or (row["mtime"] and st.st_mtime_ns != row["mtime"]):
        return "changed on disk"  # the decision was made about different content
    if group_id is not None:
        member = conn.execute(
            "SELECT decision FROM file_group_member WHERE group_id = ? AND file_id = ?",
            (group_id, file_id),
        ).fetchone()
        if member is None or member["decision"] != "delete":
            return "decision changed"
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


def _refingerprint(conn, ctx: IoContext, folder, file_id: bytes, before: dict) -> None:
    """Trashing dropped the file's vectors and thumbnail; compute them again (final review)."""
    media = {bytes(r["id"]): r["media_type"] for r in folders_db.list_extensions(conn)}.get(
        id_from_str(before["extension_id"]), "other"
    )
    if media != "image":
        return
    try:
        model = job_params(get_spec(ctx.settings["model.id"], ctx.model_dir), ctx.model_dir)
    except ModelError:
        return  # no usable model configured; the next model switch recomputes everything
    jobs_db.enqueue(
        conn,
        type="fingerprint",
        kind="compute",
        priority=PRIORITY_USER,
        params={
            "files": [
                {
                    "id": id_to_str(file_id),
                    "path": _abs(folder["path"], before["directory"], before["name"]),
                    "media_type": media,
                }
            ],
            "model": model,
            "max_pixels": int(ctx.settings["media.max_pixels"]),
            "priority": PRIORITY_USER,
        },
    )


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
    if sha1 and results.link_identical(conn, file_id, sha1):
        if not jobs_db.has_active(conn, "regroup"):
            jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
    _refingerprint(conn, ctx, folder, file_id, before)
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


def _relocate(
    conn: sqlite3.Connection, file_id: bytes, target_folder, directory: str, name: str, kind: str
) -> str | None:
    """Move/rename one file: disk first, then the row; never overwrites (design §8.5)."""
    row = files_db.get_file(conn, file_id)
    if row is None:
        return "missing row"
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return "bad name"
    try:
        directory = normalise_relative(directory)
    except ValueError:
        return "bad name"
    source_folder = folders_db.get_folder(conn, bytes(row["folder_id"]))
    src = Path(source_folder["path"]) / files_db.relative_path(row["directory"], row["name"])
    if not src.is_file():
        return "missing file"
    dest = Path(target_folder["path"]) / files_db.relative_path(directory, name)
    if dest == src:
        return None
    occupied = files_db.find_file(conn, bytes(target_folder["id"]), directory, name)
    if dest.exists() or (occupied is not None and bytes(occupied["id"]) != file_id):
        return "exists"
    move_file(src, dest, allow_suffix=False)
    before = {
        "folder_id": id_to_str(bytes(row["folder_id"])),
        "directory": row["directory"],
        "name": row["name"],
        "path": str(src),
    }
    after = {
        "folder_id": id_to_str(bytes(target_folder["id"])),
        "directory": directory,
        "name": name,
        "path": str(dest),
    }
    files_db.update_location(conn, file_id, bytes(target_folder["id"]), directory, name)
    actions_db.record(conn, kind, file_id, before, after)
    if bytes(target_folder["id"]) != bytes(row["folder_id"]):
        groups_db.mark_dirty(conn, [file_id])  # the role may differ: preselection changes
    remove_empty_parents(src.parent, Path(source_folder["path"]))
    return None


def execute_action(
    conn: sqlite3.Connection, vconn: sqlite3.Connection, ctx: IoContext, params: dict, now: datetime
) -> dict:
    """The body of apply_action for one params dict; the bulk job calls it per group."""
    result: dict = {"trashed": 0, "restored": 0, "moved": 0, "renamed": 0, "skipped": []}
    group_text = params.get("group_id")
    group_id = id_from_str(group_text) if group_text else None
    for text in params.get("trash", []):
        file_id = id_from_str(text)
        reason = _trash_one(conn, ctx, file_id, now, group_id)
        if reason:
            result["skipped"].append({"id": text, "reason": reason})
        else:
            fp.delete_for_files(vconn, [file_id])
            result["trashed"] += 1
    for text in params.get("restore", []):
        reason = _restore_one(conn, ctx, id_from_str(text))
        if reason:
            result["skipped"].append({"id": text, "reason": reason})
        else:
            result["restored"] += 1
    for item in params.get("move", []):
        file_id = id_from_str(item["file_id"])
        row = files_db.get_file(conn, file_id)
        target = folders_db.get_folder(conn, id_from_str(item["folder_id"]))
        if row is None:
            reason = "missing row"
        elif target is None:
            reason = "no such folder"
        else:
            reason = _relocate(
                conn, file_id, target, item.get("directory", ""), row["name"], "move"
            )
        if reason:
            result["skipped"].append({"id": item["file_id"], "reason": reason})
        else:
            result["moved"] += 1
    for item in params.get("rename", []):
        file_id = id_from_str(item["file_id"])
        row = files_db.get_file(conn, file_id)
        if row is None:
            result["skipped"].append({"id": item["file_id"], "reason": "missing row"})
            continue
        target = folders_db.get_folder(conn, bytes(row["folder_id"]))
        reason = _relocate(conn, file_id, target, row["directory"], item["name"], "rename")
        if reason:
            result["skipped"].append({"id": item["file_id"], "reason": reason})
        else:
            result["renamed"] += 1
    if (result["moved"] or result["renamed"]) and not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
    if group_id is not None and not result["skipped"]:
        g = groups_db.get_group(conn, group_id)
        if g is not None and g["status"] == "open":
            groups_db.set_status(conn, bytes(g["id"]), "resolved")
    if result["trashed"] and not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
    return result


def run_apply_action(ctx: IoContext) -> dict:
    with closing(ctx.open_metadata()) as conn, closing(ctx.open_vectors()) as vconn:
        return execute_action(conn, vconn, ctx, ctx.params, datetime.now(UTC))


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

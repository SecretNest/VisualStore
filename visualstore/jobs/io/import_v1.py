"""One-time import of an ImageStore v1 database (design §10, Appendix A). Read-only on v1."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.db import pairs as pairs_db
from visualstore.domain.paths import normalise_relative
from visualstore.ids import id_to_str
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX, PRIORITY_USER

SCOPE = {0: "all", 1: "other_directories", 2: "other_folders"}
VIDEO = {"mp4", "mkv", "webm", "mov", "avi", "m4v", "wmv", "flv"}
STATE_NOT_READABLE = 2
STAT_KEYS = (
    "folders",
    "extensions_added",
    "files",
    "files_skipped",
    "sha1_pairs",
    "related_keep",
    "similar_skipped",
    "similar_orphans",
    "scan_jobs",
)


class ImportError_(Exception):
    pass


def gid(blob: bytes) -> bytes:
    """v1 ids were written by .NET Guid.ToByteArray(): mixed-endian. Read with bytes_le."""
    return uuid.UUID(bytes_le=bytes(blob)).bytes


def _open_v1(path: str) -> sqlite3.Connection:
    p = Path(path)
    if not p.is_file():
        raise ImportError_(f"v1 database not found: {path}")
    try:
        conn = sqlite3.connect(f"file:{quote(str(p.resolve()))}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1 FROM Folder LIMIT 1")
    except sqlite3.Error as exc:
        raise ImportError_(f"not a readable v1 database: {exc}") from exc
    return conn


def _check_preconditions(conn, v1, mapping: dict[str, str]) -> list[sqlite3.Row]:
    if folders_db.list_folders(conn):
        raise ImportError_("The profile must be empty (no Folders) before importing a v1 database.")
    v1_folders = v1.execute("SELECT * FROM Folder").fetchall()
    missing = [f["Name"] for f in v1_folders if f["Name"] not in mapping]
    if missing:
        raise ImportError_(f"No container path given for v1 Folder(s): {', '.join(missing)}")
    for name, path in mapping.items():
        if not Path(path).is_dir():
            raise ImportError_(f"Mapped path for '{name}' does not exist in the container: {path}")
    return v1_folders


def _import_folders(conn, v1, v1_folders, mapping, stats) -> dict[bytes, bytes]:
    folder_ids: dict[bytes, bytes] = {}
    for f in v1_folders:
        fid = folders_db.create_folder(
            conn,
            name=f["Name"],
            path=mapping[f["Name"]],
            role="archive" if f["IsSealed"] else "inbox",
            compare_scope=SCOPE.get(f["CompareImageWith"], "all"),
            folder_id=gid(f["Id"]),
        )
        folder_ids[bytes(f["Id"])] = fid
        stats["folders"] += 1
    for d in v1.execute("SELECT * FROM IgnoredDirectory"):
        folder = folder_ids.get(bytes(d["FolderId"]))
        if folder is not None and normalise_relative(d["Directory"]):
            try:
                folders_db.add_ignored(
                    conn,
                    folder,
                    d["Directory"],
                    bool(d["IsSubDirectoryIncluded"]),
                    ignored_id=gid(d["Id"]),  # v1 ids are kept (design §10)
                )
            except folders_db.FolderError:
                pass  # duplicate after normalisation
    return folder_ids


def _import_extensions(conn, v1, stats) -> tuple[dict[bytes, bytes], dict[bytes, str]]:
    existing = {r["extension"].lower(): bytes(r["id"]) for r in folders_db.list_extensions(conn)}
    ext_ids: dict[bytes, bytes] = {}
    ext_names: dict[bytes, str] = {}
    for e in v1.execute("SELECT * FROM Extension"):
        name = e["Extension"].lower().lstrip(".")
        ext_names[bytes(e["Id"])] = name
        if name in existing:
            ext_ids[bytes(e["Id"])] = existing[name]
            if e["Ignored"]:
                folders_db.set_extension_enabled(conn, existing[name], False)
            continue
        media = "image" if e["IsImage"] else ("video" if name in VIDEO else "other")
        ext_ids[bytes(e["Id"])] = folders_db.upsert_extension(
            conn, name, media, enabled=not e["Ignored"], extension_id=gid(e["Id"])
        )
        stats["extensions_added"] += 1
    return ext_ids, ext_names


def _import_files(conn, v1, ctx, folder_ids, ext_ids, ext_names, stats) -> dict[bytes, bytes]:
    file_ids: dict[bytes, bytes] = {}
    for f in v1.execute("SELECT * FROM File"):
        folder = folder_ids.get(bytes(f["FolderId"]))
        ext_id = ext_ids.get(bytes(f["ExtensionId"]))
        if folder is None or ext_id is None:
            stats["files_skipped"] += 1
            continue
        sha1 = bytes(f["Sha1Hash"]) if f["Sha1Hash"] else None
        if f["FileState"] == STATE_NOT_READABLE:
            state = "unreadable"
        else:
            state = "fingerprinted" if sha1 else "new"
        new_row = files_db.insert_file(
            conn,
            folder_id=folder,
            directory=normalise_relative(f["Path"] or ""),
            name=f"{f['FileName']}.{ext_names[bytes(f['ExtensionId'])]}",
            extension_id=ext_id,
            size=max(0, int(f["FileSize"] or 0)),
            mtime=0,
            state=state,
            sha1=sha1,
            file_id=gid(f["Id"]),
        )
        file_ids[bytes(f["Id"])] = new_row
        stats["files"] += 1
        if stats["files"] % 1000 == 0:
            ctx.report({"done": stats["files"], "total": 0, "message": f"{stats['files']} files"})
    return file_ids


def _link_identical(conn, v1, file_ids, stats) -> list[bytes]:
    """Byte-identical pairs come from sha1 buckets, not SameFile (which v1 filled lazily)."""
    ignored_rows = v1.execute("SELECT FileId FROM SameFile WHERE IsIgnored = 1")
    ignored_v1 = {bytes(r["FileId"]) for r in ignored_rows}
    ignored = {file_ids[v] for v in ignored_v1 if v in file_ids}
    dirty: list[bytes] = []
    buckets = conn.execute(
        "SELECT sha1 FROM file WHERE sha1 IS NOT NULL GROUP BY sha1 HAVING COUNT(*) > 1"
    ).fetchall()
    for b in buckets:
        members = [bytes(r["id"]) for r in files_db.files_by_sha1(conn, bytes(b["sha1"]))]
        hub = members[0]
        for other in members[1:]:
            pairs_db.upsert_sha1_pair(conn, hub, other)
            stats["sha1_pairs"] += 1
            if hub in ignored or other in ignored:
                pairs_db.set_judgment(conn, hub, other, "related_keep", "import_v1")
                stats["related_keep"] += 1
        dirty.extend(members)
    groups_db.mark_dirty(conn, dirty)
    return dirty


def _rollback(conn, extensions_before: dict[bytes, int]) -> None:
    """Undo a half-done import. Helpers commit as they go, so this is an explicit reversal:
    deleting the Folders cascades files, pairs, groups and dirty marks; extensions are
    restored to their previous rows and enabled flags."""
    with conn:
        conn.execute("DELETE FROM folder")
        conn.execute("DELETE FROM regroup_queue")
        for row in conn.execute("SELECT id FROM extension").fetchall():
            if bytes(row["id"]) not in extensions_before:
                conn.execute("DELETE FROM extension WHERE id = ?", (row["id"],))
        for ext_id, enabled in extensions_before.items():
            conn.execute("UPDATE extension SET enabled = ? WHERE id = ?", (enabled, ext_id))


def run_import_v1(ctx: IoContext) -> dict:
    mapping: dict[str, str] = ctx.params.get("folder_paths") or {}
    with (
        closing(ctx.open_metadata()) as conn,
        closing(_open_v1(ctx.params["db_path"])) as v1,
    ):
        v1_folders = _check_preconditions(conn, v1, mapping)
        stats = dict.fromkeys(STAT_KEYS, 0)
        extensions_before = {bytes(r["id"]): r["enabled"] for r in folders_db.list_extensions(conn)}
        try:
            folder_ids = _import_folders(conn, v1, v1_folders, mapping, stats)
            ext_ids, ext_names = _import_extensions(conn, v1, stats)
            file_ids = _import_files(conn, v1, ctx, folder_ids, ext_ids, ext_names, stats)
            dirty = _link_identical(conn, v1, file_ids, stats)
        except Exception:
            _rollback(conn, extensions_before)  # the profile was empty: make it empty again
            raise
        for s in v1.execute("SELECT File1Id, File2Id FROM SimilarFile"):
            stats["similar_skipped"] += 1
            if bytes(s["File1Id"]) not in file_ids or bytes(s["File2Id"]) not in file_ids:
                stats["similar_orphans"] += 1
        for fid in folder_ids.values():
            jobs_db.enqueue(
                conn,
                type="scan",
                kind="io",
                priority=PRIORITY_USER,
                params={"folder_id": id_to_str(fid), "priority": PRIORITY_USER},
            )
            stats["scan_jobs"] += 1
        if dirty and not jobs_db.has_active(conn, "regroup"):
            jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
        return stats

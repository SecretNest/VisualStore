"""Folder, IgnoredDirectory and Extension queries (design §5.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from visualstore.clock import now_iso
from visualstore.domain.paths import normalise_relative
from visualstore.ids import id_to_str, new_id

ROLES = ("archive", "inbox")
SCOPES = ("all", "other_directories", "other_folders")
MEDIA_TYPES = ("image", "animated", "video", "other")


class FolderError(Exception):
    pass


# -- folders -----------------------------------------------------------------------------


def _check_enums(role: str | None, scope: str | None) -> None:
    if role is not None and role not in ROLES:
        raise FolderError(f"role must be one of {ROLES}")
    if scope is not None and scope not in SCOPES:
        raise FolderError(f"compare_scope must be one of {SCOPES}")


def _normalise_path(path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        raise FolderError("Folder path must be an absolute path inside the container.")
    if not p.is_dir():
        raise FolderError(f"Folder path does not exist or is not a directory: {path}")
    return str(p.resolve()).rstrip("/") or "/"


def _check_no_nesting(conn: sqlite3.Connection, path: str) -> None:
    candidate = Path(path)
    for row in conn.execute("SELECT name, path FROM folder"):
        existing = Path(row["path"])
        if candidate == existing:
            raise FolderError(f"A Folder already uses this path: {row['name']}")
        if candidate.is_relative_to(existing) or existing.is_relative_to(candidate):
            raise FolderError(
                f"Folders must not nest: {path} overlaps Folder '{row['name']}' at {row['path']}. "
                "Use an ignored directory instead."
            )


def create_folder(
    conn: sqlite3.Connection,
    *,
    name: str,
    path: str,
    role: str,
    compare_scope: str,
    priority: int = 0,
    folder_id: bytes | None = None,
) -> bytes:
    _check_enums(role, compare_scope)
    if not name.strip():
        raise FolderError("Folder name is required.")
    normalised = _normalise_path(path)
    _check_no_nesting(conn, normalised)
    if conn.execute("SELECT 1 FROM folder WHERE name = ?", (name,)).fetchone():
        raise FolderError(f"A Folder named '{name}' already exists (names are case-insensitive).")
    folder_id = folder_id or new_id()
    with conn:
        conn.execute(
            "INSERT INTO folder (id, name, path, role, compare_scope, priority, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (folder_id, name.strip(), normalised, role, compare_scope, priority, now_iso()),
        )
    return folder_id


_UPDATABLE = {"name", "role", "compare_scope", "enabled", "priority"}


def update_folder(conn: sqlite3.Connection, folder_id: bytes, **fields) -> None:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise FolderError(f"These Folder fields cannot be changed: {sorted(unknown)}")
    _check_enums(fields.get("role"), fields.get("compare_scope"))
    if "name" in fields:
        clash = conn.execute(
            "SELECT 1 FROM folder WHERE name = ? AND id != ?", (fields["name"], folder_id)
        ).fetchone()
        if clash or not str(fields["name"]).strip():
            raise FolderError("Folder name must be unique and non-empty.")
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"UPDATE folder SET {assignments} WHERE id = ?", (*fields.values(), folder_id))


def delete_folder(conn: sqlite3.Connection, folder_id: bytes) -> None:
    with conn:
        conn.execute("DELETE FROM folder WHERE id = ?", (folder_id,))


def get_folder(conn: sqlite3.Connection, folder_id: bytes) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM folder WHERE id = ?", (folder_id,)).fetchone()


def list_folders(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM folder ORDER BY priority, name").fetchall()


def folder_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["id"] = id_to_str(row["id"])
    d["enabled"] = bool(row["enabled"])
    return d


# -- ignored directories -----------------------------------------------------------------


def add_ignored(
    conn: sqlite3.Connection, folder_id: bytes, directory: str, recursive: bool = True
) -> bytes:
    rel = normalise_relative(directory)
    if not rel:
        raise FolderError(
            "Ignoring the Folder root would ignore everything; disable the Folder instead."
        )
    if conn.execute(
        "SELECT 1 FROM ignored_directory WHERE folder_id = ? AND directory = ?", (folder_id, rel)
    ).fetchone():
        raise FolderError(f"'{rel}' is already ignored.")
    ignored_id = new_id()
    with conn:
        conn.execute(
            "INSERT INTO ignored_directory (id, folder_id, directory, recursive)"
            " VALUES (?, ?, ?, ?)",
            (ignored_id, folder_id, rel, 1 if recursive else 0),
        )
    return ignored_id


def remove_ignored(conn: sqlite3.Connection, ignored_id: bytes) -> None:
    with conn:
        conn.execute("DELETE FROM ignored_directory WHERE id = ?", (ignored_id,))


def list_ignored(conn: sqlite3.Connection, folder_id: bytes) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ignored_directory WHERE folder_id = ? ORDER BY directory", (folder_id,)
    ).fetchall()


# -- extensions --------------------------------------------------------------------------


def list_extensions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM extension ORDER BY media_type, extension").fetchall()


def extension_map(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        r["extension"].lower(): r for r in conn.execute("SELECT * FROM extension WHERE enabled = 1")
    }


def upsert_extension(
    conn: sqlite3.Connection,
    extension: str,
    media_type: str,
    enabled: bool = True,
    extension_id: bytes | None = None,
) -> bytes:
    if media_type not in MEDIA_TYPES:
        raise FolderError(f"media_type must be one of {MEDIA_TYPES}")
    ext = extension.strip().lstrip(".").lower()
    if not ext or "/" in ext or "." in ext:
        raise FolderError("Extension must be letters/digits without a dot.")
    row = conn.execute("SELECT id FROM extension WHERE extension = ?", (ext,)).fetchone()
    with conn:
        if row is None:
            ext_id = extension_id or new_id()
            conn.execute(
                "INSERT INTO extension (id, extension, media_type, enabled) VALUES (?, ?, ?, ?)",
                (ext_id, ext, media_type, 1 if enabled else 0),
            )
            return ext_id
        conn.execute(
            "UPDATE extension SET media_type = ?, enabled = ? WHERE id = ?",
            (media_type, 1 if enabled else 0, row["id"]),
        )
        return bytes(row["id"])


def set_extension_enabled(conn: sqlite3.Connection, extension_id: bytes, enabled: bool) -> None:
    with conn:
        conn.execute(
            "UPDATE extension SET enabled = ? WHERE id = ?", (1 if enabled else 0, extension_id)
        )

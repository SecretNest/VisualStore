"""Folder management API (design §8.1 page 2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.ids import id_to_str
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(prefix="/api/p/{name}", tags=["folders"], dependencies=[Depends(require_auth)])


class CreateFolder(BaseModel):
    name: str
    path: str
    role: str
    compare_scope: str
    priority: int = 0


class UpdateFolder(BaseModel, extra="forbid"):
    name: str | None = None
    role: str | None = None
    compare_scope: str | None = None
    enabled: bool | None = None
    priority: int | None = None


class AddIgnored(BaseModel):
    directory: str
    recursive: bool = True


class UpsertExtension(BaseModel):
    extension: str
    media_type: str
    enabled: bool = True


class ToggleExtension(BaseModel):
    enabled: bool


def _folder_out(conn, row) -> dict:
    d = folders_db.folder_to_dict(row)
    d["ignored"] = [
        {
            "id": id_to_str(bytes(i["id"])),
            "directory": i["directory"],
            "recursive": bool(i["recursive"]),
        }
        for i in folders_db.list_ignored(conn, bytes(row["id"]))
    ]
    return d


def _folder_or_404(conn, folder_id: bytes):
    row = folders_db.get_folder(conn, folder_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such Folder.")
    return row


@router.get("/folders")
async def list_folders(request: Request, name: str = Depends(get_profile)) -> list[dict]:
    conn = get_state(request).registry.conn(name)
    return [_folder_out(conn, r) for r in folders_db.list_folders(conn)]


@router.post("/folders", status_code=201)
async def create_folder(
    body: CreateFolder, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = folders_db.create_folder(
        conn,
        name=body.name,
        path=body.path,
        role=body.role,
        compare_scope=body.compare_scope,
        priority=body.priority,
    )
    return _folder_out(conn, folders_db.get_folder(conn, fid))


@router.patch("/folders/{folder_id}")
async def update_folder(
    folder_id: str, body: UpdateFolder, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(folder_id)
    _folder_or_404(conn, fid)
    folders_db.update_folder(conn, fid, **body.model_dump(exclude_none=True))
    return _folder_out(conn, folders_db.get_folder(conn, fid))


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(folder_id)
    _folder_or_404(conn, fid)
    folders_db.delete_folder(conn, fid)
    return {"ok": True}


@router.post("/folders/{folder_id}/ignored", status_code=201)
async def add_ignored(
    folder_id: str, body: AddIgnored, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(folder_id)
    _folder_or_404(conn, fid)
    folders_db.add_ignored(conn, fid, body.directory, body.recursive)
    return _folder_out(conn, folders_db.get_folder(conn, fid))


@router.delete("/folders/{folder_id}/ignored/{ignored_id}")
async def remove_ignored(
    folder_id: str, ignored_id: str, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(folder_id)
    _folder_or_404(conn, fid)
    folders_db.remove_ignored(conn, parse_id(ignored_id))
    return _folder_out(conn, folders_db.get_folder(conn, fid))


@router.post("/folders/{folder_id}/scan", status_code=201)
async def trigger_scan(folder_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(folder_id)
    _folder_or_404(conn, fid)
    if jobs_db.has_active_with_param(conn, "scan", "folder_id", folder_id):
        raise HTTPException(
            status_code=409, detail="A scan of this Folder is already queued or running."
        )
    job_id = jobs_db.enqueue(
        conn,
        type="scan",
        kind="io",
        priority=PRIORITY_USER,
        params={"folder_id": folder_id, "priority": PRIORITY_USER},
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


def _extensions(conn) -> list[dict]:
    return [
        {
            "id": id_to_str(bytes(r["id"])),
            "extension": r["extension"],
            "media_type": r["media_type"],
            "enabled": bool(r["enabled"]),
        }
        for r in folders_db.list_extensions(conn)
    ]


@router.get("/extensions")
async def list_extensions(request: Request, name: str = Depends(get_profile)) -> list[dict]:
    return _extensions(get_state(request).registry.conn(name))


@router.put("/extensions")
async def upsert_extension(
    body: UpsertExtension, request: Request, name: str = Depends(get_profile)
) -> list[dict]:
    conn = get_state(request).registry.conn(name)
    folders_db.upsert_extension(conn, body.extension, body.media_type, body.enabled)
    return _extensions(conn)


@router.patch("/extensions/{extension_id}")
async def toggle_extension(
    extension_id: str, body: ToggleExtension, request: Request, name: str = Depends(get_profile)
) -> list[dict]:
    conn = get_state(request).registry.conn(name)
    folders_db.set_extension_enabled(conn, parse_id(extension_id), body.enabled)
    return _extensions(conn)

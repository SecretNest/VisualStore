"""File detail and original-content streaming (design §8.3 "open original")."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.db import pairs as pairs_db
from visualstore.ids import id_to_str

router = APIRouter(
    prefix="/api/p/{name}/files", tags=["files"], dependencies=[Depends(require_auth)]
)


def _file_and_folder(conn, file_id: bytes):
    row = files_db.get_file(conn, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such file.")
    return row, folders_db.get_folder(conn, bytes(row["folder_id"]))


@router.get("/{file_id}")
async def file_detail(file_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(file_id)
    row, folder = _file_and_folder(conn, fid)
    d = files_db.file_to_dict(row, folder["path"])
    g = groups_db.group_of_file(conn, fid)
    d["group_id"] = id_to_str(bytes(g["id"])) if g else None
    d["pairs"] = [pairs_db.pair_to_dict(p) for p in pairs_db.pairs_of_file(conn, fid)]
    return d


@router.get("/{file_id}/content")
async def file_content(
    file_id: str, request: Request, name: str = Depends(get_profile)
) -> FileResponse:
    conn = get_state(request).registry.conn(name)
    row, folder = _file_and_folder(conn, parse_id(file_id))
    path = Path(folder["path"]) / files_db.relative_path(row["directory"], row["name"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="The file is not on disk.")
    media_type = mimetypes.guess_type(row["name"])[0] or "application/octet-stream"
    return FileResponse(
        path, media_type=media_type, filename=row["name"], content_disposition_type="inline"
    )


@router.get("/{file_id}/thumbnail")
async def file_thumbnail(
    file_id: str, request: Request, name: str = Depends(get_profile)
) -> FileResponse:
    parse_id(file_id)
    path = get_state(request).registry.paths(name).thumbs / file_id[:2] / f"{file_id}.webp"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No thumbnail yet.")
    return FileResponse(path, media_type="image/webp")

"""File detail and original-content streaming (design §8.3 "open original")."""

from __future__ import annotations

import asyncio
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
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.media.errors import NotMedia, Unreadable

PREVIEW_PX = 1024

router = APIRouter(
    prefix="/api/p/{name}/files", tags=["files"], dependencies=[Depends(require_auth)]
)


def _media_type(conn, extension_id: bytes) -> str:
    for r in folders_db.list_extensions(conn):
        if bytes(r["id"]) == extension_id:
            return r["media_type"]
    return "other"


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


@router.get("/{file_id}/preview")
async def file_preview(
    file_id: str, request: Request, name: str = Depends(get_profile)
) -> FileResponse:
    state = get_state(request)
    conn = state.registry.conn(name)
    fid = parse_id(file_id)
    row, folder = _file_and_folder(conn, fid)
    cache = state.registry.paths(name).previews / file_id[:2] / f"{file_id}.webp"
    if cache.is_file():
        return FileResponse(cache, media_type="image/webp")
    path = Path(folder["path"]) / files_db.relative_path(row["directory"], row["name"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="The file is not on disk.")
    if _media_type(conn, bytes(row["extension_id"])) != "image":
        raise HTTPException(status_code=404, detail="No preview for this media type yet.")
    from visualstore.media.decode import preview_webp  # pyvips: not imported server-wide

    max_pixels = int(settings_db.get(conn, "media.max_pixels"))
    try:
        data = await asyncio.to_thread(preview_webp, str(path), PREVIEW_PX, max_pixels)
    except (NotMedia, Unreadable) as exc:
        raise HTTPException(status_code=404, detail=f"Cannot decode the file: {exc}") from None
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(cache)
    return FileResponse(cache, media_type="image/webp")


@router.get("/{file_id}/thumbnail")
async def file_thumbnail(
    file_id: str, request: Request, name: str = Depends(get_profile)
) -> FileResponse:
    parse_id(file_id)
    path = get_state(request).registry.paths(name).thumbs / file_id[:2] / f"{file_id}.webp"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No thumbnail yet.")
    return FileResponse(path, media_type="image/webp")

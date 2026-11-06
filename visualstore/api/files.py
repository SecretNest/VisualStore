"""File detail and original-content streaming (design §8.3 "open original")."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.db import pairs as pairs_db
from visualstore.db import settings as settings_db
from visualstore.domain.paths import TRASH_DIR, normalise_relative
from visualstore.ids import id_to_str
from visualstore.jobs.types import PRIORITY_USER
from visualstore.media.errors import NotMedia, Unreadable

PREVIEW_PX = 1024
_RENDER_SLOTS = asyncio.Semaphore(2)  # bounds the decoded pixels held by the server at once
_RENDERING: dict[Path, asyncio.Lock] = {}  # one render per file; later requests reuse the cache

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
    d["folder_name"] = folder["name"]
    d["media_type"] = _media_type(conn, bytes(row["extension_id"]))
    g = groups_db.group_of_file(conn, fid)
    d["group_id"] = id_to_str(bytes(g["id"])) if g else None
    rows = pairs_db.pairs_of_file(conn, fid)
    d["pairs"] = [pairs_db.pair_to_dict(p) for p in rows]
    settings = settings_db.all(conn)
    low = float(settings["grouping.group_distance"])
    high = float(settings["similarity.candidate_distance"])
    candidates = []
    for p in rows:
        if p["source"] != "vector" or p["judgment"] == "unrelated" or p["distance"] is None:
            continue
        if not (low < p["distance"] <= high):
            continue
        other = bytes(p["file_b"]) if bytes(p["file_a"]) == fid else bytes(p["file_a"])
        orow = files_db.get_file(conn, other)
        if orow is None:
            continue
        ofolder = folders_db.get_folder(conn, bytes(orow["folder_id"]))
        og = groups_db.group_of_file(conn, other)
        candidates.append(
            {
                "file_id": id_to_str(other),
                "distance": p["distance"],
                "judgment": p["judgment"],
                "relative_path": files_db.relative_path(orow["directory"], orow["name"]),
                "folder_name": ofolder["name"],
                "group_id": id_to_str(bytes(og["id"])) if og else None,
            }
        )
    d["candidates"] = sorted(candidates, key=lambda c: c["distance"])
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
    lock = _RENDERING.setdefault(cache, asyncio.Lock())
    try:
        async with lock:
            if cache.is_file():  # rendered by the request that held the lock before us
                return FileResponse(cache, media_type="image/webp")
            async with _RENDER_SLOTS:
                try:
                    data = await asyncio.to_thread(preview_webp, str(path), PREVIEW_PX, max_pixels)
                except (NotMedia, Unreadable) as exc:
                    raise HTTPException(
                        status_code=404, detail=f"Cannot decode the file: {exc}"
                    ) from None
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(cache)
    finally:
        if not lock.locked() and _RENDERING.get(cache) is lock:
            _RENDERING.pop(cache, None)
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


class Move(BaseModel):
    folder_id: str
    directory: str = ""


class Rename(BaseModel):
    name: str


def _check_name(name: str) -> None:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="The name must be a single path component.")
    if name == TRASH_DIR:
        raise HTTPException(status_code=400, detail="That name is reserved for the trash.")


def _check_directory(directory: str) -> str:
    try:
        directory = normalise_relative(directory)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if TRASH_DIR in directory.split("/"):
        raise HTTPException(
            status_code=400, detail="Files cannot be moved into the trash directory."
        )
    return directory


def _check_free(conn, folder, directory: str, name: str, file_id: bytes) -> None:
    occupied = files_db.find_file(conn, bytes(folder["id"]), directory, name)
    target = Path(folder["path"]) / files_db.relative_path(directory, name)
    if (occupied is not None and bytes(occupied["id"]) != file_id) or target.exists():
        raise HTTPException(status_code=409, detail="A file with that path already exists.")


def _queue(conn, params: dict) -> dict:
    job_id = jobs_db.enqueue(
        conn, type="apply_action", kind="io", priority=PRIORITY_USER, params=params
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


@router.post("/{file_id}/move", status_code=201)
async def move_file_endpoint(
    file_id: str, body: Move, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(file_id)
    row, _ = _file_and_folder(conn, fid)
    directory = _check_directory(body.directory)
    target = folders_db.get_folder(conn, parse_id(body.folder_id))
    if target is None:
        raise HTTPException(status_code=404, detail="No such Folder.")
    _check_free(conn, target, directory, row["name"], fid)
    return _queue(
        conn, {"move": [{"file_id": file_id, "folder_id": body.folder_id, "directory": directory}]}
    )


@router.post("/{file_id}/rename", status_code=201)
async def rename_file_endpoint(
    file_id: str, body: Rename, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(file_id)
    row, folder = _file_and_folder(conn, fid)
    _check_name(body.name)
    _check_free(conn, folder, row["directory"], body.name, fid)
    return _queue(conn, {"rename": [{"file_id": file_id, "name": body.name}]})

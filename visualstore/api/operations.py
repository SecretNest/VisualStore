"""Long-running operations started from the UI: archive move and v1 import (design §8.5, §10)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.domain.paths import normalise_relative
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(
    prefix="/api/p/{name}", tags=["operations"], dependencies=[Depends(require_auth)]
)


class ArchiveMove(BaseModel):
    source_folder_id: str
    target_folder_id: str
    directories: list[str] = []


class ImportV1(BaseModel):
    db_path: str
    folder_paths: dict[str, str]


@router.post("/archive-move", status_code=201)
async def archive_move(
    body: ArchiveMove, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    for text in (body.source_folder_id, body.target_folder_id):
        if folders_db.get_folder(conn, parse_id(text)) is None:
            raise HTTPException(status_code=404, detail="No such Folder.")
    try:
        for directory in body.directories:
            normalise_relative(directory)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    job_id = jobs_db.enqueue(
        conn, type="archive_move", kind="io", priority=PRIORITY_USER, params=body.model_dump()
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


@router.post("/import-v1", status_code=201)
async def import_v1(body: ImportV1, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    if folders_db.list_folders(conn):
        raise HTTPException(
            status_code=400, detail="Import needs an empty profile (no Folders yet)."
        )
    job_id = jobs_db.enqueue(
        conn, type="import_v1", kind="io", priority=PRIORITY_USER, params=body.model_dump()
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))

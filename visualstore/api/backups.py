"""Backup listing and manual trigger (design §9.1)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.db import jobs as jobs_db
from visualstore.jobs.io.backup import TARGETS, list_backups
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(
    prefix="/api/p/{name}/backups", tags=["backups"], dependencies=[Depends(require_auth)]
)


class TriggerBackup(BaseModel):
    targets: list[str] = list(TARGETS)


@router.get("")
async def get_backups(request: Request, name: str = Depends(get_profile)) -> list[dict]:
    return list_backups(get_state(request).config.backup_dir, name)


@router.post("", status_code=201)
async def trigger_backup(
    body: TriggerBackup, request: Request, name: str = Depends(get_profile)
) -> dict:
    unknown = [t for t in body.targets if t not in TARGETS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown backup target(s): {unknown}.")
    conn = get_state(request).registry.conn(name)
    job_id = jobs_db.enqueue(
        conn, type="backup", kind="io", params={"targets": body.targets}, priority=PRIORITY_USER
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))

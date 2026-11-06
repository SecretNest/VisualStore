"""Job queue API (design §6, §8.1 Jobs and settings page)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.db import jobs as jobs_db
from visualstore.jobs.types import KIND_OF, PRIORITY_USER

router = APIRouter(prefix="/api/p/{name}/jobs", tags=["jobs"], dependencies=[Depends(require_auth)])


class CreateJob(BaseModel):
    type: str
    params: dict = {}
    priority: int = PRIORITY_USER


def _job_or_404(conn, job_id: str):
    try:
        raw = uuid.UUID(job_id).bytes
    except ValueError:
        raise HTTPException(status_code=404, detail="No such job.") from None
    row = jobs_db.get(conn, raw)
    if row is None:
        raise HTTPException(status_code=404, detail="No such job.")
    return raw, row


@router.get("")
def list_jobs(
    request: Request,
    name: str = Depends(get_profile),
    state: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conn = get_state(request).registry.conn(name)
    return [jobs_db.to_dict(r) for r in jobs_db.list_jobs(conn, state=state, limit=limit)]


@router.post("", status_code=201)
def create_job(body: CreateJob, request: Request, name: str = Depends(get_profile)) -> dict:
    kind = KIND_OF.get(body.type)
    if kind is None:
        raise HTTPException(status_code=400, detail=f"Unknown job type '{body.type}'.")
    conn = get_state(request).registry.conn(name)
    job_id = jobs_db.enqueue(
        conn, type=body.type, kind=kind, params=body.params, priority=body.priority
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


@router.get("/{job_id}")
def get_job(job_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    _, row = _job_or_404(conn, job_id)
    return jobs_db.to_dict(row)


@router.post("/{job_id}/retry")
def retry_job(job_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    raw, _ = _job_or_404(conn, job_id)
    if not jobs_db.retry(conn, raw):
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried.")
    return jobs_db.to_dict(jobs_db.get(conn, raw))


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    raw, _ = _job_or_404(conn, job_id)
    if not jobs_db.cancel(conn, raw):
        raise HTTPException(status_code=409, detail="Only queued jobs can be cancelled.")
    return jobs_db.to_dict(jobs_db.get(conn, raw))
